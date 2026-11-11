"""REST board reads: snapshot envelope + event backfill (§5)."""
from __future__ import annotations


def _event(index: int, event_type: str, element_id: str, payload: dict) -> dict:
    return {"type": event_type, "element_id": element_id, "payload": payload}


DEMO_EVENTS = [
    _event(
        1,
        "write_heading",
        "el_heading",
        {"text": "Cell Structure", "position": {"x": 0.05, "y": 0.05}, "level": 1},
    ),
    _event(
        2,
        "write_text",
        "el_intro",
        {
            "text": "Cells are the basic unit of life.",
            "style": "body",
            "position": {"x": 0.05, "y": 0.15},
            "color": "primary",
        },
    ),
    _event(
        3,
        "write_equation",
        "el_equation",
        {"latex": "E = mc^2", "position": {"x": 0.05, "y": 0.3}},
    ),
    _event(
        4,
        "write_bullet_list",
        "el_list",
        {
            "items": ["Nucleus", "Mitochondria", "Cell membrane"],
            "position": {"x": 0.05, "y": 0.45},
            "style": "bullet",
        },
    ),
]


async def test_board_events_endpoint_returns_ordered_events(
    client, seeded_student, auth_headers, make_lesson, seed_board
):
    lesson = await make_lesson(seeded_student)
    await seed_board(lesson["id"], DEMO_EVENTS)

    response = await client.get(
        f"/api/v1/lessons/{lesson['id']}/board/events",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200
    events = response.json()["events"]
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert [event["type"] for event in events] == [
        "write_heading",
        "write_text",
        "write_equation",
        "write_bullet_list",
    ]
    first = events[0]
    assert first["session_id"] == lesson["id"]
    assert first["event_id"]
    assert first["payload"]["text"] == "Cell Structure"
    assert first["board_revision"] == 1
    assert first["source"] == "qwen_tool"
    assert first["animate"] is True
    assert first["created_at"].endswith("Z")

    backfill = await client.get(
        f"/api/v1/lessons/{lesson['id']}/board/events",
        headers=auth_headers(seeded_student),
        params={"after_sequence": 2},
    )
    assert backfill.status_code == 200
    assert [event["sequence"] for event in backfill.json()["events"]] == [3, 4]

    nothing = await client.get(
        f"/api/v1/lessons/{lesson['id']}/board/events",
        headers=auth_headers(seeded_student),
        params={"after_sequence": 999},
    )
    assert nothing.status_code == 200
    assert nothing.json()["events"] == []

    invalid = await client.get(
        f"/api/v1/lessons/{lesson['id']}/board/events",
        headers=auth_headers(seeded_student),
        params={"after_sequence": -1},
    )
    assert invalid.status_code == 422


async def test_board_without_snapshot_returns_full_replay_log(
    client, seeded_student, auth_headers, make_lesson, seed_board
):
    lesson = await make_lesson(seeded_student)
    await seed_board(lesson["id"], DEMO_EVENTS)

    response = await client.get(
        f"/api/v1/lessons/{lesson['id']}/board",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "session_id",
        "revision",
        "last_sequence",
        "snapshot",
        "snapshot_at",
        "board_events",
    }
    assert body["session_id"] == lesson["id"]
    assert body["revision"] == 4
    assert body["last_sequence"] == 4
    assert body["snapshot"] is None
    assert body["snapshot_at"] is None
    assert [event["sequence"] for event in body["board_events"]] == [1, 2, 3, 4]


async def test_board_with_snapshot_returns_tail_events(
    client, seeded_student, auth_headers, make_lesson, seed_board
):
    lesson = await make_lesson(seeded_student)
    await seed_board(lesson["id"], DEMO_EVENTS, with_snapshot=True, snapshot_revision=2)

    response = await client.get(
        f"/api/v1/lessons/{lesson['id']}/board",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["snapshot"] is not None
    assert body["revision"] == 2
    assert body["last_sequence"] == 2
    assert body["snapshot_at"] is not None
    snapshot = body["snapshot"]
    assert snapshot["session_id"] == lesson["id"]
    assert snapshot["revision"] == 2
    assert snapshot["last_sequence"] == 2
    assert [element["element_id"] for element in snapshot["elements"]] == [
        "el_heading",
        "el_intro",
    ]
    assert snapshot["elements"][0]["level"] == 2
    assert snapshot["elements"][0]["payload"]["text"] == "Cell Structure"
    # Only events after the snapshot's last_sequence are replayed on top.
    assert [event["sequence"] for event in body["board_events"]] == [3, 4]


async def test_resume_includes_board_and_cursor(
    client, seeded_student, auth_headers, make_lesson, seed_board, append_turns
):
    lesson = await make_lesson(seeded_student)
    await seed_board(lesson["id"], DEMO_EVENTS, with_snapshot=True, snapshot_revision=3)
    await append_turns(lesson["id"], ["turn one", "turn two", "turn three"])

    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/resume",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["transcript_cursor"] == 3
    board = body["board"]
    assert board["snapshot"]["revision"] == 3
    assert [event["sequence"] for event in board["board_events"]] == [4]
    assert body["lesson"]["board_revision"] == 4
