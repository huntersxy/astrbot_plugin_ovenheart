"""Replay synthetic multi-turn chat through an isolated, real AstrBot pipeline.

Private provider/persona input arrives on stdin. Only synthetic messages, visible
replies, tool actions and boolean input checks enter the report. No QQ adapter,
dashboard listener or production storage is opened.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]


def request_texts(value):
    if isinstance(value, str):
        yield value
        if value.startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return
            yield from request_texts(decoded)
    elif isinstance(value, dict):
        for item in value.values():
            yield from request_texts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from request_texts(item)
    elif hasattr(value, "model_dump_for_context"):
        yield from request_texts(value.model_dump_for_context())
    elif hasattr(value, "model_dump"):
        yield from request_texts(value.model_dump())


async def run(args, packet, report):
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.case:
        cases = {args.case: cases[args.case]}
    provider_config = packet["provider"]
    persona = packet["persona"]
    report.update(
        stage="native_full_pipeline",
        created_utc=datetime.now(timezone.utc).isoformat(),
        configured_model=provider_config["model"],
        persona_sha256=hashlib.sha256(persona.encode()).hexdigest(),
        cases_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        source_sha256={
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("main.py", "planner.py", "participation.py", "prompts.py")
        },
        conditions={
            "explicit_group_wake_each_turn": True,
            "fresh_store_per_case": True,
            "learned_traits": [],
            "memory_enabled": False,
            "same_configured_model_for_planner_and_reply": True,
            "platform_transport": "loopback",
        },
        cases=[],
    )
    # One host process owns one temporary root. Distinct groups isolate cases.
    with tempfile.TemporaryDirectory(prefix="ovenheart-chat-parity-") as directory:
        os.environ["ASTRBOT_ROOT"] = directory
        sys.path.insert(0, directory)
        data = Path(directory) / "data"
        (data / "config").mkdir(parents=True)
        destination = data / "plugins/astrbot_plugin_ovenheart"
        destination.mkdir(parents=True)
        for path in [
            *ROOT.glob("*.py"),
            ROOT / "metadata.yaml",
            ROOT / "_conf_schema.json",
        ]:
            shutil.copyfile(path, destination / path.name)
        for name in ("views", ".astrbot-plugin", "vendor"):
            shutil.copytree(ROOT / name, destination / name)
        (data / "cmd_config.json").write_text(
            json.dumps(
                {
                    "dashboard": {"enable": False},
                    "disable_metrics": True,
                    "log_level": "ERROR",
                    "trace_enable": True,
                }
            ),
            encoding="utf-8",
        )

        from astrbot import __version__
        from astrbot.api.event import AstrMessageEvent
        from astrbot.api.message_components import At, Plain
        from astrbot.api.platform import (
            AstrBotMessage,
            Group,
            MessageMember,
            MessageType,
            PlatformMetadata,
        )
        from astrbot.core import LogBroker, astrbot_config, core_lifecycle, db_helper

        report["host_version"] = __version__
        astrbot_config["agent_runner"]["config"]["model"].update(
            provider_id="parity-chat", request_max_retries=0
        )
        astrbot_config["provider_settings"]["proactive_capability"][
            "add_cron_tools"
        ] = False
        core = core_lifecycle.AstrBotCoreLifecycle(LogBroker(), db_helper)
        start = None
        deliveries = []
        active = None

        class LoopbackEvent(AstrMessageEvent):
            async def send(self, message):
                if message:
                    deliveries.append(
                        {
                            "source": self.get_extra(
                                "ovenheart_fallback_source", self.message_obj.message_id
                            ),
                            "text": "".join(
                                c.text for c in message.chain if isinstance(c, Plain)
                            ),
                        }
                    )
                await super().send(message)

        try:
            with patch.object(core_lifecycle, "update_llm_metadata", AsyncMock()):
                await core.initialize()
            metadata = core.star_context.get_registered_star("astrbot_plugin_ovenheart")
            if metadata is None or not metadata.activated:
                raise RuntimeError("Isolated plugin did not load")
            plugin = metadata.star_cls
            plugin.worker.cancel()
            await asyncio.gather(plugin.worker, return_exceptions=True)
            plugin.settings.update(
                planner_mode="maibot",
                planner_provider_id="parity-chat",
                provider_id="parity-chat",
                planner_timeout=90,
                planner_max_wait=1,
                participation_mode="off",
                learning_enabled=False,
                memory_enabled=False,
                native_memory_enabled=False,
                person_memory_enabled=False,
                emoji_enabled=False,
                selection_mode="lexical",
                session_isolation=True,
                reply_rhythm_enabled=True,
                reply_typing_speed=0,
            )
            # Record the real action boundary without retaining prompts/reasoning.
            debug = plugin.debug

            def record_debug(name, **details):
                if active is not None and name == "conversation_planner":
                    active["actions"].append(
                        {
                            key: value
                            for key, value in details.items()
                            if key
                            in (
                                "action",
                                "tool",
                                "msg_id",
                                "status",
                                "seconds",
                                "reason",
                            )
                        }
                    )
                return debug(name, **details)

            plugin.debug = record_debug
            await core.persona_mgr.update_persona(
                "default", system_prompt=persona, tools=[], skills=[]
            )
            actual_persona = core.persona_mgr.get_persona_v3_by_id("default")
            report["native_persona_sha256"] = hashlib.sha256(
                actual_persona["prompt"].encode()
            ).hexdigest()
            if actual_persona["prompt"] != persona:
                raise ValueError("Host persona cache differs from requested identity")
            await core.provider_manager.load_provider(
                provider_config
                | {
                    "id": "parity-chat",
                    "enable": True,
                    "provider_type": "chat_completion",
                    "timeout": 90,
                    "max_context_tokens": 32768,
                }
            )
            provider = await core.provider_manager.get_provider_by_id("parity-chat")
            real_transport = provider.text_chat

            async def observed_transport(**kwargs):
                tools = kwargs.get("func_tool")
                stage = "planner" if tools and tools.get_tool("reply") else "replyer"
                serialized = "\n".join(request_texts(kwargs))
                row = {
                    "stage": stage,
                    "argument_types": {
                        key: type(value).__name__ for key, value in kwargs.items()
                    },
                    "request_text_chars": len(serialized),
                    "query_in_request": active["text"] in serialized,
                    "persona_in_request": persona in serialized,
                    "previous_replies_in_request": [
                        text in serialized for text in active["previous_replies"]
                    ],
                }
                active["calls"].append(row)
                if not row["query_in_request"] or not row["persona_in_request"]:
                    raise ValueError(
                        "Current synthetic input/persona missing from request"
                    )
                started = time.monotonic()
                response = await real_transport(**kwargs)
                row.update(
                    seconds=round(time.monotonic() - started, 3),
                    role=response.role,
                    tools=[
                        {"name": name, "arguments": arguments}
                        for name, arguments in zip(
                            response.tools_call_name or [],
                            response.tools_call_args or [],
                            strict=True,
                        )
                    ],
                    usage=asdict(response.usage) if response.usage else None,
                )
                return response

            provider.text_chat = observed_transport
            start = asyncio.create_task(core.start())
            for name, turns in cases.items():
                case = {"name": name, "turns": []}
                report["cases"].append(case)
                previous_replies = []
                for index, turn in enumerate(turns):
                    identifier = f"{name}-{index + 1}"
                    active = turn | {
                        "id": identifier,
                        "calls": [],
                        "actions": [],
                        "previous_replies": list(previous_replies),
                    }
                    case["turns"].append(active)
                    message = AstrBotMessage()
                    message.type = MessageType.GROUP_MESSAGE
                    message.self_id = "bot"
                    message.sender = MessageMember(
                        turn["sender"], {"alice": "阿梨", "bob": "阿柏"}[turn["sender"]]
                    )
                    message.group = Group(name, "隔离对照群")
                    message.message_id = identifier
                    message.message_str = turn["text"]
                    message.message = [At(qq="bot"), Plain(text=turn["text"])]
                    incoming = LoopbackEvent(
                        turn["text"],
                        message,
                        PlatformMetadata(
                            "aiocqhttp", "parity-loopback", "parity-loopback"
                        ),
                        name,
                    )
                    incoming.set_extra("selected_provider", "parity-chat")
                    begin = len(deliveries)
                    await core.event_queue.put(incoming)
                    deadline = time.monotonic() + args.turn_timeout
                    while not incoming.trace._finished:
                        if time.monotonic() > deadline:
                            raise TimeoutError("Isolated turn did not finish")
                        await asyncio.sleep(0.05)
                    active.update(
                        replies=[item["text"] for item in deliveries[begin:]],
                        has_errors=incoming.trace._summary["has_errors"],
                        receipt=incoming.get_extra("ovenheart_planner_receipt"),
                        planner_status=incoming.get_extra("ovenheart_planner_status"),
                    )
                    previous_replies.extend(active["replies"])
                    # Keep progress reviewable even if a later provider call fails.
                    args.output.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    if (
                        active["has_errors"]
                        or not active["calls"]
                        or any(
                            not call["query_in_request"]
                            or not call["persona_in_request"]
                            or call.get("role") not in ("assistant", "tool")
                            for call in active["calls"]
                        )
                    ):
                        raise RuntimeError("Native pipeline reported an error")
                active = None
        finally:
            await core.stop()
            if start:
                start.cancel()
                await asyncio.gather(start, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument(
        "--cases", type=Path, default=ROOT / "scripts/chat_parity_cases.json"
    )
    parser.add_argument("--case")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turn-timeout", type=int, default=240)
    args = parser.parse_args()
    ROOT = args.source.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"phase": "running"}
    try:
        packet = json.load(sys.stdin)
        # Host/provider diagnostics may contain private input. Do not persist them.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            asyncio.run(run(args, packet, report))
        report["phase"] = "complete"
    except Exception as exc:
        report.update(
            phase="failed",
            error=type(exc).__name__,
            location=[
                {
                    "file": Path(frame.filename).name,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(exc.__traceback__)[-4:]
            ],
        )
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "phase": report["phase"],
                "output": str(args.output),
                "error": report.get("error"),
            }
        )
    )
    sys.exit(0 if report["phase"] == "complete" else 1)
