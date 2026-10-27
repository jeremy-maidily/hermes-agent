"""DIAGNOSTIC ONLY (wine2e/winstage-diag, never merges): where does the child stage get lost?"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.windows_handoff_support import _log
from tests.waiting import wait_until

ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = ROOT / "scripts/desktop-update/windows.ps1"
DIAG_CLI = """
import os, sys, json, time, tempfile
from pathlib import Path
def main():
    if '--version' in sys.argv:
        print('Install directory: ' + str(Path(__file__).resolve().parents[1])); return 0
    if '--help' in sys.argv:
        print('--keep-stash'); return 0
    if sys.argv[1:2] == ['update']:
        t0 = time.monotonic()
        sys.path.insert(0, os.environ['HANDOFF_REPO'])
        for name in [m for m in sys.modules if m == 'hermes_cli' or m.startswith('hermes_cli.')]:
            del sys.modules[name]
        from hermes_cli import update_stage
        rec = {'env': os.environ.get('HERMES_UPDATE_STATUS_FILE'), 'import_s': time.monotonic() - t0}
        target = update_stage.status_file()
        rec['target'] = str(target)
        rec['pre_exists'] = bool(target and target.exists())
        try:
            fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix='.update-stage-')
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                fh.write(json.dumps({'status': 'running', 'message': 'Packaging the desktop app'}))
            os.replace(tmp, target)
            rec['replace'] = 'ok'
        except OSError as exc:
            rec['replace'] = repr(exc) + ' winerror=' + str(getattr(exc, 'winerror', None))
        rec['exists_after'] = bool(target and target.exists())
        log = Path(os.environ['HERMES_HOME']) / 'logs' / 'desktop-update-handoff.log'
        deadline = time.monotonic() + 60
        rec['relayed_after_s'] = None
        samples = []
        while time.monotonic() < deadline:
            try:
                text = log.read_text(encoding='utf-8-sig')
            except OSError as exc:
                samples.append('read:' + type(exc).__name__)
                text = ''
            if 'stage: Packaging the desktop app' in text:
                rec['relayed_after_s'] = round(time.monotonic() - t0, 2)
                break
            if len(samples) < 5:
                samples.append('exists=%s' % target.exists())
            time.sleep(0.05)
        rec['samples'] = samples
        rec['exists_end'] = target.exists()
        Path(os.environ['HANDOFF_DIAG']).write_text(json.dumps(rec), encoding='utf-8')
    return 0
if __name__ == '__main__':
    sys.exit(main())
"""


@pytest.mark.platforms("windows")
def test_diag(tmp_path: Path) -> None:
    install = tmp_path / "checkout"
    publish_fixture_launcher(install, DIAG_CLI)
    home = tmp_path / "home"
    home.mkdir()
    diag = tmp_path / "diag.json"
    script = subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
         "-InstallRoot", str(install), "-NoUi"],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "HERMES_HOME": str(home), "HERMES_RUNTIME_DIR": str(tmp_path / "empty-store"),
             "HANDOFF_DIAG": str(diag), "HANDOFF_REPO": str(ROOT)},
    )
    try:
        script.wait(timeout=200)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(script.pid)], capture_output=True, check=False)
        script.wait(timeout=30)
    rec = json.loads(diag.read_text(encoding="utf-8")) if diag.exists() else {"diag": "missing"}
    out = ROOT / "diag-out"
    out.mkdir(exist_ok=True)
    (out / (__import__("uuid").uuid4().hex + ".json")).write_text(json.dumps(rec), encoding="utf-8")
    assert rec.get("relayed_after_s") is not None, "DIAG " + json.dumps(rec) + "\n" + _log(home)[-2500:]
