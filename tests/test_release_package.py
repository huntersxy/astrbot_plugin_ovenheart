"""A release must import with the real SDK after extraction, without checkout files."""

import os
import subprocess
import sys
import zipfile
from pathlib import Path


def test_release_imports_without_modules_from_the_source_checkout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    built = subprocess.run(
        [sys.executable, str(root / "scripts/package.py")],
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
planner = importlib.import_module(spec.name+'.planner')
assert planner.ConversationPlanner
people = importlib.import_module(spec.name+'.person_memory')
assert people.PersonMemory
retrieval = importlib.import_module(spec.name+'.vendor.maibot.amemorix.retrieval')
assert retrieval.DualPathRetriever
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
