"""Reproduce the pure MaiBot mid-term parser/ranker port from the pinned checkout."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "MaiBot/src/maisaka/memory/mid_term.py"
SYMBOLS = {
    "MidTermMemorySummaryModel",
    "MidTermMemoryRecallCandidate",
    "_parse_summary_response",
    "_load_json_payload",
    "_parse_json_candidate",
    "_coerce_str_list",
    "_normalize_recall_cues",
    "_normalize_recall_cue_text",
    "_resolve_payload_summary",
    "_iter_recall_cue_payloads",
    "_is_mid_term_memory_candidate_already_recalled",
    "_normalize_reference_identity_text",
    "_select_best_recall_candidate",
    "_get_candidate_embedding",
    "_cosine_similarity",
    "_format_mid_term_memory_reference",
    "_build_summary_message_id",
    "_to_base36",
    "_truncate",
}


def main():
    source = SOURCE.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    blocks, hashes = [], {}
    for node in ast.parse(source).body:
        if (
            not isinstance(node, (ast.ClassDef, ast.FunctionDef))
            or node.name not in SYMBOLS
        ):
            continue
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)]) - 1
        block = "".join(lines[start : node.end_lineno])
        blocks.append(block)
        hashes[node.name] = hashlib.sha256(
            ast.dump(node, include_attributes=False).encode()
        ).hexdigest()
    assert set(hashes) == SYMBOLS
    header = '''"""Original MaiBot mid-term pure algorithms; see mid_term_source.json and NOTICE.md."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha1
from math import sqrt
from typing import Any, Sequence
from astrbot.api import logger

from json_repair import repair_json
from pydantic import BaseModel

MID_TERM_MEMORY_REFERENCE_MARKER = "【聊天回想-内部参考】"
MID_TERM_MEMORY_RECALL_SUMMARY_TEXT_LIMIT = 1400
MID_TERM_MEMORY_DEFAULT_RECALL_THRESHOLD = 0.8

# Native internal-reference identity; this is not a platform message.
@dataclass(frozen=True, slots=True)
class MemorySummaryMessage:
    message_id: str
'''
    target = ROOT / "vendor/maibot/mid_term.py"
    target.write_text(
        header + "\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8", newline="\n"
    )
    template = ROOT.parent / "MaiBot/prompts/zh-CN/mid_term_memory_summary.prompt"
    prompt = target.parent / "mid_term_summary.txt"
    prompt.write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
    )
    (target.parent / "mid_term_source.json").write_text(
        json.dumps(
            {
                "commit": "5c7637e6913c84d1c3d5e742fa9e95a7e70d3501",
                "source": "src/maisaka/memory/mid_term.py",
                "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                "ported_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "template_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
                "symbol_ast_sha256": hashes,
                "adaptation": "Pure original parser, cue, cosine selection and reference algorithms. Native internal identity DTO replaces MaiBot context-message dependency. Host compression, provenance, storage and transport live in midterm.py.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
