import importlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def tasks(folder, *items):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "scheduled-tasks.json").write_text(json.dumps({"scheduledTasks": list(items)}))


def read(folder):
    return {t["id"]: t["enabled"] for t in json.loads((folder / "scheduled-tasks.json").read_text())["scheduledTasks"]}


def test_recurring_routines_follow_the_switch(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("app_switch", None)
    mod = importlib.import_module("app_switch")
    monkeypatch.setattr(mod, "SESSIONS", tmp_path)
    source, target = tmp_path / "source/org", tmp_path / "target/org"
    tasks(source,
          {"id": "review", "displayName": "daily-report", "cronExpression": "0 8 * * *", "enabled": True, "lastRunAt": "2026-01-01"},
          {"id": "new", "cronExpression": "0 9 * * *", "enabled": True},
          {"id": "once", "fireAt": 1, "enabled": True})
    tasks(target, {"id": "review", "displayName": "Daily report", "cronExpression": "0 7 * * *", "enabled": False, "lastRunAt": "2026-01-02"})

    assert len(mod.move_routines("target/org", dry=True)) == 2
    assert read(target) == {"review": False}  # a dry run writes nothing

    mod.move_routines("target/org", dry=False)
    assert read(target) == {"review": True, "new": True}
    assert read(source) == {"review": False, "new": False, "once": True}  # one-shot tasks stay put
    moved = {t["id"]: t for t in json.loads((target / "scheduled-tasks.json").read_text())["scheduledTasks"]}["review"]
    # The definition travels (a rename, a new schedule); the target keeps its newer last run.
    assert (moved["displayName"], moved["cronExpression"], moved["lastRunAt"]) == ("daily-report", "0 8 * * *", "2026-01-02")


def test_layout_follows_the_switch(tmp_path, monkeypatch):
    import plyvel
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("app_switch", None)
    mod = importlib.import_module("app_switch")
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    src, dst = tmp_path / "src", tmp_path / "dst"
    for p, panes in ((src, "left"), (dst, "old")):
        (p / "Local Storage/leveldb").mkdir(parents=True)
        db = plyvel.DB(str(p / "Local Storage/leveldb"), create_if_missing=True)
        db.put(mod.LS_PREFIX + b"desktop-frame.paneStore.v1", b"\x01" + panes.encode())
        db.put(mod.LS_PREFIX + b"dframe-store", b"\x01" + p.name.encode())
        db.close()
        (p / "claude_desktop_config.json").write_text(json.dumps(
            {"preferences": {"epitaxyPrefs": {"desktop-frame.paneStore.v1": panes, "groups": p.name}}}))
    (src / "session-popouts.json").write_text('{"popouts": ["remote-cli"]}')

    mod.carry_layout(src, dst, dry=False)
    db = plyvel.DB(str(dst / "Local Storage/leveldb"))
    assert db.get(mod.LS_PREFIX + b"desktop-frame.paneStore.v1") == b"\x01left"
    assert db.get(mod.LS_PREFIX + b"dframe-store") == b"\x01dst"  # groups stay the account's own
    db.close()
    prefs = json.loads((dst / "claude_desktop_config.json").read_text())["preferences"]["epitaxyPrefs"]
    assert prefs == {"desktop-frame.paneStore.v1": "left", "groups": "dst"}
    assert (dst / "session-popouts.json").read_text() == '{"popouts": ["remote-cli"]}'


def test_restart_keeps_the_panes_exports(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("app_switch", None)
    mod = importlib.import_module("app_switch")
    start = '"zsh -ic \'export TOKEN=\\"\\${A:-\\$B}\\"; claude -n x --continue \'\\"\'\\"\'go\'\\"\'\\"\'; exec zsh -il\'"'

    class Out:
        stdout = start
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Out)
    assert mod.shell_prefix("%0") == 'export TOKEN="${A:-$B}"; '
    Out.stdout = "\"claude -n memory 'go'; exec zsh -il\""
    assert mod.shell_prefix("%1") == ""


def test_running_sessions_are_not_woken_again(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("app_switch", None)
    mod = importlib.import_module("app_switch")
    monkeypatch.setattr(mod, "SESSIONS", tmp_path)
    folder = tmp_path / "acct/org"
    folder.mkdir(parents=True)
    (folder / "local_a.json").write_text(json.dumps({"cliSessionId": "aaaa-1"}))
    (folder / "local_b.json").write_text(json.dumps({"cliSessionId": "bbbb-2"}))

    class Out:
        stdout = "/x/claude --output-format stream-json --resume=aaaa-1 -p\n/bin/zsh\n"
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: Out)
    assert mod.running_sessions() == {"local_a"}


def test_open_panes_persist_without_activity_filter(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import app_switch as mod
    import local_storage
    monkeypatch.setattr(mod, "AWAKE", tmp_path / "awake.json")
    monkeypatch.setattr(mod, "SESSIONS", tmp_path / "sessions")
    folder = tmp_path / "sessions/acct/org"
    folder.mkdir(parents=True)
    for sid in ("local_a", "local_idle"):
        (folder / f"{sid}.json").write_text(json.dumps({"title": sid}))
    store = {"state": {"lastPrimaryCodeSession": {"id": "local_a"},
                       "extraPanesByMode": {"code": [{"ref": {"id": "local_idle"}}]}}}
    (tmp_path / "claude_desktop_config.json").write_text(json.dumps(
        {"preferences": {"epitaxyPrefs": {mod.LAYOUT_KEYS[0]: store}}}))
    class DB:
        def get(self, key): return None
        def close(self): pass
    monkeypatch.setattr(local_storage, "open_copy", lambda _: DB())
    assert mod.remember_awake(tmp_path, False) == {"local_a", "local_idle"}
    assert set(json.loads(mod.AWAKE.read_text())["sessions"]) == {"local_a", "local_idle"}


def test_wake_names_pick_live_sessions(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("app_switch", None)
    mod = importlib.import_module("app_switch")
    monkeypatch.setattr(mod, "SESSIONS", tmp_path)
    for scope, sid, title, at, archived in [("other/org", "local_new", "api-fix", 2, False),
                                            ("target/org", "local_old", "api-fix", 1, False),
                                            ("other/org", "local_gone", "notes", 3, True),
                                            ("other/org", "local_os", "notes", 1, False)]:
        (tmp_path / scope).mkdir(parents=True, exist_ok=True)
        (tmp_path / scope / f"{sid}.json").write_text(
            json.dumps({"title": title, "lastActivityAt": at, "isArchived": archived}))
    assert mod.wake_choice("api-fix, notes") == {"local_new", "local_os"}  # newest live one per title
    assert mod.wake_choice("old") == {"local_old"}  # a local id, with or without its prefix
    try:
        mod.wake_choice("missing")
    except SystemExit as error:
        assert "missing" in str(error)
    else:
        raise AssertionError("an unknown name must stop the switch")



def test_port_groups_keeps_target_ids_and_target_only_members(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("port_groups", None)
    mod = importlib.import_module("port_groups")

    def profile(name, scope, groups, assignments):
        d = tmp_path / name
        d.mkdir()
        sections = {"sections": [{"id": "pinned"}] + [{"id": g["id"], "name": g["name"]} for g in groups]}
        (d / "claude_desktop_config.json").write_text(json.dumps({"preferences": {"epitaxyPrefs": {
            "dframe-group-scopes": {scope: {"groups": groups, "assignments": assignments}},
            "dframe-code-sections": {scope: sections}}}}))
        return d

    src = profile("src", "a/o", [{"id": "cg-s1", "name": "Rel"}, {"id": "cg-s2", "name": "New"}],
                  {"code:local_x": "cg-s1", "code:local_y": "cg-s2"})
    dst = profile("dst", "b/o", [{"id": "cg-t1", "name": "Rel"}, {"id": "cg-t9", "name": "Gone"}],
                  {"code:local_x": "cg-t9", "code:local_only": "cg-t1", "code:local_lost": "cg-t9"})
    groups, sections, names = mod.plan(src, "a/o", dst, "b/o", {"local_x", "local_y"})
    ids = [g["id"] for g in groups["groups"]]
    assert ids[0] == "cg-t1" and ids[1].startswith("cg-") and ids[1] != "cg-s2"
    assert groups["assignments"] == {"code:local_x": "cg-t1", "code:local_y": ids[1], "code:local_only": "cg-t1"}
    assert [s["id"] for s in sections["sections"]] == ["pinned", *ids]


def test_the_left_accounts_sidebar_wins(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("app_switch", None)
    mod = importlib.import_module("app_switch")
    monkeypatch.setattr(mod, "SESSIONS", tmp_path)
    left, stale, target = tmp_path / "source/org", tmp_path / "target/org", tmp_path / "other/org"
    for folder in (left, stale, target):
        folder.mkdir(parents=True)

    def put(folder, sid, **fields):
        (folder / f"local_{sid}.json").write_text(json.dumps({"title": sid, "lastActivityAt": 1, **fields}))

    put(left, "old", isArchived=True, lastActivityAt=1)
    put(stale, "old", isArchived=False, lastActivityAt=5)  # newer, but on an account not left
    put(target, "old", isArchived=False, lastActivityAt=5)
    put(left, "renamed", title="new name")
    put(target, "renamed", title="old name")
    put(target, "gone")
    (left / "deleted_gone").write_text("")
    put(stale, "only-there", lastActivityAt=9)

    mod.copy_missing("other/org", dry=False, src_scope="source/org")
    got = {p.stem[6:]: json.loads(p.read_text()) for p in target.glob("local_*.json")}
    assert got["old"]["isArchived"] is True  # archived where it was left
    assert got["renamed"]["title"] == "new name"
    assert got["gone"]["isArchived"] is True  # deleted on the account left
    assert "only-there" in got  # a live session the left account lacks still comes
    assert json.loads((target / "archived-sessions.idx").read_text())["archived"] == ["local_gone", "local_old"]
