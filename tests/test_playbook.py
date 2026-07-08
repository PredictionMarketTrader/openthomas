import json

from openthomas.memory.journal import Journal
from openthomas.memory.lessons import (
    MAX_ACTIVE_RULES, MAX_ADDS_PER_REFLECTION, LessonBook,
)


def book(tmp_path):
    return LessonBook(tmp_path / "lessons")


def test_add_revise_deprecate_cycle(tmp_path):
    b = book(tmp_path)
    audit = b.apply_ops([
        {"op": "add", "text": "Miami consensus runs 2°F cold — shade highs up",
         "scope": "Miami", "reason": "hindcast bias +1.8..2.1"},
        {"op": "add", "text": "Don't fade LAX afternoon marine layer", "scope": "lax"},
    ])
    assert len(audit) == 2
    rules = b.active_rules()
    assert [r["id"] for r in rules] == [1, 2]
    assert rules[0]["scope"] == "miami"  # normalized lowercase

    b.apply_ops([{"op": "revise", "id": 1, "text": "Miami: shade highs +2°F"}])
    assert b.active_rules()[0]["text"] == "Miami: shade highs +2°F"
    assert "revised" in b.active_rules()[0]

    b.apply_ops([{"op": "deprecate", "id": 2, "reason": "edge decayed"}])
    active = b.active_rules()
    assert len(active) == 1 and active[0]["id"] == 1
    # Deprecated rules stay on file with their reason — audit, not amnesia.
    dead = [r for r in b._load()["rules"] if r["status"] == "deprecated"]
    assert dead[0]["deprecate_reason"] == "edge decayed"


def test_caps_enforced(tmp_path):
    b = book(tmp_path)
    b.apply_ops([{"op": "add", "text": f"seed {i}", "scope": "x"} for i in range(9)])
    assert len(b.active_rules()) == MAX_ADDS_PER_REFLECTION  # per-reflection cap
    for i in range(5):  # fill to the active cap over several reflections
        b.apply_ops([{"op": "add", "text": f"more {i}a", "scope": "x"},
                     {"op": "add", "text": f"more {i}b", "scope": "x"}])
    assert len(b.active_rules()) == MAX_ACTIVE_RULES


def test_bad_ops_ignored(tmp_path):
    b = book(tmp_path)
    audit = b.apply_ops([
        {"op": "revise", "id": 99, "text": "ghost"},
        {"op": "deprecate", "id": 99},
        {"op": "add", "text": ""},
        {"op": "explode"},
    ])
    assert audit == [] and b.active_rules() == []


def test_parse_ops_from_noisy_output():
    noisy = 'Thinking...\n```json\n{"ops": [{"op": "add", "text": "x", "scope": "y"}]}\n```'
    assert LessonBook._parse_ops(noisy)[0]["op"] == "add"
    assert LessonBook._parse_ops("no json here") == []
    assert LessonBook._parse_ops('{"ops": "not-a-list"}') == []


def test_reflect_applies_ops_and_renders(tmp_path):
    j = Journal(tmp_path / "j.db")
    # seed 6 settlements so reflection engages
    for i in range(6):
        j.db.execute(
            "INSERT INTO settlements VALUES (?, ?, 'kalshi', ?, 'climate and weather', "
            "'yes', 1.0, 0.6, 0.4)",
            (f"M{i}", f"2026-07-0{i + 1}T00:00:00+00:00", f"Will Miami high be >9{i}°?"),
        )
    j.db.commit()

    def fake_llm(system, user):
        assert "Active rules" in user and "Recent settlements" in user
        return json.dumps({"ops": [{"op": "add", "scope": "miami",
                                    "text": "Miami runs hot — shade up",
                                    "reason": "6 green settlements"}]})

    b = book(tmp_path)
    rendered = b.reflect(j, fake_llm)
    assert "R1 [miami]: Miami runs hot — shade up" in rendered
    assert "Track record: 6 settled" in rendered


def test_rule_track_flags_negative_scope(tmp_path):
    j = Journal(tmp_path / "j.db")
    b = book(tmp_path)
    b.apply_ops([{"op": "add", "text": "fade denver", "scope": "denver"}])
    for i in range(12):
        j.db.execute(
            "INSERT INTO settlements VALUES (?, '2099-01-01T00:00:00+00:00', 'kalshi', "
            "'Will the high temp in Denver be >90°?', 'climate and weather', 'no', "
            "0, 0.5, -0.5)", (f"D{i}",))
    j.db.commit()
    track = b._rules_with_track(j)
    assert "NEGATIVE since adoption" in track
