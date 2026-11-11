# Architecture

This document explains **why** the system is shaped the way it is. The wire-level
rules live in [`CONTRACTS.md`](CONTRACTS.md).

## 1. Components and responsibility boundaries

| component | owns | never does |
|---|---|---|
| Flutter app | all rendering, audio device I/O, navigation, local board document, animations | server state, prompt logic, direct Qwen access |
| FastAPI backend | auth/ownership, lesson lifecycle, Qwen session custody, tool validation, persistence, notes pipeline | drawing decisions, UI layout |
| Qwen Realtime | natural teaching conversation, when/what to write, lesson tool intent | persistence, direct DB/widget access |
| LangGraph | authoritative pedagogical state, concept progression, long-term memory | speaking, board geometry |

The teacher model is intentionally *not* the source of truth. All model output
that changes state passes through a validated, persisted boundary. This keeps
the classroom recoverable, auditable and safe.

## 2. Lesson start sequence

```
Flutter            FastAPI                 LangGraph              Qwen
  │ POST /lessons    │                        │                    │
  ├─────────────────►│ create lesson row      │                    │
  │                  ├── retrieve memory ────►│                    │
  │                  ├── plan + init state ──►│ checkpoint(thread=lesson_id)
  │◄── LessonDetail ─┤                        │                    │
  │ WS connect       │                        │                    │
  ├─────────────────►│ auth + ownership       │                    │
  │                  ├── open session ────────┼───────────────────►│ session.update
  │                  │                        │   (instructions + 28 tools)
  │◄─ session.ready ─┤ (board snapshot, transcript cursor)           │
  │                  │◄──────────── audio + transcript + tool calls ┘
  │◄─ audio.delta ───┤◄── tool call ─ registry.validate ─ BoardService
  │◄─ board.event ───┤      persist(event, sequence) + publish
  │                  ├── tool result ───────────────────────────────►│
  │                  │◄──────────────── continuation ────────────────┘
```

Key properties:
* The WS is opened **after** the lesson row and LangGraph state exist, so a
  mid-connect failure leaves a resumable lesson rather than an orphan session.
* The board snapshot is sent in `session.ready`, then events; the client never
  has to fetch to render the first chalk stroke.
* Tool results are returned to Qwen so it can continue the same spoken response;
  the board update does not delay the audio pipe.

## 3. Audio path

Client captures 16 kHz mono PCM16 and sends raw binary frames over the same WS.
Backend base64-appends them to the Qwen session (`input_audio_buffer.append`).
Tutor audio arrives as 24 kHz PCM16 deltas, is relayed as `audio.delta` JSON
(base64) and played by the Flutter PCM sink. Transcription comes from Qwen's
audio-transcript events (tutor) and from student-input transcript events where
available; both are persisted with monotonic per-lesson sequence numbers.

Nothing in the audio path waits for painting. The blackboard animation scheduler
is driven by the Flutter frame pipeline and owns its own queue.

## 4. Board model

`BoardDocument` is the durable state: `elements {id → element}` plus
`revision`/`last_sequence`. Every mutation is an append-only
`lesson_board_events` row with a stable `event_id` and per-session `sequence`
allocated inside a transaction. Snapshots (structured JSON, no images) are
written every 40 events (configurable) or on pause/end, keeping the last 5.

Reconstruction: latest snapshot (`document`, `last_sequence`) + events with
`sequence > last_sequence`. Flutter's reducer is pure and idempotent, so replay,
reconnect and duplicate delivery converge to the same document.

Positions are normalized `[0,1]` so one document renders correctly on a phone,
tablet or foldable. `BoardTransform` is the only place normalized coordinates
become pixels.

## 5. Teaching loop and memory

LangGraph runs `thread_id = lesson_id`:

```
retrieve_memory → (plan_lesson | load_plan) → teach_step
      ↑                                           │
      │                                     record_answer
      │                                           │
      └── evaluate_and_advance ◄── assessment evidence
                    │
             persist_observations → finalize_lesson
```

* **Active state** (concept, plan, completed/pending, remaining time,
  understanding, recommended action) lives in the checkpointed graph state.
* **Long-term memory** lives in the LangGraph Store, mirrored into queryable
  relational tables (`student_concept_mastery`,
  `student_learning_observations`). Mastery only moves on evidence: a checked
  answer, an explicit revisit, or an assessment — never "the student heard it".
* **Session archive** (transcript, board events, notes, assessments) is
  independent of the graph so it survives state pruning and is directly
  queryable by the Notes experience.
* On start, memory is compressed into a bounded prompt-safe summary (previous
  difficulties for the requested subject, mastered concepts, preferences) and
  injected into the teacher instructions. On finish, meaningful observations and
  mastery updates are written, then the checkpoint is finalised.

## 6. Reliability model

| failure | behaviour |
|---|---|
| WS drop | client reconnects with backoff; `session.ready` → `board.backfill(after_sequence)`; lesson marked `paused`, checkpoint intact |
| backend restart | lesson rows + board events + LangGraph Postgres checkpoints survive; a new WS resumes the same thread |
| duplicate event | unique `event_id`/`(lesson, sequence)` constraints; client reducer ignores `sequence <= applied` |
| out-of-order event | client buffers gaps and requests backfill; server always emits strictly increasing sequence |
| invalid tool call | rejected with `{ok:false, ...}`; zero DB mutations; Qwen retries or continues |
| Qwen outage | `qwen_unavailable` error with `retryable`; offline fake transport available for dev |
| audio glitch | playback queue independent of board/transcript; UI shows teacher state, never blocks |
| two clients same lesson | newest socket takes the lease; old socket is closed `4001` after `session.interrupted` |

## 7. Ownership and deletion

`lessons.student_id` is the ownership root. Every read/write path resolves the
lesson and compares `lesson.student_id` to the JWT subject — IDs from the client
are never trusted. `DELETE /api/v1/me/data` cascades lessons → transcripts →
board events/snapshots → notes/assessments and calls the graph service to delete
LangGraph thread checkpoints and Store namespaces.

## 8. Deliberate non-goals

* No LangChain (`langgraph` used directly).
* No WebView/HTML/canvas board, no screenshots or image-based board.
* No duplicate lesson-state copies: relational rows are indexes/archives, the
  LangGraph checkpoint is authoritative for teaching state, and board
  events/snapshots are authoritative for the board.
* No automatic structured notes without explicit student consent.
