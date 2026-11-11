"""DELETE /me/data: confirm gate + full cascade + isolation (§5, §8)."""
from __future__ import annotations


async def test_delete_requires_confirm(client, seeded_student, auth_headers):
    headers = auth_headers(seeded_student)
    for payload in ({}, {"confirm": False}, {"confirm": "true"}):
        response = await client.request("DELETE", "/api/v1/me/data", headers=headers, json=payload)
        if payload == {"confirm": "true"}:
            # wrong type -> request validation 422
            assert response.status_code == 422
        else:
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_arguments"

    no_body = await client.request("DELETE", "/api/v1/me/data", headers=headers)
    assert no_body.status_code == 422


async def test_delete_requires_auth(client):
    response = await client.request(
        "DELETE", "/api/v1/me/data", json={"confirm": True}
    )
    assert response.status_code == 401


async def test_delete_all_data_cascades_and_is_scoped(
    client,
    seeded_student,
    other_student,
    auth_headers,
    make_lesson,
    append_turns,
    seed_board,
    wait_for_note,
    count_rows,
):
    from app.db.base import utcnow
    from app.db.models import (
        Lesson,
        LessonAssessment,
        LessonBoardEvent,
        LessonBoardSnapshot,
        LessonCheckpoint,
        LessonNote,
        LessonPlan,
        LessonTranscript,
        LessonTranscriptEntry,
        StudentConceptMastery,
        StudentLearningObservation,
        StudentLearningProfile,
        User,
    )
    from app.db.session import session_scope

    seeded_lesson = await make_lesson(seeded_student)
    other_lesson = await make_lesson(other_student, topic="Other Student Lesson")
    await append_turns(seeded_lesson["id"], ["seeded one", "seeded two"])
    await append_turns(other_lesson["id"], ["other one"])
    await seed_board(
        seeded_lesson["id"],
        [
            {
                "type": "write_heading",
                "element_id": "el_h",
                "payload": {"text": "Cells", "position": {"x": 0.1, "y": 0.1}},
            },
            {
                "type": "write_text",
                "element_id": "el_t",
                "payload": {
                    "text": "Cells are the basic unit of life.",
                    "position": {"x": 0.1, "y": 0.2},
                },
            },
        ],
        with_snapshot=True,
    )
    await seed_board(
        other_lesson["id"],
        [
            {
                "type": "write_heading",
                "element_id": "el_o",
                "payload": {"text": "Other", "position": {"x": 0.1, "y": 0.1}},
            }
        ],
    )

    async with session_scope() as db:
        for student, lesson in ((seeded_student, seeded_lesson), (other_student, other_lesson)):
            db.add(
                StudentConceptMastery(
                    student_id=student["id"],
                    subject="Biology",
                    concept="Cells",
                    mastery_level=3,
                    status="in_progress",
                )
            )
            db.add(
                StudentLearningObservation(
                    student_id=student["id"],
                    lesson_id=lesson["id"],
                    subject="Biology",
                    concept="Cells",
                    kind="observation",
                    observation="Observed a correct answer.",
                )
            )
            db.add(
                StudentLearningObservation(
                    student_id=student["id"],
                    subject="Biology",
                    concept="Cells",
                    kind="observation",
                    observation="Session-level observation.",
                )
            )
            db.add(
                LessonAssessment(
                    lesson_id=lesson["id"],
                    concept="Cells",
                    question="What is a cell?",
                    expected_answer="The basic unit of life.",
                    student_answer="The basic unit of life.",
                    correct=True,
                    feedback="Correct.",
                    created_at=utcnow(),
                )
            )

    await client.post(
        f"/api/v1/lessons/{seeded_lesson['id']}/end",
        headers=auth_headers(seeded_student),
        json={},
    )
    generated = await client.post(
        f"/api/v1/lessons/{seeded_lesson['id']}/notes/generate",
        headers=auth_headers(seeded_student),
        json={"consent": True},
    )
    assert generated.status_code == 200, generated.text
    await wait_for_note(seeded_student, generated.json()["note"]["id"])

    # Counts before deletion: 2 users, 2 lessons.
    assert await count_rows(User) == 2
    assert await count_rows(Lesson) == 2
    assert await count_rows(LessonPlan) >= 2
    assert await count_rows(LessonTranscriptEntry) == 3
    assert await count_rows(LessonNote) == 1
    assert await count_rows(StudentConceptMastery) == 2
    # 4 direct observations + 1 lesson_summary written by finalize_lesson_node
    # during end_lesson.
    assert await count_rows(StudentLearningObservation) == 5
    assert await count_rows(LessonAssessment) == 2

    response = await client.request(
        "DELETE",
        "/api/v1/me/data",
        headers=auth_headers(seeded_student),
        json={"confirm": True},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True}

    # The deleted student's token is now invalid.
    me = await client.get("/api/v1/me", headers=auth_headers(seeded_student))
    assert me.status_code == 401

    assert await count_rows(User) == 1
    assert await count_rows(StudentLearningProfile) == 1
    assert await count_rows(Lesson) == 1
    assert await count_rows(LessonPlan) == 1
    assert await count_rows(LessonCheckpoint) == 1
    assert await count_rows(LessonTranscript) == 1
    assert await count_rows(LessonTranscriptEntry) == 1
    assert await count_rows(LessonBoardEvent) == 1
    assert await count_rows(LessonBoardSnapshot) == 0
    # Other student's lesson remains, so their assessment remains.
    assert await count_rows(LessonAssessment) == 1
    assert await count_rows(LessonNote) == 0
    assert await count_rows(StudentConceptMastery) == 1
    # Other student keeps their 2 observations (student_id != seeded and
    # lesson_id != seeded lesson_id).
    assert await count_rows(StudentLearningObservation) == 2

    # Survivor keeps working.
    survivor = await client.get("/api/v1/me", headers=auth_headers(other_student))
    assert survivor.status_code == 200
    survivor_lessons = await client.get(
        "/api/v1/lessons", headers=auth_headers(other_student)
    )
    assert survivor_lessons.json()["total"] == 1
