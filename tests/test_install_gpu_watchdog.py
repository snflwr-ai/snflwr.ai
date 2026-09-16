"""scripts/install_gpu_watchdog.sh must actually schedule the GPU watchdog.

The watchdog existed for months while nothing on a docker deploy ever ran it, so an
apt daily-upgrade daemon-reload left the tutor on CPU for ~6 hours unnoticed. These
tests drive the installer against a fake `crontab` backed by a temp file, and check
that deploy.sh invokes it -- a watchdog that is never scheduled is the original bug.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "scripts" / "install_gpu_watchdog.sh"
TAG = "# snflwr-gpu-watchdog (managed by scripts/install_gpu_watchdog.sh)"

EXISTING = (
    'MAILTO=""\n'
    "# WorldMonitor: refresh feeds\n"
    "0 4 * * * /opt/worldmonitor/run-seeders.sh >> /tmp/seed.log 2>&1\n"
)


@pytest.fixture
def fake_crontab(tmp_path):
    table = tmp_path / "crontab.txt"
    fake = tmp_path / "crontab"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'TABLE="{table}"\n'
        'if [ "$1" = "-l" ]; then\n'
        '  [ -f "$TABLE" ] || { echo "no crontab for user" >&2; exit 1; }\n'
        '  cat "$TABLE"\n'
        'elif [ "$1" = "-" ]; then\n'
        '  cat > "$TABLE"\n'
        "fi\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake, table


def run(fake, **env):
    e = {**os.environ, "CRONTAB_CMD": str(fake), **env}
    return subprocess.run(
        ["bash", str(INSTALLER)], capture_output=True, text=True, env=e, timeout=30
    )


def watchdog_lines(text):
    return [
        l for l in text.splitlines() if "gpu_watchdog.sh" in l and not l.startswith("#")
    ]


def test_installs_into_an_empty_crontab(fake_crontab):
    fake, table = fake_crontab
    r = run(fake)
    assert r.returncode == 0, r.stderr
    text = table.read_text()
    assert text.count(TAG) == 1
    (line,) = watchdog_lines(text)
    assert line.startswith("*/2 * * * * ")
    assert str(REPO / "scripts" / "gpu_watchdog.sh") in line
    assert str(REPO / "logs" / "gpu_watchdog.log") in line
    # cron's PATH is minimal; docker and nvidia-smi must be reachable
    assert "PATH=/usr/local/bin:/usr/bin:/bin" in line


def test_preserves_existing_entries(fake_crontab):
    fake, table = fake_crontab
    table.write_text(EXISTING)
    assert run(fake).returncode == 0
    text = table.read_text()
    for original in EXISTING.strip().splitlines():
        assert original in text
    assert len(watchdog_lines(text)) == 1


def test_is_idempotent(fake_crontab):
    fake, table = fake_crontab
    table.write_text(EXISTING)
    for _ in range(3):
        assert run(fake).returncode == 0
    text = table.read_text()
    assert text.count(TAG) == 1
    assert len(watchdog_lines(text)) == 1
    assert text.count("run-seeders.sh") == 1


def test_off_removes_only_the_managed_entry(fake_crontab):
    fake, table = fake_crontab
    table.write_text(EXISTING)
    assert run(fake).returncode == 0
    r = run(fake, SNFLWR_GPU_WATCHDOG="off")
    assert r.returncode == 0, r.stderr
    text = table.read_text()
    assert TAG not in text
    assert watchdog_lines(text) == []
    assert "run-seeders.sh" in text


def test_missing_crontab_fails_loudly(tmp_path):
    r = run(tmp_path / "no-such-crontab")
    assert r.returncode != 0
    assert "schedule it manually" in r.stderr


def test_deploy_sh_schedules_the_watchdog():
    """A watchdog nothing runs is the defect this file exists for."""
    deploy = (REPO / "deploy.sh").read_text()
    assert "scripts/install_gpu_watchdog.sh" in deploy
