"""Exercise command capture through an isolated real host and plugin loader."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("astrbot.core.core_lifecycle")
ROOT = Path(__file__).resolve().parents[1]


async def test_native_core_never_learns_a_stripped_builtin_command(tmp_path):
    output = tmp_path / "native-smoke.json"
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            str(ROOT / "scripts/evaluate_native.py"),
            "--smoke",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["phase"] == "complete"
    assert len(report["stages"]) == 9
    command = report["events"][0]
    assert command["input"] == "/sid" and command["pipeline_text"] == "sid"
    assert command["replies"] and "UMO" in command["replies"][0]
    assert report["command_pending"] == 0
