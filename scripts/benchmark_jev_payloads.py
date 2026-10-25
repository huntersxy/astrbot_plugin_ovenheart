"""Build public synthetic before/after payloads without making provider calls."""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock

root = Path(__file__).resolve().parents[1]


def load_service(name, source, transport_source=None):
    package = types.ModuleType(name)
    package.__path__ = [str(root)]
    sys.modules[name] = package
    if transport_source is not None:
        transport = types.ModuleType(name + ".jev_transport")
        sys.modules[transport.__name__] = transport
        exec(
            compile(transport_source, name + ".jev_transport", "exec"),
            transport.__dict__,
        )
    module = types.ModuleType(name + ".jev")
    module.__package__ = name
    sys.modules[module.__name__] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module.JevService


async def generate(baseline):
    before = (
        await asyncio.to_thread(
            subprocess.check_output, ["git", "show", baseline + ":jev.py"], cwd=root
        )
    ).decode()
    before_transport = (
        await asyncio.to_thread(
            subprocess.check_output,
            ["git", "show", baseline + ":jev_transport.py"],
            cwd=root,
        )
    ).decode()
    classes = {
        "before": load_service("jev_baseline", before, before_transport),
        "after": load_service(
            "jev_current", (root / "jev.py").read_text(encoding="utf-8")
        ),
    }
    cases = json.loads(
        (root / "docs/evaluation/jev-cases.json").read_text(encoding="utf-8")
    )
    payloads = []
    for case in cases:
        for variant, service_type in classes.items():
            plugin = types.SimpleNamespace(
                config={"jev_connection": {"api_key": "synthetic-capture-only"}},
                settings={
                    "jev_enabled": True,
                    "jev_participation_enabled": True,
                    "jev_learning_enabled": True,
                    "jev_timeout_sec": 4,
                    "jev_max_state_chars": 6000,
                },
                permitted=AsyncMock(return_value=True),
            )
            service = service_type(plugin)

            async def capture(cfg, state, questions, case=case, variant=variant):
                payloads.append(
                    {
                        "case": case["name"],
                        "variant": variant,
                        "expected": case["expected"],
                        "state": state,
                        "questions": questions,
                    }
                )
                return {"status": "error"}

            service.client.evaluate = capture
            if case["purpose"] == "learning":
                await service.learning(
                    case["traits"], case["base"], time.monotonic() + 5
                )
            else:
                await service.participation(
                    None,
                    "synthetic",
                    case["messages"][case["target_index"]],
                    case["messages"],
                )
    return payloads


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="c46aa60")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(generate(args.baseline))
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name in dict.fromkeys(p["case"] for p in result):
        pair = [p for p in result if p["case"] == name]
        sizes = {
            p["variant"]: len(
                json.dumps(
                    {k: p[k] for k in ("state", "questions")}, ensure_ascii=False
                )
            )
            for p in pair
        }
        print(name, sizes)
