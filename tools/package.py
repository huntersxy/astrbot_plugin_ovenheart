"""Build the runtime artifact; developer files never enter the install ZIP."""

import ast
import re
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_FILES = (
    "__init__.py",
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "logo.png",
)
RUNTIME_TREES = {
    "core": {".py"},
    "chat": {".py"},
    "memory": {".py"},
    "integrations": {".py"},
    "views": {".html", ".js", ".css"},
    ".astrbot-plugin": {".json"},
    "vendor": {".py", ".md", ".txt", ".json"},
}


def validate_logger_imports(source, filename):
    for node in ast.walk(ast.parse(source, filename=filename)):
        invalid = False
        if isinstance(node, ast.Import):
            invalid = any(alias.name.split(".")[0] == "logging" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            invalid = (node.module or "").split(".")[0] == "logging" or any(
                alias.name in {"get_logger", "getLogger"} for alias in node.names
            )
            if any(alias.name == "logger" for alias in node.names):
                invalid = invalid or not (
                    node.module == "astrbot.api"
                    and not node.level
                    and len(node.names) == 1
                    and node.names[0].asname is None
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            invalid = node.name in {"get_logger", "getLogger"}
        elif isinstance(node, ast.Call):
            invalid = (
                isinstance(node.func, ast.Name)
                and node.func.id in {"get_logger", "getLogger"}
            ) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"get_logger", "getLogger"}
            )
        if invalid:
            raise ValueError(
                f"{filename}:{node.lineno}: logger must use from astrbot.api import logger"
            )


def package():
    tracked = set(
        subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
        .decode("utf-8")
        .split("\0")
    )
    version = re.search(
        r"^version:\s*(\S+)",
        (ROOT / "metadata.yaml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )[1]
    destination = ROOT / "agent" / "dist" / f"astrbot_plugin_ovenheart-v{version}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = [ROOT / name for name in RUNTIME_FILES]
    if missing := set(RUNTIME_FILES) - tracked:
        raise ValueError(f"Required release files are not tracked: {sorted(missing)}")
    for directory, extensions in RUNTIME_TREES.items():
        files.extend(
            p
            for p in (ROOT / directory).rglob("*")
            if p.is_file()
            and p.suffix in extensions
            and "__pycache__" not in p.parts
            and p.relative_to(ROOT).as_posix() in tracked
        )
    for path in files:
        if path.suffix == ".py":
            validate_logger_imports(path.read_bytes(), path.relative_to(ROOT).as_posix())
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
    return destination


if __name__ == "__main__":
    print(package())
