"""Build the runtime artifact; developer files never enter the install ZIP."""

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
    destination = ROOT / "dist" / f"astrbot_plugin_ovenheart-v{version}.zip"
    destination.parent.mkdir(exist_ok=True)
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
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
    return destination


if __name__ == "__main__":
    print(package())
