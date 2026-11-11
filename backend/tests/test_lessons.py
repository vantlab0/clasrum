"""Lesson lifecycle: create/list/detail/resume/end (§5)."""
from __future__ import annotations

import re

TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _assert_summary_shape(item: dict) -> None:
    expected_summary = {
        "id",
        "topic",
        "subject",
        "status",
        "learning_level",
        "duration_minutes",
        "progress",
        "current_concept",
        "started_at",
        "last_activity_at",
        "ended_at",
        "has_transcript",
        "has_notes",
        "board_revision",
    }
    # GET /lessons returns LessonSummary; POST /lessons returns LessonDetail
    # which adds: remaining_seconds, plan, elapsed_seconds, assessment_summary,
    # teaching_state.  Accept either shape.
    detail_only = {
        "remaining_seconds",
        "plan",
        "elapsed_seconds",
        "assessment_summary",
        "teaching_state",
    }
    assert expected_summary.issubset(set(item)), f"missing: {expected_summary - set(item)}"
    extra = set(item) - expected_summary - detail_only
    assert not extra, f"unexpected fields: {extra}"
    assert TIMESTAMP_RE.match(item["started_at"]), item["started_at"]
    assert 0.0 <= float(item["progress"]) <= 1.0


async def test_create_lesson(client, seeded_student, auth_headers, make_lesson):
    lesson = await make_lesson(seeded_student)
    assert lesson["topic"] == "Introduction to Photosynthesis"
    assert lesson["subject"] == "Biology"
    assert lesson["duration_minutes"] == 20
    assert lesson["status"] in {"created", "live"}
    assert lesson["has_transcript"] is False
    assert lesson["has_notes"] is False
    assert lesson["board_revision"] == 0
    assert lesson["elapsed_seconds"] >= 0
    assert 0 <= lesson["remaining_seconds"] <= 20 * 60
    plan = lesson["plan"]
    assert isinstance(plan["objectives"], list) and plan["objectives"]
    assert plan["concepts"], "graph start_lesson must return a plan with concepts"
    for concept in plan["concepts"]:
        assert set(concept) == {"id", "title", "order", "status", "objectives"}
        assert isinstance(concept["objectives"], list)
    state = lesson["teaching_state"]
    assert set(state) == {
        "current_concept_id",
        "current_concept_title",
        "completed_concept_ids",
        "pending_concept_ids",
        "progress",
        "understanding_score",
        "assessment_results",
        "recommended_action",
        "is_finished",
    }
    assert state["is_finished"] is False
    _assert_summary_shape(lesson)


async def test_create_lesson_validation(client, seeded_student, auth_headers):
    cases = [
        {"topic": "a", "learning_level": "high_school", "duration_minutes": 20},
        {"topic": "Valid topic", "learning_level": "high_school", "duration_minutes": 0},
        {"topic": "Valid topic", "learning_level": "high_school", "duration_minutes": 5000},
        {"topic": "Valid topic", "learning_level": "high_school"},
        {"learning_level": "high_school", "duration_minutes": 20},
    ]
    for payload in cases:
        response = await client.post(
            "/api/v1/lessons", json=payload, headers=auth_headers(seeded_student)
        )
        assert response.status_code == 422, (payload, response.text)
        assert response.json()["error"]["code"] == "invalid_arguments"


async def test_create_lesson_requires_auth(client):
    response = await client.post(
        "/api/v1/lessons",
        json={"topic": "Valid topic", "learning_level": "high_school", "duration_minutes": 20},
    )
    assert response.status_code == 401


async def test_list_lessons_with_filters_and_pagination(
    client, seeded_student, auth_headers, make_lesson
):
    first = await make_lesson(seeded_student, topic="Algebra Basics", subject="Maths")
    second = await make_lesson(seeded_student, topic="Cell Biology", subject="Biology")

    response = await client.get("/api/v1/lessons", headers=auth_headers(seeded_student))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 50 and body["offset"] == 0
    assert {item["id"] for item in body["items"]} == {first["id"], second["id"]}
    assert body["items"][0]["id"] == second["id"]  # newest activity first
    for item in body["items"]:
        _assert_summary_shape(item)

    filtered = await client.get(
        "/api/v1/lessons",
        headers=auth_headers(seeded_student),
        params={"status": "created"},
    )
    # The graph may move a lesson to `live`; in that case `created` filters it out.
    assert filtered.status_code == 200
    assert all(item["status"] == "created" for item in filtered.json()["items"])

    invalid = await client.get(
        "/api/v1/lessons",
        headers=auth_headers(seeded_student),
        params={"status": "nonsense"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_arguments"

    paged = await client.get(
        "/api/v1/lessons",
        headers=auth_headers(seeded_student),
        params={"limit": 1, "offset": 1},
    )
    assert paged.status_code == 200
    assert paged.json()["total"] == 2
    assert len(paged.json()["items"]) == 1
    assert paged.json()["items"][0]["id"] == first["id"]


async def test_get_lesson_detail(client, seeded_student, auth_headers, make_lesson):
    created = await make_lesson(seeded_student)
    response = await client.get(
        f"/api/v1/lessons/{created['id']}", headers=auth_headers(seeded_student)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["plan"]["objectives"] == created["plan"]["objectives"]
    assert body["teaching_state"]["is_finished"] is False


async def test_end_lesson_is_idempotent(client, seeded_student, auth_headers, make_lesson):
    lesson = await make_lesson(seeded_student)
    first = await client.post(
        f"/api/v1/lessons/{lesson['id']}/end",
        headers=auth_headers(seeded_student),
        json={"reason": "student_finished"},
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "completed"
    assert TIMESTAMP_RE.match(body["ended_at"])
    assert body["remaining_seconds"] == 0
    assert body["teaching_state"]["is_finished"] is True
    ended_at = body["ended_at"]

    second = await client.post(
        f"/api/v1/lessons/{lesson['id']}/end",
        headers=auth_headers(seeded_student),
        json={},
    )
    assert second.status_code == 200
    assert second.json()["ended_at"] == ended_at
    assert second.json()["status"] == "completed"


async def test_resume_before_end(
    client, seeded_student, auth_headers, make_lesson, append_turns
):
    lesson = await make_lesson(seeded_student)
    await append_turns(lesson["id"], ["Welcome to the tutorial.", ("student", "Hello!")])
    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/resume",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"lesson", "board", "transcript_cursor", "needs_notes_prompt"}
    assert body["lesson"]["status"] == "live"
    assert body["lesson"]["has_transcript"] is True
    assert body["transcript_cursor"] == 2
    assert body["needs_notes_prompt"] is False
    board = body["board"]
    assert board["session_id"] == lesson["id"]
    assert board["revision"] == lesson["board_revision"]
    assert board["last_sequence"] == 0
    assert board["snapshot"] is None
    assert board["board_events"] == []


async def test_resume_ended_lesson_prompts_for_notes(
    client, seeded_student, auth_headers, make_lesson, append_turns
):
    lesson = await make_lesson(seeded_student)
    await append_turns(lesson["id"], ["Explain differentiation.", ("student", "I see.")])
    await client.post(
        f"/api/v1/lessons/{lesson['id']}/end",
        headers=auth_headers(seeded_student),
        json={},
    )
    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/resume",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["needs_notes_prompt"] is True
    assert body["transcript_cursor"] == 2
    assert body["lesson"]["status"] == "live"
    assert body["lesson"]["ended_at"] is None


async def test_ended_lesson_without_transcript_does_not_prompt(
    client, seeded_student, auth_headers, make_lesson
):
    lesson = await make_lesson(seeded_student)
    await client.post(
        f"/api/v1/lessons/{lesson['id']}/end",
        headers=auth_headers(seeded_student),
        json={},
    )
    response = await client.post(
        f"/api/v1/lessons/{lesson['id']}/resume",
        headers=auth_headers(seeded_student),
    )
    assert response.status_code == 200
    assert response.json()["needs_notes_prompt"] is False


async def test_unknown_lesson_is_404(client, seeded_student, auth_headers):
    response = await client.get(
        "/api/v1/lessons/does-not-exist", headers=auth_headers(seeded_student)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
