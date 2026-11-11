# AI Personal Classroom — Implementation Contracts (v1, authoritative)

This document is the single source of truth for every boundary between the three
implementations: **Python backend**, **Qwen Realtime**, and the **Flutter app**.
If code and this document disagree, fix the code (or raise the discrepancy to the
Lead and update this document in the same commit). Do not invent parallel
schemas.

Repository root: `ai-classroom/`

```
backend/   Python 3.12 + FastAPI + LangGraph          (owner: backend-core / backend-ai)
app/       Flutter 3.x (Android + iOS)                (owner: flutter-app / flutter-board)
docs/      architecture + this contract + event schema
docker-compose.yml, .env.example, README.md           (owner: lead)
```

Module ownership (write scopes) is listed in the team task board. Never edit a
file outside your own scope; request changes through the Lead.

---

## 1. Conventions

* Timestamps: RFC 3339 UTC with milliseconds, `2026-11-11T05:50:00.123Z`.
* All ids are opaque strings (UUID4 for DB rows, ULID-ish for events/elements).
* Normalized board coordinates: `x`, `y` in `[0,1]`, origin top-left, `y` grows
  downward. Sizes/widths also normalized. Flutter multiplies by the board
  content rect. **Never** send pixels from the backend.
* JSON keys are `snake_case` everywhere (REST, WS, board events, Dart models use
  the same wire names with `@JsonKey`-free manual parsing).
* Colors: hex strings `#RRGGBB`. Fixed chalk palette (Flutter owns it; backend
  sends palette role names or explicit colors):
  * `primary` `#F2F0E8` — default chalk
  * `secondary` `#A8A79E` — muted annotations
  * `blue` `#9CC9E3` — highlights / graph curves
  * `yellow` `#E8D58C` — emphasis
  * `red` `#E39C9C` — mistakes / corrections
  * `green` `#A9D3A0` — success / correct answers
  Backend sends `color` as a role name from that list (flutter resolves) **or**
  an explicit `#RRGGBB`; both accepted.
* Errors use a stable envelope (see §7).
* Auth: `Authorization: Bearer <jwt>` for REST. For WebSocket, `?token=<jwt>`
  query param is allowed (browsers cannot set headers).
* Ownership: every lesson/transcript/note/event row is reachable only through
  the authenticated `student_id`. The client never supplies a student id.

---

## 2. Board event envelope

Every board operation is exactly one persistent event. The AI never touches
widgets; Qwen → tool call → registry validation → board service → persisted
event → WS publish → Flutter renderer.

```json
{
  "event_id": "evt_01JABCDEF",
  "session_id": "6f1c8a9e-6d2f-4c55-9d0e-1f2a3b4c5d6e",
  "sequence": 7,
  "type": "write_text",
  "element_id": "el_01JABCDEG",
  "payload": { "text": "Introduction to Differentiation", "style": "heading", "position": { "x": 0.08, "y": 0.08 }, "color": "primary" },
  "created_at": "2026-11-11T05:50:00.123Z",
  "board_revision": 7,
  "depends_on": [],
  "source": "qwen_tool",
  "animate": true
}
```

| field | type | notes |
|---|---|---|
| `event_id` | string | globally unique, generated backend-side, idempotency key |
| `session_id` | string | lesson id (one lesson == one session) |
| `sequence` | int | per-session, strictly increasing starting at 1, allocated in a DB transaction (`SELECT ... FOR UPDATE` on lesson row or unique constraint retry) |
| `type` | enum | one of the event types in §3 |
| `element_id` | string \| null | target/created element; `null` only for `clear`, `erase(scope=all)` |
| `payload` | object | per-type schema in §3 |
| `created_at` | string | |
| `board_revision` | int | monotonic revision counter of the board document (== sequence for a single-writer board) |
| `depends_on` | string[] | event ids that must render first (ordering, not data dependency) |
| `source` | enum | `qwen_tool`, `system`, `student` |
| `animate` | bool | false for restoration/replay (instant apply) |

Rules:
* Flutter applies events in `sequence` order. If `sequence <= last_applied` →
  ignore (idempotent). If a gap exists → buffer and request
  `board.request_missing` / REST backfill; never apply out of order.
* Element ids are stable for the life of the session; updates always reference an
  existing element. Unknown target ⇒ event rejected server-side, and client-side
  becomes a no-op warning, never a crash.
* Board levels: element `level` is derived from type, not sent redundantly:
  1 background, 2 text/handwriting, 3 equation, 4 drawing/diagram, 5 graph,
  6 highlight/annotation.
* Animations must never block audio; client schedules them (see §6).
* Deletion is soft in the document (`removed_at_sequence`) so replay is stable.

---

## 3. Board event types and payloads

`position` = `{ "x": float, "y": float }` normalized, meaning the **top-left of
the element's bounding box** unless stated otherwise. `size` =
`{ "w": float, "h": float }` normalized; if omitted the client measures text
and grows downward (headings/body) or derives from points (paths/graphs).

### 3.1 Text (layer 2)

| type | payload |
|---|---|
| `write_text` | `{ text, style, position, size?, color?, align?, font_scale?, max_width? }` |
| `write_heading` | `{ text, position, level?: 1\|2, color?, underline?: bool }` |
| `write_definition` | `{ term, text, position, color?, term_style?: "chalk_blue" }` |
| `write_bullet_list` | `{ items: string[], position, style?: "bullet"\|"numbered", color?, marker_color? }` |
| `update_text` | `{ text?, style?, color?, position? }` — full replacement of the text |

`style` ∈ `body`, `heading`, `subheading`, `definition`, `label`, `caption`,
`note`, `code` (monospace / code-block presentation for programming lessons).
Chalk-style font: bundled `PatrickHand`/`Caveat`-like asset
(flutter-board may substitute `google_fonts`-free bundled asset; fallback UI
font). Text writes animate left→right as if chalk is drawing; whole-element
fade is not acceptable.

### 3.2 Equations (layer 3)

| type | payload |
|---|---|
| `write_equation` | `{ latex, position, size?, color?, font_scale?, caption? }` |
| `update_equation` | `{ latex, color?, font_scale? }` |
| `highlight_equation` | `{ color?, style?: "box"\|"underline" }` — element must be equation |

LaTeX is the interchange format, rendered locally with `flutter_math_fork`.
Progressive reveal is by meaningful sections (client tokenizes top-level LaTeX
groups) — no screenshots.

### 3.3 Drawings (layer 4)

| type | payload |
|---|---|
| `draw_line` | `{ start, end, color?, stroke_width?, dash?: float[], arrowhead?: bool }` |
| `draw_shape` | `{ shape, position, size, color?, fill_color?, stroke_width?, corners?: float, points?: {x,y}[] }` |
| `draw_path` | `{ points: {x,y}[], closed?: bool, smooth?: bool, color?, stroke_width?, fill_color? }` |
| `draw_arrow` | `{ start, end, curve?: float (-1..1), label?: string, color?, stroke_width? }` |
| `draw_diagram` | `{ kind, nodes: [{id, label?, shape?, position, size?, color?}], edges: [{from, to, label?, style?, arrow?, curve?}], title?, coordinate_note? }` |

`draw_shape.shape` ∈ `rect`, `circle`, `ellipse`, `triangle`, `polygon`,
`rhombus`, `cylinder`. `draw_diagram.kind` ∈ `flowchart`, `circuit`, `force`,
`biology`, `concept_map`, `geometry`, `generic`. Nodes with a `shape` and edges
always exist; labels are rendered as chalk text and appear last.

### 3.4 Graphs (layer 5)

| type | payload |
|---|---|
| `create_coordinate_system` | `{ position, size, x_range:[min,max], y_range:[min,max], grid?: bool, x_label?, y_label?, x_step?, y_step?, color? }` |
| `plot_function` | `{ coordinate_system_id, expression, x_domain?: [min,max], samples: [[x,y],...], color?, stroke_width?, style?: "solid"\|"dashed", label? }` |
| `plot_points` | `{ coordinate_system_id, points: [{x, y, label?, color?, radius?}], color? }` |
| `add_graph_label` | `{ coordinate_system_id, at: {x, y}, text, color?, with_leader?: bool }` |

Plotting is data-first: **the backend samples the function** with a safe,
sandboxed expression evaluator and sends `samples` (data coordinates, ordered by
x). Flutter maps data→screen using the coordinate system and progressively
unveils the polyline. If `samples` is absent (shouldn't happen), the client
draws nothing and records a non-fatal warning.

### 3.5 Board management / annotations (layer 6)

| type | payload |
|---|---|
| `erase` | `{ scope: "all"\|"region", region?: {x,y,w,h} }` — element_id null unless region |
| `erase_element` | `{}` — element_id required |
| `highlight` | `{ target_ids: string[], style, color?, label? }` style ∈ `underline`, `circle`, `box`, `strike`, `arrow` |
| `clear` | `{}` — wipes document, element_id null |
| `move_element` | `{ position?, delta? }` |

`highlight` computes its geometry from the target elements' current bounds in
Flutter. Backend stores only the annotation record referencing `target_ids`; a
minimum of one existing target is enforced.

### 3.6 Teaching events (no board mutation)

These are emitted alongside tool calls so the UI can react; they are **not**
board events and are not persisted by the board service (`lesson_events` table
or `lessons` metadata instead — see DB ownership):

* `lesson.state_changed` — teaching state after any lesson tool
* `lesson.question_asked` `{ question, concept, expected_answer? , question_id }`
* `lesson.answer_evaluated` `{ question_id, correct, feedback, mastery_delta }`
* `lesson.progress_updated` `{ current_concept, completed_concepts, progress }`
* `lesson.finished` `{ summary, duration_seconds, assessment }`

Delivered to Flutter as WS `lesson.state` messages (see §6), never as board
events.

---

## 4. Tool registry (Qwen function calling)

Qwen receives OpenAI-style function tools
(`{"type":"function","function":{"name","description","parameters"}}`).
Tools are grouped; every tool maps to exactly one board event type or one
lesson action. **The registry exposes exactly 22 board tools + 6 lesson tools =
28 tools (the PRD §7 list, verbatim), and nothing else.** JSON Schema must be closed (`additionalProperties: false`),
required fields explicit, and every numeric field bounded (positions 0..1,
stroke widths 0.5..4 logical px, text length ≤ 2000, list lengths bounded).

### Board tools → event type

| tool | event type |
|---|---|
| `board_write_text` | `write_text` |
| `board_write_heading` | `write_heading` |
| `board_write_definition` | `write_definition` |
| `board_write_bullet_list` | `write_bullet_list` |
| `board_update_text` | `update_text` |
| `board_write_equation` | `write_equation` |
| `board_update_equation` | `update_equation` |
| `board_highlight_equation` | `highlight_equation` |
| `board_draw_line` | `draw_line` |
| `board_draw_shape` | `draw_shape` |
| `board_draw_path` | `draw_path` |
| `board_draw_arrow` | `draw_arrow` |
| `board_draw_diagram` | `draw_diagram` |
| `board_create_coordinate_system` | `create_coordinate_system` |
| `board_plot_function` | `plot_function` |
| `board_plot_points` | `plot_points` |
| `board_add_graph_label` | `add_graph_label` |
| `board_erase` | `erase` |
| `board_erase_element` | `erase_element` |
| `board_highlight` | `highlight` |
| `board_clear` | `clear` |
| `board_move_element` | `move_element` |

### Lesson tools (LangGraph actions via backend orchestrator)

`lesson_next_concept`, `lesson_update_progress`, `lesson_ask_question`,
`lesson_check_answer`, `lesson_revisit_concept`, `lesson_finish`.

Tool-call result contract returned to Qwen (string content = JSON):

```json
{ "ok": true, "event_id": "evt_…", "sequence": 7, "element_id": "el_…" }
{ "ok": false, "error": "invalid_arguments", "message": "position.x must be within [0,1]", "retryable": true }
{ "ok": true, "lesson_state": { "current_concept": "…", "progress": 0.4 } }
```

Never include secrets, transcripts, or raw DB rows in tool results. A rejected
call must not mutate board or lesson state. The backend logs rejects at WARNING
and returns `{ok:false}` rather than letting Qwen continue undetected.

> The registry must expose **only** these functions. No eval, no SQL, no
> filesystem, no shell, no arbitrary HTTP.

---

## 5. REST API

Base path `/api/v1`. JSON in/out, `snake_case`. Errors per §7.

Auth:
| method | path | body | response |
|---|---|---|---|
| POST | `/auth/register` | `{email, password, display_name}` | `{access_token, token_type:"bearer", student: Student}` |
| POST | `/auth/login` | `{email, password}` | same |
| GET | `/me` | — | `Student` |
| PATCH | `/me` | `{display_name?, default_level?, default_duration_minutes?, preferences?}` | `Student` |
| DELETE | `/me/data` | `{confirm: true}` | `{deleted: true}` (cascade: lessons, transcripts, board, notes, memory) |

Lessons:
| method | path | body/query | response |
|---|---|---|---|
| POST | `/lessons` | `{topic, learning_level, duration_minutes, subject?}` | `LessonDetail` (creates DB lesson + LangGraph state) |
| GET | `/lessons` | `?status=&limit=&offset=` | `{items: [LessonSummary]}` |
| GET | `/lessons/{id}` | — | `LessonDetail` |
| POST | `/lessons/{id}/resume` | — | `{lesson, board: BoardSnapshotEnvelope, transcript_cursor, needs_notes_prompt}` |
| POST | `/lessons/{id}/end` | `{reason?}` | `LessonDetail` |
| GET | `/lessons/{id}/board` | — | `BoardSnapshotEnvelope` |
| GET | `/lessons/{id}/board/events` | `?after_sequence=0` | `{events: [BoardEvent]}` |
| GET | `/lessons/{id}/transcript` | — | `{entries: [TranscriptEntry]}` |
| GET | `/lessons/{id}/notes` | — | `{notes: NoteSummary[]}` |
| POST | `/lessons/{id}/notes/generate` | `{consent: true}` | `{note: NoteDetail, status: "pending"\|"ready"}` (consent false ⇒ 422) |
| GET | `/notes` | `?limit=` | `{items: [NoteSummary]}` — includes transcript-only sessions? **No**: only structured notes; transcripts are reachable via `/lessons/{id}/transcript`. |
| GET | `/notes/{note_id}` | — | `NoteDetail` |
| GET | `/healthz` | — | `{status:"ok", version, db:"ok", qwen_configured: bool}` |

DTOs:
```
Student      { id, email, display_name, default_level, default_duration_minutes, preferences, created_at }
LessonSummary{ id, topic, subject, status, learning_level, duration_minutes, progress,
               current_concept, started_at, last_activity_at, ended_at,
               has_transcript, has_notes, board_revision }
LessonDetail { ...LessonSummary, plan: LessonPlan, teaching_state: TeachingState,
               elapsed_seconds, remaining_seconds, assessment_summary }
LessonPlan   { objectives: string[], concepts: [{ id, title, order, status, objectives: string[] }] }
TeachingState{ current_concept_id, current_concept_title, completed_concept_ids,
               pending_concept_ids, progress, understanding_score, assessment_results,
               recommended_action, is_finished }
BoardSnapshotEnvelope { session_id, revision, last_sequence, snapshot: BoardDocument|null,
                        snapshot_at, board_events?: [...] }
BoardDocument{ session_id, revision, last_sequence, elements: [BoardElement], created_at, updated_at }
BoardElement { element_id, type, level, payload, position, size, style, color,
               created_sequence, updated_sequence?, removed_at_sequence?, animation }
TranscriptEntry { id, session_id, sequence, speaker: "tutor"|"student"|"system",
                  text, started_at, ended_at? }
NoteSummary  { id, lesson_id, title, subject, status: "pending"|"ready"|"failed",
               created_at, updated_at, word_count }
NoteDetail   { ...NoteSummary, content_markdown, sections: [{heading, body, latex?}],
               source_transcript_id, generated_by, error? }
```

Pagination: `{items, total, limit, offset}` where list endpoints support it.

---

## 6. Realtime WebSocket protocol

Endpoint: `GET /api/v1/ws/lessons/{lesson_id}?token=<jwt>` (upgrade to WS).
One socket per active lesson. Audio flows through the backend (the Qwen API key
never reaches the client).

Server→client (JSON text frames unless noted):

| type | payload |
|---|---|
| `session.ready` | `{lesson_id, student_id, lease_id, board: BoardSnapshotEnvelope, transcript_cursor, lesson: LessonDetail, audio: {input_sample_rate:16000, output_sample_rate:24000, format:"pcm16", channels:1}}` |
| `board.event` | `{event: BoardEvent}` |
| `board.events` | `{events: [BoardEvent], from_sequence, to_sequence}` (replay/backfill) |
| `board.snapshot` | `{board: BoardSnapshotEnvelope}` (periodic compaction / on request) |
| `audio.delta` | `{data: "<base64 pcm16>", sample_rate: 24000, sequence}` tutor speech chunk |
| `audio.done` | `{response_id}` end of a tutor utterance |
| `audio.truncated` | `{response_id, played_ms}` echo of interruption truncation |
| `teacher.state` | `{speaking: bool, listening: bool, thinking: bool}` |
| `transcript.entry` | `{entry: TranscriptEntry}` |
| `lesson.state` | `{lesson: LessonDetail}` |
| `session.interrupted` | `{reason, at_sequence}` |
| `session.ended` | `{reason, needs_notes_prompt: bool}` |
| `error` | `{code, message, retryable}` |
| `pong` | `{t}` |

Client→server:

| type | payload |
|---|---|
| binary frame | raw little-endian PCM16 mono 16 kHz microphone audio (preferred) |
| `audio.start` | `{sample_rate:16000, format:"pcm16"}` |
| `audio.stop` | `{}` |
| `session.interrupt` | `{}` — barge-in: server cancels Qwen response, truncates playback |
| `session.end` | `{reason?}` |
| `board.request_snapshot` | `{}` |
| `board.backfill` | `{after_sequence}` |
| `ping` | `{}` |

Typed questions are **not** part of the realtime contract: Qwen-Omni realtime
accepts audio (and image) input only — there is no text turn, so a typed
question cannot be answered by the live tutor. The Live screen therefore
requires a working microphone for the AI teacher (with a clear permission/error
state), and typed input is limited to the topic field on Home.

### Qwen Realtime wire facts (verified against DashScope)

* Endpoint: `wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<model>`,
  headers `Authorization: Bearer <key>` and
  `X-DashScope-DataInspection: disable`.
* `session.update` shape:
  ```json
  {"type":"session.update","session":{
    "instructions":"...", "modalities":["audio","text"], "voice":"Cherry",
    "input_audio_format":"pcm", "output_audio_format":"pcm",
    "turn_detection":{"type":"server_vad","threshold":0.5,"silence_duration_ms":800},
    "input_audio_transcription":{"model":"qwen3-asr-flash-realtime"},
    "tools":[...]}}
  ```
  Readiness is signalled by a `session.updated` frame (timeout ≈15 s).
* Client events: `input_audio_buffer.append` (base64 PCM16 16 kHz mono),
  `input_audio_buffer.commit`, `response.create`, `response.cancel`,
  `conversation.item.create` with `{"type":"function_call_output","call_id",...}`.
* Server events consumed: `response.created`, `response.audio.delta` (base64
  PCM16 24 kHz), `response.audio_transcript.delta`, `response.done`,
  `input_audio_buffer.speech_started/stopped`,
  `conversation.item.input_audio_transcription.completed`,
  `response.output_item.added` (function_call name), 
  `response.function_call_arguments.delta/done`, `error`.
* **No `conversation.item.truncate` support** on this API. Barge-in is handled
  by `response.cancel` plus a client-side playback flush; `audio.truncated` is a
  backend→client instruction with the client-measured `played_ms`, not a Qwen
  call.
* The API key never leaves the backend.

Reliability:
* `audio.delta.sequence` is the Qwen audio chunk order; client may drop late
  chunks but must not stall the pipeline.
* Board events are the source of truth and arrive with `sequence`; duplicates
  ignored. On reconnect the client sends `board.backfill` with its last applied
  sequence and then `session.ready` is followed by `board.events`.
* Server keeps a `lease_id` per socket; a newer socket for the same lesson takes
  over (old one gets `session.interrupted` then close 4001).
* Max frame 512 KiB; heartbeat ping every 20 s; close codes:
  `4001` superseded, `4003` forbidden/not owner, `4004` lesson ended,
  `4010` Qwen unavailable.

---

## 7. Error envelope

```json
{ "error": { "code": "invalid_arguments", "message": "human readable", "details": {...}, "retryable": true } }
```

Codes: `unauthorized`(401), `forbidden`(403), `not_found`(404), `conflict`(409),
`invalid_arguments`(422), `validation_error`(422), `rate_limited`(429),
`qwen_unavailable`(503), `internal_error`(500). UI must show a friendly message
and never leak stack traces.

---

## 8. Database schema and ownership

SQLAlchemy 2.0 async models, PostgreSQL in production (`asyncpg`), SQLite
(`aiosqlite`) allowed only for tests/dev. Alembic owns migrations. LangGraph
checkpoint + Store tables are created by the `langgraph-checkpoint-postgres`
library (`setup()` on startup), **not** by Alembic.

| table | key columns | owner of writes |
|---|---|---|
| `users` | id, email uq, password_hash, display_name, created_at | auth |
| `student_learning_profiles` | student_id PK/FK, level, default_duration_minutes, preferences JSON | profile |
| `student_concept_mastery` | student_id, subject, concept, mastery_level 0–5, status, attempts, correct_answers, misconceptions JSON, updated_at; uq(student_id, subject, concept) | graph/memory |
| `student_learning_observations` | id, student_id, lesson_id, subject, concept, observation, kind, created_at | graph/memory |
| `lessons` | id, student_id, topic, subject, learning_level, duration_minutes, status, current_concept, progress, started_at, last_activity_at, ended_at, board_revision, transcript_id? | lessons service |
| `lesson_plans` | id, lesson_id uq, objectives JSON, concepts JSON, created_at, updated_at | graph/lessons |
| `lesson_checkpoints` | id, lesson_id uq, langgraph_thread_id, state JSON (denormalized resume info), created_at, updated_at | graph (LangGraph checkpoint remains authoritative; this is a **resume index only**) | 
| `lesson_transcripts` | id, lesson_id uq, content JSONB optional | transcript |
| `lesson_transcript_entries` | id, lesson_id, transcript_id, sequence, speaker, text, started_at, ended_at; uq(lesson_id, sequence) | transcript |
| `lesson_board_events` | id, lesson_id, event_id uq, sequence, type, element_id, payload JSON, created_at; uq(lesson_id, sequence) | board service |
| `lesson_board_snapshots` | id, lesson_id, revision, last_sequence, document JSON, created_at; uq(lesson_id, revision) | board service |
| `lesson_assessments` | id, lesson_id, question, expected_answer?, student_answer?, correct bool?, concept, feedback, created_at | graph/lessons |
| `lesson_notes` | id, lesson_id, student_id, title, subject, status, content_markdown, sections JSON, source_transcript_id, generated_by, error, created_at, updated_at | notes service |

Rules:
* `lesson_checkpoints.state` must never be treated as teaching truth — LangGraph
  checkpoint (`thread_id = lesson id`) is authoritative for teaching state.
* Board event history + latest snapshot are the board truth; Flutter reconstructs
  by snapshot@revision then events with `sequence > last_sequence`.
* Snapshots every N=40 events or on session end; keep last 5.
* Every write checks `lesson.student_id == current_student.id`.

---

## 9. LangGraph contract

Thread id = lesson id. State (`LessonState` TypedDict, all JSON-safe):

```python
{
  "lesson_id": str, "student_id": str, "topic": str, "subject": str,
  "learning_level": str, "duration_minutes": int,
  "plan": {"objectives": [...], "concepts": [{"id","title","objectives":[...],"status"}]},
  "current_concept": str|None,
  "completed_concepts": [str], "pending_concepts": [str],
  "understanding_score": float, "assessment_results": [...],
  "student_memory": {...},          # retrieved long-term memory snapshot for prompt
  "board_revision": int, "board_snapshot_ref": str|None,
  "elapsed_seconds": int, "remaining_seconds": int,
  "status": "created"|"live"|"paused"|"completed"|"error",
  "recommended_action": str|None,
  "last_updated": str,
}
```

Nodes: `retrieve_memory` → `plan_lesson` (or `load_plan`) → `teach_step`
(no-op placeholder for between-tool state) → `record_answer` →
`evaluate_and_advance` → `persist_observations` → `finalize_lesson`.
Tool calls invoke the orchestrator which performs `graph.update_state` /
`graph.ainvoke` with the appropriate action, then returns compact JSON.

Long-term memory Store namespaces:
* `("students", student_id, "profile")` → preferences/level history
* `("students", student_id, "concepts")` → per-concept mastery docs
* `("students", student_id, "observations")` → append-only learning observations
Mastery is updated only from evidence (answer evaluation, explicit revisit), with
`mastery_level` clamped 0..5.

---

## 10. Flutter app contract (Dart API surface)

Package name: `ai_classroom`. Entry `lib/main.dart`.

Blackboard module (`lib/blackboard/`) — owner flutter-board, public barrel
`lib/blackboard/blackboard.dart` must export exactly:

```dart
class BoardEvent {           // parsed from JSON per §2/§3
  final String eventId, sessionId, type;
  final int sequence, boardRevision;
  final String? elementId;
  final Map<String, dynamic> payload;
  final List<String> dependsOn;
  final bool animate;
  final DateTime createdAt;
  factory BoardEvent.fromJson(Map<String, dynamic> json);
}

class BoardDocument {        // structured document per §5
  final String sessionId;
  final int revision, lastSequence;
  final Map<String, BoardElement> elements;   // insertion = z-order within level
  BoardDocument.fromJson(Map<String, dynamic> json);
  Map<String, dynamic> toJson();
  BoardDocument apply(BoardEvent event);      // pure, idempotent
  BoardDocument restore(List<BoardEvent> events); // replay
}

class BlackboardController extends ChangeNotifier {
  BlackboardController({BoardEventQueue? queue});
  BoardDocument get document;
  int get appliedSequence;
  bool get isAnimating;
  void loadSnapshot(BoardDocument? doc);
  void enqueue(BoardEvent event);        // ordered queue + idempotency + gap buffer
  void enqueueAll(List<BoardEvent> events);
  void backfill(List<BoardEvent> events); // older events during reconnect
  Listenable get repaint;                // animation ticker neverifier
  Future<void> waitForIdle();
  void dispose();
}

class BlackboardView extends StatelessWidget {  // the tutorial board widget
  const BlackboardView({super.key, required this.controller, this.interactive = true});
  final BlackboardController controller;
  final bool interactive;
}
```

Rendering requirements: layered `CustomPainter`s (background, text, equation,
drawing, graph, annotations), `flutter_math_fork` for LaTeX, progressive stroke
reveal driven by a single `Ticker`/`AnimationController`, `RepaintBoundary` per
level, no WebView/canvas-html/screenshots, normalized→pixel mapping via one
`BoardTransform` (content rect with padding), and chalk-style text rendering
(stroke reveal over glyphs, not whole-widget fade).

Realtime client (`lib/services/realtime_client.dart`): connects §6 WS, exposes
broadcast streams `boardEvents`, `audioDeltas`, `transcriptEntries`,
`lessonStates`, `teacherStates`, `connectionStates`, sends mic PCM16 frames and
control messages. Audio: `record` for 16 kHz PCM16 mic, `flutter_pcm_sound` (or
platform channel) for 24 kHz PCM16 playback; playback and rendering are
independent — never `await` audio before board.

Screens: `lib/features/home`, `notes`, `profile`, `live`; shell with exactly
three `NavigationDestination`s (Home, Notes, Profile). Selecting a notes-list
session shows a modal with **View notes** and **Continue Live Session**. On
lesson end, ask "Would you like me to generate structured lesson notes from this
tutorial?" and only call `POST .../notes/generate` with `consent: true` on yes.

State: no heavy external state framework; `ChangeNotifier`/`ValueNotifier` +
`StreamBuilder` is fine. `http` for REST, `web_socket_channel` for WS,
`shared_preferences` for token. API base URL from `--dart-define=API_BASE_URL`.

---

## 11. Testing gates

* Backend: `pytest` green; coverage of board registry validation, event
  sequencing/idempotency, ownership enforcement, lesson lifecycle, transcript
  persistence, notes consent gate, LangGraph state transitions, board
  reconstruction from snapshot+events.
* Flutter: `flutter analyze` clean; `flutter test` green for pure logic (document
  reducer, coordinate transform, event ordering/gap handling, audio decode).
* Integration: no real Qwen key needed — `FakeRealtimeTransport` (backend) and
  `MockQwenSession` used in tests; Qwen adapter tested for message framing with a
  local websockets fake server.
* Contract drift: `backend/tests/test_contract_parity.py` asserts every tool name
  in §4 exists in the registry and every event type in §3 is handled by the
  Flutter document reducer list exported from Dart (kept as a checked-in JSON
  fixture `docs/contracts/event_types.json`; Flutter test reads it).
