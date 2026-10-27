"""Run the pinned Maisaka runtime with synthetic multi-turn chat and loopback IM.

The original scheduler, reasoning engine, tools, replyer and history are used.
Identity adapters supply the same native persona; only platform sends are
replaced. Private provider/persona input arrives on stdin and is never exported.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from maibot_replyer_probe import PINNED, reference_commit


async def run(args, packet, report):
    source = args.source.resolve()
    commit = await asyncio.to_thread(reference_commit, source)
    if commit != PINNED:
        raise ValueError("Unexpected reference revision")
    instance = args.instance.resolve()
    if instance == source or source in instance.parents:
        raise ValueError("Reference instance must be outside source")
    instance.mkdir(parents=True, exist_ok=False)
    os.environ["MAIBOT_INSTANCE_DIR"] = str(instance)
    os.chdir(instance)
    sys.path.insert(0, str(source))

    from src.chat.message_receive.chat_manager import chat_manager
    from src.chat.message_receive.message import SessionMessage
    from src.chat.replyer.maisaka_generator import MaisakaReplyGenerator
    from src.chat.replyer.replyer_manager import replyer_manager
    from src.common.data_models.mai_message_data_model import (
        GroupInfo,
        MessageInfo,
        UserInfo,
    )
    from src.common.data_models.message_component_data_model import (
        MessageSequence,
        TextComponent,
    )
    from src.config.config import global_config, model_config
    from src.config.model_configs import APIProvider, ModelInfo, TaskConfig
    from src.llm_models.payload_content.context_item import get_item_text
    from src.maisaka.chat_loop_service import MaisakaChatLoopService
    from src.maisaka.runtime import MaisakaHeartFlowChatting
    from src.services import send_service

    provider = packet["provider"]
    persona = packet["persona"]
    if provider["type"] not in ("openai_chat_completion", "openai_responses"):
        raise ValueError("Unsupported matching transport")
    body = provider.get("custom_extra_body") or {}
    api = APIProvider(
        name="parity-reference",
        base_url=provider["api_base"],
        api_key=provider["key"][0],
        client_type="openai_responses"
        if provider["type"] == "openai_responses"
        else "openai",
        timeout=90,
        max_retry=0,
        default_headers=provider.get("custom_headers") or {},
    )
    model = ModelInfo(
        model_type="llm",
        name="parity-reference",
        model_identifier=provider["model"],
        api_provider=api.name,
        send_temperature=False,
        max_tokens=body.get("max_tokens") or 4096,
        extra_params={"body": body},
    )
    model_config.api_providers = [api]
    model_config.models = [model]
    for task in ("planner", "replyer", "utils", "fast_model"):
        setattr(
            model_config.model_task_config,
            task,
            TaskConfig(
                model_list=[model.name],
                max_tokens=model.max_tokens,
                hard_timeout=90,
            ),
        )
    global_config.debug.save_maisaka_reasoning = False
    global_config.mcp.enable = False
    global_config.expression.use_vector_expression = False
    for rule in global_config.expression.learning_list:
        rule.use = False
        rule.learn = False
    for rule in global_config.jargon.learning_list:
        rule.use = False
        rule.learn = False
    global_config.a_memorix.plugin.enabled = False
    global_config.a_memorix.integration.enable_memory_query_tool = False
    global_config.a_memorix.integration.enable_person_profile_query_tool = False
    global_config.a_memorix.integration.enable_person_profile_injection = False
    global_config.a_memorix.integration.heuristic_memory_recall_enabled = False
    global_config.a_memorix.image_memory.enabled = False
    global_config.experimental.planner_message_steering = True
    global_config.bot.nickname = "烤箱"
    # Loopback has no platform account; compare undecorated text on both sides.
    global_config.chat.reply_style.group_quote_frequency = "never"
    global_config.chat.reply_style.private_quote_frequency = "never"

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = {args.case: cases[args.case]} if args.case else cases
    report.update(
        stage="maibot_full_runtime",
        source_commit=commit,
        created_utc=datetime.now(timezone.utc).isoformat(),
        configured_model=provider["model"],
        persona_sha256=hashlib.sha256(persona.encode()).hexdigest(),
        cases_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        conditions={
            "platform_transport": "loopback",
            "learned_traits": [],
            "memory_enabled": False,
            "explicit_group_wake_each_turn": True,
            "reference_max_output_tokens": model.max_tokens,
            "identity_adapter": "verbatim native persona in behavior style and replyer identity",
            "quote_frequency": "never; loopback has no platform sending account",
        },
        cases=[],
    )
    active = None
    runtime = None
    deliveries = []

    def sanitized_reason(reason):
        for private in [
            persona,
            provider["api_base"],
            *provider["key"],
            *(provider.get("custom_headers") or {}).values(),
        ]:
            if private:
                reason = reason.replace(str(private), "[redacted]")
        return reason[:500]

    def verify_items(items, stage):
        text = "\n".join(get_item_text(item) for item in items)
        row = {
            "stage": stage,
            "query_in_request": active["text"] in text,
            "persona_in_request": persona in text,
            "previous_replies_in_request": [
                reply in text for reply in active["previous_replies"]
            ],
        }
        active["calls"].append(row)
        if not row["query_in_request"] or not row["persona_in_request"]:
            raise ValueError("Original request filtered synthetic input or persona")
        return row

    class IdentityPlanner(MaisakaChatLoopService):
        @property
        def behavior_style_prompt(self):
            return persona

        def _build_request_messages(self, *args, **kwargs):
            items, count = super()._build_request_messages(*args, **kwargs)
            verify_items(items, "planner")
            return items, count

        async def chat_loop_step(self, *args, **kwargs):
            started = time.monotonic()
            result = await super().chat_loop_step(*args, **kwargs)
            active["actions"].extend(
                {"name": call.func_name, "arguments": call.args}
                for call in result.tool_calls
            )
            active["planner_results"].append(
                {
                    "seconds": round(time.monotonic() - started, 3),
                    "input_tokens": result.prompt_tokens,
                    "output_tokens": result.completion_tokens,
                }
            )
            return result

    class IdentityReplyer(MaisakaReplyGenerator):
        def _build_personality_prompt(self):
            return persona

        def _build_request_messages(self, **kwargs):
            items = super()._build_request_messages(**kwargs)
            verify_items(items, "replyer")
            return items

        async def generate_reply_with_context(self, *args, **kwargs):
            success, result = await super().generate_reply_with_context(*args, **kwargs)
            active["replyer_results"].append(
                {
                    "success": success,
                    "error": sanitized_reason(result.error_message or ""),
                }
            )
            return success, result

    async def loopback_send(**kwargs):
        text = kwargs["processed_plain_text"]
        message = SessionMessage(
            f"loopback-bot-{len(deliveries) + 1}", datetime.now(), "ovenheart-parity"
        )
        message.session_id = runtime.session_id
        message.message_info = MessageInfo(
            UserInfo("bot", "烤箱"), GroupInfo(active["case"], "隔离对照群")
        )
        message.raw_message = kwargs["message_sequence"]
        message.processed_plain_text = text
        if not runtime.append_sent_message_to_chat_history(message):
            raise RuntimeError("Original runtime rejected sent message history")
        deliveries.append(text)
        return message

    send_service._send_to_target_with_message = loopback_send

    def observe_result(tool_call, result, **kwargs):
        active["tool_results"].append(
            {
                "name": tool_call.func_name,
                "success": result.success,
                "error": sanitized_reason(result.error_message or ""),
            }
        )
        return append_result(tool_call, result, **kwargs)

    for name, turns in cases.items():
        session = await chat_manager.get_or_create_session(
            platform="ovenheart-parity",
            user_id="alice",
            group_id=name,
        )
        runtime = MaisakaHeartFlowChatting(session.session_id)
        append_result = runtime._reasoning_engine._append_tool_execution_result

        runtime._reasoning_engine._append_tool_execution_result = observe_result
        service = IdentityPlanner(session_id=session.session_id, is_group_chat=True)
        service.set_tool_registry(runtime._tool_registry)
        runtime._chat_loop_service = service
        replyer_manager._repliers[f"maisaka:{session.session_id}"] = IdentityReplyer(
            chat_stream=session
        )
        case = {"name": name, "turns": []}
        report["cases"].append(case)
        previous_replies = []
        await runtime.start()
        try:
            for index, turn in enumerate(turns):
                active = turn | {
                    "case": name,
                    "id": f"{name}-{index + 1}",
                    "calls": [],
                    "actions": [],
                    "planner_results": [],
                    "replyer_results": [],
                    "tool_results": [],
                    "previous_replies": list(previous_replies),
                }
                case["turns"].append(active)
                now = datetime.now()
                message = SessionMessage(
                    active["id"], now, "ovenheart-parity", received_at=now
                )
                message.session_id = session.session_id
                message.message_info = MessageInfo(
                    UserInfo(
                        turn["sender"], {"alice": "阿梨", "bob": "阿柏"}[turn["sender"]]
                    ),
                    GroupInfo(name, "隔离对照群"),
                )
                message.raw_message = MessageSequence(
                    [TextComponent(text=turn["text"])]
                )
                await message.process(
                    enable_heavy_media_analysis=False, enable_voice_transcription=False
                )
                message.is_at = True
                begin = len(deliveries)
                await runtime.register_message(message)
                deadline = time.monotonic() + args.turn_timeout
                while True:
                    if any(not result["success"] for result in active["tool_results"]):
                        raise RuntimeError(
                            "Original action failed; inspect sanitized result"
                        )
                    if any(
                        not result["success"] for result in active["replyer_results"]
                    ):
                        raise RuntimeError(
                            "Original replyer failed; inspect sanitized result"
                        )
                    task = runtime._internal_loop_task
                    if task and task.done():
                        task.result()
                        raise RuntimeError("Original reasoning loop exited")
                    if (
                        runtime._last_processed_index == len(runtime.message_cache)
                        and active["planner_results"]
                        and runtime._agent_state
                        in (runtime._STATE_STOP, runtime._STATE_WAIT)
                    ):
                        break
                    if time.monotonic() > deadline:
                        raise TimeoutError("Original runtime did not finish turn")
                    await asyncio.sleep(0.05)
                active.update(
                    replies=deliveries[begin:], final_state=runtime._agent_state
                )
                previous_replies.extend(active["replies"])
                args.output.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        finally:
            await runtime.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--case")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turn-timeout", type=int, default=240)
    args = parser.parse_args()
    args.cases = args.cases.resolve()
    args.output = args.output.resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"phase": "running"}
    try:
        packet = json.load(sys.stdin)
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
                "error": report.get("error"),
                "output": str(args.output),
            }
        )
    )
    sys.exit(0 if report["phase"] == "complete" else 1)
