"""A release must import with the real SDK after extraction, without checkout files."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


def test_release_rejects_noncanonical_logger_imports():
    from tools.package import validate_logger_imports

    validate_logger_imports("from astrbot.api import logger\nlogger.info('ok')", "valid.py")
    for source in (
        "import logging",
        "from logging import getLogger",
        "from .host import logger",
        "from .host import get_logger",
        "from astrbot.api import AstrBotConfig, logger",
        "from astrbot.api import logger as log",
        "logger = host.get_logger('vendor')",
        "def get_logger(name): return None",
    ):
        with pytest.raises(ValueError, match="from astrbot.api import logger"):
            validate_logger_imports(source, "vendor/rejected.py")


def test_release_imports_without_modules_from_the_source_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    built = subprocess.run(
        [sys.executable, str(root / "tools/package.py")],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    archive = Path(built.stdout.strip())
    extracted = tmp_path / "release"
    with zipfile.ZipFile(archive) as package:
        package.extractall(extracted)
    code = """
import importlib, importlib.util, sys
spec = importlib.util.spec_from_file_location('release_under_test', sys.argv[1]+'/__init__.py', submodule_search_locations=[sys.argv[1]])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
main = importlib.import_module(spec.name+'.main')
assert main.PersonaPlugin
planner = importlib.import_module(spec.name+'.chat.planner')
assert planner.ConversationPlanner
people = importlib.import_module(spec.name+'.memory.person_memory')
assert people.PersonMemory
retrieval = importlib.import_module(spec.name+'.vendor.maibot.amemorix.retrieval')
assert retrieval.DualPathRetriever
summary = importlib.import_module(spec.name+'.memory.memory_summary')
assert summary.NativeSummaryImporter
kernel = importlib.import_module(spec.name+'.vendor.maibot.amemorix.runtime.sdk_memory_kernel')
assert kernel.SDKMemoryKernel
native = importlib.import_module(spec.name+'.memory.native_memory_kernel')
assert native.NativeMemoryKernel
midterm = importlib.import_module(spec.name+'.memory.midterm')
assert midterm.MidTermMemory(None).template
emoji = importlib.import_module(spec.name+'.memory.emoji_actions')
assert emoji.EmojiActions
images = importlib.import_module(spec.name+'.memory.image_memory')
assert images.ImageMemory
rhythm = importlib.import_module(spec.name+'.chat.reply_rhythm')
assert rhythm.ReplyRhythm
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(extracted)],
        cwd=tmp_path,
        env=os.environ | {"ASTRBOT_ROOT": str(tmp_path / "host")},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
