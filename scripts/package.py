"""Build an installable ZIP using an explicit allowlist of release files."""

import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
version = re.search(
    r"^version:\s*(\S+)",
    (root / "metadata.yaml").read_text(encoding="utf-8"),
    re.MULTILINE,
)[1]
destination = root / "dist" / f"astrbot_plugin_ovenheart-v{version}.zip"
destination.parent.mkdir(exist_ok=True)
files = [
    root / name
    for name in (
        "__init__.py",
        "main.py",
        "auxiliary.py",
        "jev.py",
        "jev_transport.py",
        "direct_priority.py",
        "learning.py",
        "memory_bridge.py",
        "group_context.py",
        "conversation.py",
        "outgoing_media.py",
        "oven_multi.py",
        "page_api.py",
        "participation.py",
        "prompts.py",
        "reply_payload.py",
        "rhythm.py",
        "planner.py",
        "person_memory.py",
        "room_memory.py",
        "memory_embedding.py",
        "image_memory.py",
        "emoji_actions.py",
        "memory_consolidation.py",
        "midterm.py",
        "memory_summary.py",
        "native_memory_kernel.py",
        "retrieval.py",
        "settings.py",
        "store.py",
        "metadata.yaml",
        "logo.png",
        "_conf_schema.json",
        "requirements.txt",
        "README.md",
        "LICENSE",
        "CHANGELOG.md",
        "scripts/evaluate_social_reply.py",
    )
]
for directory, extensions in (
    ("views", {".html", ".js", ".css"}),
    (".astrbot-plugin", {".json"}),
    ("vendor", {".py", ".md", ".txt", ".json"}),
):
    files.extend(
        p
        for p in (root / directory).rglob("*")
        if p.is_file() and p.suffix in extensions
    )
files.extend(root / path for path in (
    "docs/zh/README.md", "docs/en/README.md", "docs/design/reply-context.md",
    "docs/design/social-reply.md",
    "docs/design/conversation-planner.md",
    "docs/design/person-memory.md",
    "docs/design/room-memory.md",
    "docs/design/memory-consolidation.md",
    "docs/design/midterm-memory.md",
    "docs/design/image-memory.md",
    "docs/design/emoji-actions.md",
    "docs/design/native-memory-kernel.md",
    "docs/design/maibot-parity.md",
    "docs/evaluation/jev-review-2026-10-25.md", "docs/evaluation/jev-cases.json",
    "docs/evaluation/jev-benchmark-2026-10-25.json",
))
files.extend(
    root / "docs/assets" / name
    for name in ("profile.jpg", "library-triage-v0.56-desktop.jpg")
)
with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, path.relative_to(root).as_posix())
with ZipFile(destination) as archive:
    assert archive.testzip() is None
print(destination)
