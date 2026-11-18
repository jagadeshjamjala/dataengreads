import subprocess
import sys
from pathlib import Path

from dataengreads.cli import COMMANDS, main

ROOT = Path(__file__).resolve().parent.parent


def test_help_lists_every_command():
    out = subprocess.run([sys.executable, "-m", "dataengreads", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0
    for name in COMMANDS:
        assert name in out.stdout


def test_stub_command_runs(capsys):
    assert main(["collect"]) == 0
    assert "not implemented" in capsys.readouterr().out
