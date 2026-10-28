import importlib
import json
import sys
from pathlib import Path

import plyvel

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PFX = b"_https://claude.ai\x00\x01"
A, B = ("aaaaaaaa-0000-0000-0000-000000000001", "0aaaaaaa-0000-0000-0000-000000000001"), \
       ("bbbbbbbb-0000-0000-0000-000000000002", "0bbbbbbb-0000-0000-0000-000000000002")


def profile(root, ids, prefs, ls):
    (root / "Local Storage").mkdir(parents=True)
    (root / "claude_desktop_config.json").write_text(json.dumps({"preferences": prefs}))
    db = plyvel.DB(str(root / "Local Storage/leveldb"), create_if_missing=True)
    for k, v in ls.items():
        db.put(PFX + k.encode(), b"\x01" + json.dumps(v).encode("latin-1"))
    db.close()
    return root, ids


def ls(root, key):
    db = plyvel.DB(str(root / "Local Storage/leveldb"))
    raw = db.get(PFX + key.encode())
    db.close()
    return json.loads(raw[1:].decode("latin-1")) if raw else None


def test_every_profile_gets_the_same_settings_under_its_own_account(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("sync_settings", None)
    mod = importlib.import_module("sync_settings")
    monkeypatch.setattr(mod, "STATE", tmp_path / "state")
    monkeypatch.setattr(mod, "FORCED", {"claudeIosSimulatorAccessEnabled": False})
    left = profile(tmp_path / "left", A,
                   {"coworkPreferredBrowser": "built_in", "ccdScheduledTasksEnabled": True, "claudeIosSimulatorAccessEnabled": True,
                    "bypassPermissionsGateByAccount": {A[0]: True},
                    "epitaxyPrefs": {"dframe-group-scopes": {"x": 1}}},
                   {"LSS-persisted.epitaxy-transcript-links-in-preview": {"value": False, "timestamp": 2},
                    f"LSS-persisted.epitaxy-folder-permission-mode.{A[0]}": {"value": {"/projects/demo": "bypassPermissions"}, "timestamp": 5}})
    new = profile(tmp_path / "new", B,
                  {"coworkPreferredBrowser": "chrome", "ccdScheduledTasksEnabled": False,
                   "epitaxyPrefs": {"dframe-group-scopes": {"y": 2}}},
                  {"LSS-persisted.epitaxy-transcript-links-in-preview": {"value": True, "timestamp": 1},
                   f"LSS-persisted.epitaxy-folder-permission-mode.{B[0]}": {"value": {"/projects": "bypassPermissions"}, "timestamp": 1},
                   "LSS-persisted.epitaxy-diff-layout": {"value": "unified", "timestamp": 1}})

    mod.sync([left, new], dry=False, account_ids=[*A, *B])

    for root, ids in (left, new):
        prefs = json.loads((root / "claude_desktop_config.json").read_text())["preferences"]
        assert prefs["coworkPreferredBrowser"] == "built_in" and prefs["ccdScheduledTasksEnabled"] is True
        assert prefs.get("bypassPermissionsGateByAccount", {}) == ({A[0]: True} if ids == A else {})
        assert prefs["claudeIosSimulatorAccessEnabled"] is False  # forced off, whatever the left profile says
        assert prefs["epitaxyPrefs"]["epitaxy-transcript-links-in-preview"] is False  # the newer edit
        assert ls(root, "LSS-persisted.epitaxy-diff-layout")["value"] == "unified"  # only one had it
        assert ls(root, f"LSS-persisted.epitaxy-folder-permission-mode.{ids[0]}")["value"] == (
            {"/projects/demo": "bypassPermissions"} if ids == A else {"/projects": "bypassPermissions"})
    groups = lambda root: json.loads((root / "claude_desktop_config.json").read_text())["preferences"]["epitaxyPrefs"]["dframe-group-scopes"]
    assert groups(left[0]) == {"x": 1} and groups(new[0]) == {"y": 2}  # port_groups owns them
