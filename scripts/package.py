"""Build an installable ZIP using an explicit allowlist of release files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
destination = root / "dist" / "astrbot_plugin_persona-v0.1.0.zip"
destination.parent.mkdir(exist_ok=True)
files = [
    *root.glob("*.py"),
    root / "metadata.yaml",
    root / "_conf_schema.json",
    root / "requirements.txt",
    root / "README.md",
    root / "LICENSE",
    root / "CHANGELOG.md",
]
for directory in ("views", "docs", ".astrbot-plugin"):
    files.extend(p for p in (root / directory).rglob("*") if p.is_file())
with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, path.relative_to(root).as_posix())
with ZipFile(destination) as archive:
    assert archive.testzip() is None
print(destination)
