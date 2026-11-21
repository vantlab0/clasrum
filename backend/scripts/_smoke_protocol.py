import sys

sys.path.insert(0, ".")
from app.board.drawing_protocol import parse_drawing_plan, plan_total_duration_ms, plan_element_bounds

plan = {
    "protocol": "clasrum.drawing",
    "version": 1,
    "title": "Mitochondrion",
    "elements": [
        {
            "id": "outer",
            "kind": "path",
            "geometry": {
                "segments": [
                    {"op": "move", "point": {"x": 0.3, "y": 0.4}},
                    {"op": "cubic", "c1": {"x": 0.35, "y": 0.2}, "c2": {"x": 0.55, "y": 0.18}, "point": {"x": 0.62, "y": 0.35}},
                    {"op": "cubic", "c1": {"x": 0.68, "y": 0.55}, "c2": {"x": 0.5, "y": 0.7}, "point": {"x": 0.34, "y": 0.58}},
                    {"op": "close"},
                ]
            },
            "style": {"stroke_color": "blue", "stroke_width": 2.5, "fill_color": "#223322", "fill_opacity": 0.25, "line_cap": "round"},
            "layer": 0,
            "stage": 1,
            "duration_ms": 800,
            "delay_ms": 0,
        },
        {
            "id": "cristae1",
            "kind": "quad_bezier",
            "geometry": {"start": {"x": 0.4, "y": 0.4}, "control": {"x": 0.5, "y": 0.3}, "end": {"x": 0.55, "y": 0.45}},
            "style": {"stroke_color": "primary", "stroke_width": 1.5},
            "layer": 1,
            "stage": 2,
            "duration_ms": 400,
            "delay_ms": 100,
        },
        {
            "id": "lbl",
            "kind": "text",
            "geometry": {"text": "Cristae", "position": {"x": 0.66, "y": 0.42}, "align": "left"},
            "layer": 2,
            "stage": 3,
            "duration_ms": 300,
        },
    ],
    "stages": [
        {"stage": 1, "description": "outer membrane", "element_ids": ["outer"], "pause_ms": 150},
        {"stage": 2, "description": "cristae", "element_ids": ["cristae1"]},
        {"stage": 3, "description": "labels", "element_ids": ["lbl"]},
    ],
}

p = parse_drawing_plan(plan)
payload = p.to_payload()
print("elements:", len(payload["elements"]))
print("duration:", plan_total_duration_ms(payload))
print("bounds:", plan_element_bounds(payload))
try:
    parse_drawing_plan({"elements": []})
except Exception as e:
    print("reject empty:", str(e)[:90])
bad = dict(plan)
bad["elements"] = [dict(plan["elements"][0], kind="hatch", geometry={})]
try:
    parse_drawing_plan(bad)
except Exception as e:
    print("reject bad kind:", str(e)[:90])
bad2 = dict(plan)
bad2["elements"] = [dict(plan["elements"][0], style={"fill_color": "blue"})]
try:
    parse_drawing_plan(bad2)
except Exception as e:
    print("reject fill on open:", str(e)[:90])
bad3 = dict(plan)
bad3["elements"] = [dict(plan["elements"][0], clip_id="cristae1")]
try:
    parse_drawing_plan(bad3)
except Exception as e:
    print("reject clip:", str(e)[:90])
