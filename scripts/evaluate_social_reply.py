"""Probe social expression judgments with public synthetic inputs; never send chat.

The supplied config is read only. Keys stay in memory; output contains enum
choices, counts and timings only. This measures the typed auxiliary boundary,
not main-model response quality or a real QQ delivery.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TRAITS = [
    {
        "id": 1,
        "kind": "expression",
        "situation": "对方低落且只想倾诉时",
        "content": "先接住感受，不急着建议或分析",
    },
    {
        "id": 2,
        "kind": "expression",
        "situation": "朋友之间轻松调侃时",
        "content": "顺着笑点用简短反问回敬，不认真说教",
    },
    {
        "id": 3,
        "kind": "expression",
        "situation": "自己说错且被纠正时",
        "content": "直接认错并更正，不找借口或转移话题",
    },
    {
        "id": 4,
        "kind": "behavior",
        "situation": "对方明确请求完整操作步骤时",
        "content": "按实际步骤完整回答，不能只答应或附和",
    },
    {
        "id": 5,
        "kind": "expression",
        "situation": "对方拒绝亲昵称呼时",
        "content": "简短接受边界，不继续用拒绝的称呼撒娇",
    },
]
CASES = [
    {
        "name": "listen_without_advice",
        "text": "user: 今天真的很难受。我只想吐槽，别给我建议。",
        "acts": ["陪伴倾听"],
        "include": [1],
        "exclude": [2, 4],
    },
    {
        "name": "friendly_teasing",
        "text": "朋友: 你刚说绝对不会翻车，结果第一个掉坑里了哈哈哈哈。bot此前也在和这个朋友轻松开玩笑。",
        "acts": ["接梗调侃"],
        "include": [2],
        "exclude": [1, 4],
    },
    {
        "name": "own_correction",
        "text": "bot: 我刚刚说这台机器只有8G内存。user: 你看错了，你上面贴的截图明明是16G，别换话题。",
        "acts": ["修正自己的话"],
        "include": [3],
        "exclude": [1, 2],
    },
    {
        "name": "complete_task",
        "text": "user: 我要从零安装这个软件，请完整列出准备、安装、配置和验证步骤，每一步都要能执行，不要只回一句。",
        "acts": ["直接回答"],
        "include": [4],
        "exclude": [1, 2],
        "style_contains": "完整落实",
    },
    {
        "name": "respect_boundary",
        "text": "user: 别叫我主人，我不喜欢。正常叫我名字就行，接受就好，不需要解释。",
        "acts": ["简短接住", "直接回答"],
        "include": [5],
        "exclude": [2, 4],
    },
    {
        "name": "no_forced_habit",
        "text": "user: 只回答 7乘以8 等于多少，直接给数字。",
        "acts": ["直接回答", "简短接住"],
        "include": [],
        "exclude": [1, 2, 3, 4, 5],
    },
]


async def main(args):
    config = json.loads(
        await asyncio.to_thread(args.config.read_text, encoding="utf-8-sig")
    )
    source = globals().get("SOURCE_OVERRIDE") or await asyncio.to_thread(
        (ROOT / "jev.py").read_text, encoding="utf-8"
    )
    package = types.ModuleType("ovenheart_social_evaluation")
    package.__path__ = [str(ROOT)]
    sys.modules[package.__name__] = package
    module = types.ModuleType(package.__name__ + ".jev")
    module.__package__ = package.__name__
    sys.modules[module.__name__] = module
    exec(compile(source, "jev.py", "exec"), module.__dict__)
    settings = {
        "jev_enabled": True,
        "jev_timeout_sec": 4,
        "jev_max_state_chars": 6000,
        "max_traits": 4,
    }
    service = module.JevService(types.SimpleNamespace(config=config, settings=settings))
    rows = []
    try:
        for case in CASES:
            started = time.monotonic()
            result = await service.select_habits(
                TRAITS,
                case["text"],
                settings,
                "你是正在群里聊天的群友，表达自然，有自己的看法，尊重对方边界。",
                [],
                {},
            )
            ids = result["selected_ids"]
            intent = result["expression_intent"]
            passed = (
                intent.get("reply_act") in case["acts"]
                and set(case["include"]).issubset(ids)
                and not set(case["exclude"]).intersection(ids)
                and case.get("style_contains", "") in intent.get("tone", "")
            )
            rows.append(
                {
                    "case": case["name"],
                    "passed": passed,
                    "selected_ids": ids,
                    "expression_intent": intent,
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
    finally:
        await service.client.close()
    print(
        json.dumps(
            {
                "scope": "Six public synthetic typed judgments; no user data, main chat generation or QQ sends.",
                "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                "passed": sum(row["passed"] for row in rows),
                "total": len(rows),
                "cases": rows,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[3]
        / "config/astrbot_plugin_ovenheart_config.json",
    )
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        print(
            f"Social evaluation failed ({type(exc).__name__}); provider details suppressed.",
            file=sys.stderr,
        )
        sys.exit(1)
