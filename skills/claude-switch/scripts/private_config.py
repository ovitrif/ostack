"""Optional machine-local settings; never store account identities or credentials here."""
import os
import sys
from pathlib import Path

import yaml


def load_config():
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = root / "ostack/claude-switch/config.yaml"
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a settings mapping")
    for key in ("wake_text", "waker_cmd", "bundle_prefix"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"{path}: {key} must be text")
    if not isinstance(value.get("forced_prefs", {}), dict):
        raise ValueError(f"{path}: forced_prefs must be a mapping")
    return value


CONFIG = load_config()
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "ostack/claude-switch"

if __name__ == "__main__":
    if sys.argv[1:] == ["bundle_prefix"]:
        print(CONFIG.get("bundle_prefix", "local"))
    else:
        sys.exit("usage: private_config.py bundle_prefix")
