import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import app_switch as app


def test_open_panes_include_idle_and_paused_without_process_lookup():
    store = {'state': {'lastPrimaryCodeSession': {'id': 'local_primary'},
                       'extraPanesByMode': {'code': [{'ref': {'id': 'local_idle'}},
                                                    {'ref': {'id': 'local_paused'}}]}}}
    assert app.pane_ids(store) == {'local_primary', 'local_idle', 'local_paused'}


def test_unknown_layout_does_not_wake_everything():
    with pytest.raises(ValueError, match='unsupported'):
        app.pane_ids({})



def test_terminal_logging_captures_child_output_and_failure(tmp_path, monkeypatch):
    child = tmp_path / 'child.py'
    child.write_text('import sys\nprint("plan")\nprint("failure", file=sys.stderr)\nsys.exit(7)\n')
    monkeypatch.setattr(app, '__file__', str(child))
    monkeypatch.setattr(app, 'STATE_DIR', tmp_path / 'state')
    monkeypatch.delenv('CLAUDE_CODE_ENTRYPOINT', raising=False)
    monkeypatch.delenv('OSTACK_SWITCH_LOG', raising=False)
    assert app.run_cli(['personal']) == 7
    log = next((tmp_path / 'state').glob('switch-*.log'))
    assert all(text in log.read_text() for text in ['plan', 'failure', 'Exit status: 7'])
    assert log.stat().st_mode & 0o777 == 0o600


def test_session_backup_keeps_archive_and_routine_state(tmp_path, monkeypatch):
    source = tmp_path / 'sessions/acct/org'
    source.mkdir(parents=True)
    for name in ['local_a.json', 'scheduled-tasks.json', 'archived-sessions.idx', 'deleted_b']:
        (source / name).write_text('{}')
    monkeypatch.setattr(app, 'SESSIONS', tmp_path / 'sessions')
    monkeypatch.setattr(app, 'STATE_DIR', tmp_path / 'state')
    backup = app.backup_sessions()
    for name in ['local_a.json', 'scheduled-tasks.json', 'archived-sessions.idx', 'deleted_b']:
        assert (backup / 'acct/org' / name).read_bytes() == (source / name).read_bytes()
