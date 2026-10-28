import importlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SCOPE = "aaaa/1111"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_APP_SUPPORT", str(tmp_path))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("local_storage", "sidebar_state"):
        sys.modules.pop(name, None)
    folder = tmp_path / "claude-code-sessions" / SCOPE
    folder.mkdir(parents=True)
    prefs = {"dframe-group-scopes": {SCOPE: {
        "groups": [{"id": "cg-1", "name": "Work"}],
        "assignments": {"code:local_a": "cg-1", "code:local_gone": "cg-1"},
        "order": {"cg-1": ["code:local_a", "code:local_gone"]}}}}
    (tmp_path / "claude_desktop_config.json").write_text(json.dumps({"preferences": {"epitaxyPrefs": prefs}}))
    return tmp_path, folder, importlib.import_module("sidebar_state")


def record(folder, sid, **fields):
    (folder / f"{sid}.json").write_text(json.dumps({"sessionId": sid, **fields}))


def test_reports_what_the_old_reader_skipped(app):
    _, folder, mod = app
    record(folder, "local_a", title="same", isArchived=False)
    record(folder, "local_b", title="same", isArchived=False)
    (folder / "local_bad.json").write_text("{not json")
    (folder / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": ["local_b"]}))
    scope = mod.read_all("aaaa")[SCOPE]
    assert set(scope["sessions"]) == {"local_a", "local_b"}  # equal titles stay apart by id
    assert scope["sessions"]["local_b"]["archived"] is True
    assert scope["sessions"]["local_a"]["group"] == "Work"
    kinds = {next(iter(p)) for p in scope["problems"]}
    assert {"unparsable", "member_without_record", "archive_index_disagrees"} <= kinds


def test_diff_names_missing_and_different_sessions_by_id(app, tmp_path):
    _, folder, mod = app
    record(folder, "local_a", title="x", cwd="/r", isArchived=False)
    inventory = tmp_path / "inventory.yaml"
    import yaml
    other = {"sessions": {"local_a": {"title": "x", "cwd": "/r", "archived": True, "group": "Work"},
                          "local_c": {"title": "y", "cwd": "/r", "archived": False, "group": None}},
             "groups": ["Work"]}
    inventory.write_text(yaml.safe_dump({"scopes": {SCOPE: other}}))
    result = mod.diff(mod.side(f"{inventory}:aaaa"), mod.side("aaaa"))
    assert result["only_a"] == {"local_c": "y"}
    assert result["different"]["local_a"]["archived"] == [True, False]
    assert mod.main(["diff", f"{inventory}:aaaa", "aaaa"]) == 1


def test_switch_copies_newer_and_live_records_only(app, tmp_path, monkeypatch):
    root, folder, _ = app
    switch = importlib.import_module("app_switch")
    monkeypatch.setattr(switch, "SESSIONS", root / "claude-code-sessions")
    target = root / "claude-code-sessions" / "bbbb" / "2222"
    target.mkdir(parents=True)
    (target / "archived-sessions.idx").write_text(json.dumps({"v": 1, "archived": []}))
    record(folder, "local_new", title="live", lastActivityAt=2)
    record(folder, "local_old", title="archived", lastActivityAt=2, isArchived=True)
    record(folder, "local_both", title="newer", lastActivityAt=5, isArchived=True)
    record(target, "local_both", title="older", lastActivityAt=1)
    record(folder, "local_gone", title="deleted", lastActivityAt=9)
    (target / "deleted_gone").write_text("")
    copied = switch.copy_missing("bbbb/2222", dry=False)
    assert sorted(c.split(" (")[0] for c in copied) == ["live", "newer"]
    assert json.loads((target / "archived-sessions.idx").read_text())["archived"] == ["local_both"]


def test_wake_list_skips_archived_and_routine_runs(app):
    switch = importlib.import_module("app_switch")
    side = {"routines": [{"displayName": "Daily review"}],
            "sessions": {"local_a": {"title": "work", "archived": False},
                         "local_b": {"title": "old", "archived": True},
                         "local_c": {"title": "Daily review", "archived": False, "scheduled_task_id": "routine-a"}}}
    assert switch.wake_list(side) == {"local_a": "work"}


def test_tree_uses_section_order_recency_and_distinct_ids():
    mod = importlib.import_module("sidebar_state")
    state = {"scope": {"groups": ["Second", "First"], "sections": [
        {"kind": "manual", "name": "Second", "sortBy": "recency", "collapsed": True},
        {"kind": "manual", "name": "First", "shown": False}],
        "sessions": {"local_a": {"title": "same", "group": "Second", "archived": False, "lastActivityAt": "2026-01-01"},
                     "local_b": {"title": "same", "group": "Second", "archived": False, "lastActivityAt": "2026-01-02"}},
        "routines": [], "problems": []}}
    text = mod.render_tree(state)
    assert text.index("Second (2) [collapsed]") < text.index("First (hidden)")
    assert text.index("local_b") < text.index("local_a")


def test_group_copy_normalizes_account_specific_ids_and_keeps_manual_order():
    mod = importlib.import_module("sidebar_state")
    def copy(gid):
        return mod.group_copy({"groups": [{"id": gid, "name": "Work"}],
            "assignments": {"code:local_a": gid}, "order": {gid: ["code:local_b", "code:local_a"]}},
            {"sections": [{"id": gid, "kind": "manual", "name": "Work", "sortBy": "manual"}]})
    assert copy("source-group") == copy("target-group")
    assert copy("source-group")["member_order"]["Work"] == ["local_b", "local_a"]


def test_verification_does_not_pass_matching_but_inconsistent_data(app, tmp_path, monkeypatch):
    import yaml
    _, _, mod = app
    side = {"sessions": {}, "groups": [], "problems": [{"pin_copies_disagree": True}]}
    path = tmp_path / "baseline.yaml"
    path.write_text(yaml.safe_dump({"scopes": {SCOPE: side}}))
    monkeypatch.setattr(mod, "read_all", lambda arg: {SCOPE: side})
    assert mod.verify(str(path), "target") == 2


def test_load_app_reads_requested_profiles_database(app, monkeypatch):
    root, _, mod = app
    seen = []
    class DB:
        def get(self, key):
            return None
        def close(self):
            pass
    monkeypatch.setattr(mod, "open_copy", lambda profile: seen.append(profile) or DB())
    mod.load_app(root)
    assert seen == [root]


def test_diff_catches_pins_routines_and_section_settings():
    import copy
    mod = importlib.import_module("sidebar_state")
    before = {"sessions": {"local_a": {"title": "same", "pinned": True}}, "groups": [],
              "routines": [{"id": "daily", "cronExpression": "0 8 * * *"}],
              "sections": [{"kind": "sessions", "sortBy": "recency"}]}
    after = copy.deepcopy(before)
    after["sessions"]["local_a"]["pinned"] = False
    after["routines"][0]["cronExpression"] = "0 9 * * *"
    after["sections"][0]["sortBy"] = "manual"
    result = mod.diff(before, after)
    assert result["different"]["local_a"]["pinned"] == [True, False]
    assert "routines" in result and "sections" in result


def test_tree_nests_helpers_and_omits_private_side_chats():
    mod = importlib.import_module("sidebar_state")
    rows = {
        "local_parent": {"title": "Parent", "group": "Work", "archived": False},
        "local_helper": {"title": "Helper", "group": None, "parent": "local_parent", "archived": False},
        "local_side": {"title": "Aside conversation", "group": None, "parent": "local_parent", "side_chat": True, "archived": False},
    }
    text = mod.render_tree({"scope": {"groups": ["Work"], "sessions": rows, "routines": [], "problems": []}})
    assert "  - Parent  [local_parent]\n    - Helper  [local_helper]" in text
    assert "Aside conversation" not in text
    assert "1 side chat(s); retained in YAML" in text
    assert "Recents (0)" in text


def test_diff_ignores_recency_order_but_preserves_manual_order():
    mod = importlib.import_module("sidebar_state")
    a = {"sessions": {}, "groups": ["Work"], "sections": [{"name": "Work", "sortBy": "recency"}],
         "member_order": {"Work": ["a", "b"]}}
    b = {**a, "member_order": {"Work": ["b", "a"]}}
    assert "member_order" not in mod.diff(a, b)
    a["sections"] = b["sections"] = [{"name": "Work", "sortBy": "manual"}]
    assert "member_order" in mod.diff(a, b)


def test_missing_member_only_archived_elsewhere_is_informational(app):
    root, folder, mod = app
    record(folder, "local_a", title="visible", isArchived=False)
    other = root / "claude-code-sessions/bbbb/2222"
    other.mkdir(parents=True)
    record(other, "local_gone", title="old", isArchived=True)
    scope = mod.read_all("aaaa")[SCOPE]
    assert [p["member_without_record"] for p in scope["ignored_stale_members"]] == ["local_gone"]
    assert not any(p.get("member_without_record") == "local_gone" for p in scope["problems"])
    record(other, "local_gone", title="live elsewhere", isArchived=False)
    scope = mod.read_all("aaaa")[SCOPE]
    assert any(p.get("member_without_record") == "local_gone" for p in scope["problems"])


def test_absent_archived_history_is_not_a_switch_discrepancy(app):
    _, _, mod = app
    before = {"sessions": {"local_old": {"title": "retired", "archived": True},
                           "local_live": {"title": "visible", "archived": False}}}
    after = {"sessions": {}}
    assert mod.diff(before, after)["only_a"] == {"local_live": "visible"}
    assert mod.diff(after, before)["only_b"] == {"local_live": "visible"}
