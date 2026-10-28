#!/usr/bin/env python3
"""Read and write the Claude desktop app's Local Storage (Chromium leveldb).

Reads work while the app runs (they read a copy); `put` needs the app quit, since
the app holds the store open and rewrites it on quit.
  list [substring]        keys of the https://claude.ai origin, with value sizes
  get <key>               the value as JSON (or raw text when it is not JSON)
  put <key> <file|->      write the value from a JSON or text file, after a backup
Needs `uv run --with plyvel-ci python local_storage.py ...`.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import plyvel

APP_SUPPORT = Path(os.environ.get("CLAUDE_APP_SUPPORT", Path.home() / "Library/Application Support/Claude"))
from private_config import STATE_DIR as STATE
PREFIX = b"_https://claude.ai\x00\x01"


def db_path():
    return APP_SUPPORT / "Local Storage" / "leveldb"


def decode(raw):
    # Chromium stores a localStorage string as 0x01 + Latin-1 or 0x00 + UTF-16LE.
    return raw[1:].decode("latin-1" if raw[:1] == b"\x01" else "utf-16-le")


def encode(text):
    try:
        return b"\x01" + text.encode("latin-1")
    except UnicodeEncodeError:
        return b"\x00" + text.encode("utf-16-le")


class SnapshotDB:
    """Close the database and remove its private temporary copy together."""
    def __init__(self, db, directory):
        self.db = db
        self.directory = directory

    def __getattr__(self, name):
        return getattr(self.db, name)

    def close(self):
        try:
            self.db.close()
        finally:
            shutil.rmtree(self.directory, ignore_errors=True)


def open_copy(app=None):
    """Read a temporary snapshot while the app holds its database lock."""
    tmp = Path(tempfile.mkdtemp(prefix="claude-ls-"))
    try:
        shutil.copytree((app / "Local Storage/leveldb") if app else db_path(), tmp / "leveldb", ignore=shutil.ignore_patterns("LOCK"))
        return SnapshotDB(plyvel.DB(str(tmp / "leveldb")), tmp)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def main(argv):
    if not argv or argv[0] not in ("list", "get", "put"):
        sys.exit(__doc__)
    db = plyvel.DB(str(db_path())) if argv[0] == "put" else open_copy()
    try:
        if argv[0] == "list":
            needle = argv[1] if len(argv) > 1 else ""
            for key, raw in db.iterator(prefix=PREFIX):
                name = key[len(PREFIX):].decode("utf-8", "replace")
                if needle in name:
                    print(f"{name}\t{len(raw)}")
        elif argv[0] == "get":
            raw = db.get(PREFIX + argv[1].encode())
            if raw is None:
                sys.exit(f"no key {argv[1]!r}")
            text = decode(raw)
            try:
                print(json.dumps(json.loads(text), indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(text)
        else:
            text = sys.stdin.read() if argv[2] == "-" else Path(argv[2]).read_text()
            try:
                text = json.dumps(json.loads(text), separators=(",", ":"), ensure_ascii=False)
            except json.JSONDecodeError:
                text = text.rstrip("\n")
            db.close()
            backup = STATE / f"local-storage-{datetime.now():%Y%m%d-%H%M%S-%f}"
            shutil.copytree(db_path(), backup)
            db = plyvel.DB(str(db_path()))
            db.put(PREFIX + argv[1].encode(), encode(text))
            print(f"wrote {argv[1]}; backup: {backup}")
    finally:
        db.close()


if __name__ == "__main__":
    main(sys.argv[1:])
