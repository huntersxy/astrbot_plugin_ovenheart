"""Copy MaiBot's original contact-sheet functions without rewriting their algorithms."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "MaiBot/src/maisaka/builtin_tool/send_emoji.py"
SYMBOLS = {
    "_calculate_grid_shape",
    "_build_placeholder_tile",
    "_build_labeled_tile",
    "_merge_emoji_tiles",
}


def main():
    raw = SOURCE.read_bytes()
    source = raw.decode("utf-8")
    lines = source.splitlines(keepends=True)
    nodes = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in SYMBOLS
    ]
    assert len(nodes) == len(SYMBOLS)
    text = '''"""Original MaiBot emoji contact-sheet functions; see emoji_source.json and NOTICE.md."""
from __future__ import annotations
import math
from io import BytesIO
from typing import List
from PIL import Image as PILImage, ImageDraw, ImageFont

_EMOJI_CANDIDATE_TILE_SIZE = 256
_EMOJI_INDEX_BADGE_SIZE = 64
_EMOJI_INDEX_FONT_SIZE = 32
'''
    text += (
        "\n\n"
        + "\n\n".join(
            "".join(lines[node.lineno - 1 : node.end_lineno]) for node in nodes
        )
        + "\n"
    )
    target = ROOT / "vendor/maibot/emoji_tiles.py"
    target.write_text(text, encoding="utf-8")
    (target.parent / "emoji_source.json").write_text(
        json.dumps(
            {
                "commit": "5c7637e6913c84d1c3d5e742fa9e95a7e70d3501",
                "source": "src/maisaka/builtin_tool/send_emoji.py",
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "ported_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "symbol_ast_sha256": {
                    node.name: hashlib.sha256(
                        ast.dump(node, include_attributes=False).encode()
                    ).hexdigest()
                    for node in nodes
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
