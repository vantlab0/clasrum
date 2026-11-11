# AI Personal Classroom

An AI-powered private classroom: the student speaks with a tutor that **teaches
out loud while writing, drawing and solving on a native Flutter blackboard**.
Qwen Realtime is the teacher, LangGraph is the lesson/learning-memory engine,
FastAPI is the control plane, and the blackboard is rendered natively in Flutter
(`CustomPainter` layers — no WebView, no HTML canvas, no screenshots).

```
┌──────────────────────── Flutter (Android / iOS) ────────────────────────┐
│ Home · Notes · Profile            Live tutorial                         │
│   │  REST /api/v1                 │  WebSocket /api/v1/ws/lessons/{id}   │
│   │                               ├── board events  ─► layered painters │
│   │                               ├── audio deltas  ─► PCM playback     │
│   │                               └── transcript / lesson state          │
└───┼───────────────────────────────┼─────────────────────────────────────┘
    ▼                               ▼
┌──────────────────────── FastAPI backend ────────────────────────────────┐
│ REST · WS hub · auth/ownership · transcripts · notes generation         │
│   │                              ▲                                      │
│   │ board events (validated)     │ audio / transcripts / tool calls     │
│   ▼                              │                                      │
│ Tool registry (28 native fns) ──► Qwen Realtime session (WebSocket)      │
│   │                                                                     │
│   ▼                                                                     │
│ LangGraph StateGraph + Store (Postgres checkpointing)                   │
└─────────────────────────────────────────────────────────────────────────┘
                  PostgreSQL (lessons, board doc/events, transcripts, notes)
```

## Repository layout

| path | contents |
|---|---|
| `backend/` | FastAPI service, Qwen adapter, tool registry, LangGraph graph + memory, Alembic, pytest |
| `app/` | Flutter application: 3-tab shell, live classroom, native blackboard engine |
| `docs/CONTRACTS.md` | **authoritative** wire contracts (events, tools, REST, WS, DB ownership) |
| `docs/ARCHITECTURE.md` | component/data-flow design, reliability and teaching loop |
| `docs/contracts/event_types.json` | machine-readable contract fixture used by both test suites |
| `docker-compose.yml` | PostgreSQL + backend container |

## Quickstart — backend

Python 3.11+ (the repo was developed on 3.12).

```powershell
cd ai-classroom
python -m venv .venv
.\.venv\Scripts\pip install -r backend\requirements.txt
copy .env.example .env            # edit JWT_SECRET / QWEN_API_KEY as needed

cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

* `http://127.0.0.1:8000/healthz` → `{ "status": "ok", ... }`
* Interactive docs: `http://127.0.0.1:8000/docs`
* Default development database is SQLite (`./dev.db`) so no Postgres is needed.
* No Qwen key? Set `QWEN_FAKE_MODE=true` and the backend serves a deterministic
  offline tutor that still speaks through the same pipeline and writes on the
  board — useful for demos, UI work and CI.

Production: `docker compose up --build` (PostgreSQL 16 + backend with
`AsyncPostgresSaver`/`AsyncPostgresStore` durable LangGraph persistence).

## Quickstart — Flutter app

Flutter 3.24+ with an Android emulator or iOS simulator.

```powershell
cd ai-classroom\app
flutter pub get
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000   # Android emulator
# iOS simulator: --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

`10.0.2.2` is the host machine as seen from the Android emulator; on a physical
device use your machine's LAN IP and make sure the backend binds `0.0.0.0`.

## How a lesson works

1. **Home** → student types a topic, picks level + duration → `POST /api/v1/lessons`.
2. Backend creates the lesson row, initialises the **LangGraph** lesson state
   (retrieving long-term mastery/observations first), builds a concept plan.
3. Flutter opens `/api/v1/ws/lessons/{id}`. Backend opens the **Qwen Realtime**
   session with scripted teacher instructions + all native tool definitions.
4. Audio and board events stream **concurrently**: the tutor speaks while
   `board_*` function calls are validated, persisted as structured events
   (`sequence`, `event_id`, normalized coordinates) and pushed to Flutter, which
   animates chalk writing/drawing in order.
5. Student barges in (`session.interrupt`): playback truncates, the tutor adapts;
   LangGraph keeps the authoritative teaching state.
6. **End lesson** → transcript is already persisted; the app asks
   *"Would you like me to generate structured lesson notes from this tutorial?"*
   Consent → structured notes are generated and stored **separately** from the
   raw transcript.
7. **Notes** lists sessions + structured notes; the session popup offers
   **View notes** and **Continue Live Session**. **Profile** holds settings and a
   full data-deletion control.

## Blackboard

Six native layers, one `BoardDocument` of structured elements, snapshot +
append-only event history:

1. matte `#171817` chalkboard background with subtle texture
2. chalk handwriting (progressive stroke reveal)
3. LaTeX equations (`flutter_math_fork`, progressive by section)
4. diagrams/drawings (paths, shapes, flowcharts, circuits, forces, biology)
5. graphs (coordinate systems, sampled functions, points, labels)
6. highlights/annotations that reference existing element ids

The AI never touches widgets: `Qwen → function call → backend validation →
board event → Flutter renderer`. Malformed or out-of-order operations can never
corrupt the board.

## Tests

```powershell
# backend (SQLite, no external services)
cd backend; ..\.venv\Scripts\python -m pytest -q

# flutter logic + widgets
cd app; flutter analyze; flutter test
```

Contract parity is enforced by `backend/tests/test_contract_parity.py` and
`app/test/contract_parity_test.dart`, both reading
`docs/contracts/event_types.json`.

## Status / scope

Implemented in this repository: full backend (auth, lessons, realtime pipeline,
tool registry, board persistence, LangGraph memory/state, notes generation,
transcripts), the native Flutter blackboard engine, and the three-tab application
with the live tutorial experience. Profile is intentionally an interim version —
its final specification is defined separately (see PRD §3.3). Exact physical DB
indexes may evolve with Alembic migrations.

## Security

* Every lesson/transcript/note/board/memory record is scoped to the authenticated
  student; ownership is verified server-side on every access.
* Tool calls are schema-validated; no eval/SQL/filesystem/shell/network tool is
  exposed to the model.
* The Qwen API key stays in the backend; clients only get lesson-scoped JWTs.
* `DELETE /api/v1/me/data` removes the student's lessons, transcripts, board
  history, notes and learning memory.
\"# clasrum\"  
