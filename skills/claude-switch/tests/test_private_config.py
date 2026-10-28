import importlib.util
from pathlib import Path

import pytest


def load(monkeypatch, tmp_path, text=None):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    if text is not None:
        path = tmp_path / "ostack/claude-switch/config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(text)
    spec = importlib.util.spec_from_file_location("isolated_config", Path(__file__).parents[1] / "scripts/private_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_defaults_have_no_personal_preferences(monkeypatch, tmp_path):
    assert load(monkeypatch, tmp_path).CONFIG == {}


def test_private_settings_loaded_without_shell_exports(monkeypatch, tmp_path):
    config = load(monkeypatch, tmp_path, 'wake_text: "resume {email}"\nwaker_cmd: "notify worker"\nforced_prefs:\n  example: false\n').CONFIG
    assert config["wake_text"] == "resume {email}"
    assert config["forced_prefs"] == {"example": False}


def test_malformed_config_fails_before_switch(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="forced_prefs"):
        load(monkeypatch, tmp_path, "forced_prefs: false\n")


def test_app_handoff_forwards_config_location_to_existing_tmux(monkeypatch, tmp_path):
    import importlib
    import sys
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CLAUDE_APP_WAKE_TEXT", "resume {email}")
    sys.modules.pop("app_switch", None)
    module = importlib.import_module("app_switch")
    monkeypatch.setattr(module, "STATE_DIR", tmp_path / "state")
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append(args))
    with pytest.raises(SystemExit, match="started in tmux"):
        module.main(["target"])
    assert len(calls) == 1
    assert f"XDG_CONFIG_HOME={tmp_path / 'config'}" in calls[0]
    assert "CLAUDE_APP_WAKE_TEXT=resume {email}" in calls[0]
    assert "env -u CLAUDE_CODE_ENTRYPOINT" in calls[0][-1]
