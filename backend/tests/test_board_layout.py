from app.board.layout import BlackboardLayoutManager, Rect, estimate_text_size


def test_layout_places_sequential_text_without_overlap():
    mgr = BlackboardLayoutManager({"elements": []})
    p1 = mgr.place_text({
        "text": "Mass",
        "style": "heading",
        "heading_level": 1,
        "position_hint": "next_line",
        "side_preference": "auto",
    })
    p2 = mgr.place_text({
        "text": "Mass is the amount of matter in an object.",
        "style": "body",
        "position_hint": "next_line",
        "side_preference": "auto",
    })
    p3 = mgr.place_text({
        "text": "W = m g",
        "style": "equation",
        "latex": "W = m g",
        "position_hint": "next_line",
        "side_preference": "auto",
    })

    r1 = Rect(p1["position"]["x"], p1["position"]["y"], p1["size"]["w"], p1["size"]["h"])
    r2 = Rect(p2["position"]["x"], p2["position"]["y"], p2["size"]["w"], p2["size"]["h"])
    r3 = Rect(p3["position"]["x"], p3["position"]["y"], p3["size"]["w"], p3["size"]["h"])

    assert not r1.intersects(r2)
    assert not r2.intersects(r3)
    assert not r1.intersects(r3)
    assert p1["position"]["y"] <= p2["position"]["y"] <= p3["position"]["y"]


def test_heading_levels_have_distinct_estimated_sizes():
    h1 = estimate_text_size("Title", style="heading", heading_level=1)
    h3 = estimate_text_size("Title", style="heading", heading_level=3)
    h6 = estimate_text_size("Title", style="heading", heading_level=6)
    body = estimate_text_size("Title", style="body")
    assert h1[1] > h3[1] > h6[1]
    assert h6[1] <= body[1] * 1.2  # close to body scale, but distinct


def test_layout_respects_side_preference_when_space_available():
    mgr = BlackboardLayoutManager({"elements": []})
    left = mgr.place_text({
        "text": "Mass: amount of matter",
        "style": "body",
        "position_hint": "left",
        "side_preference": "left",
    })
    right = mgr.place_text({
        "text": "Weight: force due to gravity",
        "style": "body",
        "position_hint": "right",
        "side_preference": "right",
    })
    assert left["position"]["x"] < 0.3
    assert right["position"]["x"] > 0.45


def test_visual_region_finder_avoids_occupied_area():
    doc = {
        "elements": [
            {
                "element_id": "e1",
                "type": "write_text",
                "position": {"x": 0.05, "y": 0.05},
                "size": {"w": 0.4, "h": 0.2},
                "payload": {},
            }
        ]
    }
    mgr = BlackboardLayoutManager(doc)
    name, rect = mgr.find_region_for_visual(width=0.35, height=0.25, preferred="left")
    existing = Rect(0.05, 0.05, 0.4, 0.2)
    assert not rect.intersects(existing)
    assert rect.w > 0 and rect.h > 0
