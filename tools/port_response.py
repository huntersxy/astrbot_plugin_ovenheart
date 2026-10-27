"""Reproduce original MaiBot splitter and typing-time code with scoped host imports."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "MaiBot/src/chat/utils"


def main():
    splitter = (SOURCE / "llm_sentence_splitter.py").read_text(encoding="utf-8")
    ported = splitter.replace(
        "from src.common.logger import get_logger",
        "from astrbot.api import logger",
    ).replace(
        "from src.services.llm_service import LLMServiceClient",
        "from .response_host import LLMServiceClient",
    ).replace('logger = get_logger("llm_sentence_splitter")\n', "")
    target = ROOT / "vendor/maibot/response_splitter.py"
    target.write_text(ported, encoding="utf-8", newline="\n")
    source = (SOURCE / "utils.py").read_text(encoding="utf-8")
    node = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name == "calculate_typing_time"
    )
    body = "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
    timing = target.parent / "typing_time.py"
    timing.write_text(
        '"""Original MaiBot typing-time function; see response_source.json."""\nfrom .response_host import global_config\n\n'
        + body
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    merge_nodes = [
        n
        for n in ast.parse(source).body
        if isinstance(n, (ast.ClassDef, ast.FunctionDef))
        and n.name
        in ("ProcessedResponseSegment", "_merge_processed_segments_to_max_count")
    ]
    merged = '"""Original MaiBot reply-segment compression; see response_source.json."""\nfrom dataclasses import dataclass\n\n'
    for entry in merge_nodes:
        start = min([entry.lineno, *(d.lineno for d in entry.decorator_list)]) - 1
        merged += "\n".join(source.splitlines()[start : entry.end_lineno]) + "\n\n"
    merge_target = target.parent / "response_segments.py"
    merge_target.write_text(merged, encoding="utf-8", newline="\n")
    (target.parent / "response_source.json").write_text(
        json.dumps(
            {
                "commit": "5c7637e6913c84d1c3d5e742fa9e95a7e70d3501",
                "splitter": {
                    "source": "src/chat/utils/llm_sentence_splitter.py",
                    "source_sha256": hashlib.sha256(
                        (SOURCE / "llm_sentence_splitter.py").read_bytes()
                    ).hexdigest(),
                    "ported_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "symbol_ast_sha256": {
                        node.name: hashlib.sha256(
                            ast.dump(node, include_attributes=False).encode()
                        ).hexdigest()
                        for node in ast.parse(splitter).body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    },
                },
                "typing": {
                    "source": "src/chat/utils/utils.py",
                    "source_sha256": hashlib.sha256(
                        (SOURCE / "utils.py").read_bytes()
                    ).hexdigest(),
                    "ported_sha256": hashlib.sha256(timing.read_bytes()).hexdigest(),
                    "symbol_ast_sha256": {
                        node.name: hashlib.sha256(
                            ast.dump(node, include_attributes=False).encode()
                        ).hexdigest()
                    },
                },
                "adaptation": "Only native logger/model imports change. Root reply_rhythm.py handles AstrBot send timing and permissions.",
                "segments": {
                    "source": "src/chat/utils/utils.py",
                    "ported_sha256": hashlib.sha256(
                        merge_target.read_bytes()
                    ).hexdigest(),
                    "symbol_ast_sha256": {
                        entry.name: hashlib.sha256(
                            ast.dump(entry, include_attributes=False).encode()
                        ).hexdigest()
                        for entry in merge_nodes
                    },
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
