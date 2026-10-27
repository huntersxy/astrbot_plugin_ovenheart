"""Run an isolated AstrBot core with real pipeline stages and loopback transport.

Only synthetic messages are submitted. Full mode calls the configured chat model
and SiliconFlow's free embedding/rerank models. Credentials stay in process memory.
Smoke mode requires no credentials. No dashboard server or QQ connection is opened.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]


async def main(args):
    """Boot the host, load the plugin and submit messages through its event bus.

    Args:
        args: Output path, model endpoint and optional smoke-only mode.
    """
    report = {
        "scope": "Native core, plugin loader, permissions, session history, all pipeline stages and model execution; only platform transport is loopback. No real QQ sends, dashboard server, real memory data or MaiBot outcome comparison.",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "smoke": args.smoke,
        "profile_condition": "Explicit isolated-session legacy dual-delivery acceptance; shared/cached defaults are covered separately.",
        "chat_parameters": {"temperature": 0.2, "timeout_seconds": args.chat_timeout},
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (
                "main.py",
                "core/learning.py",
                "core/prompts.py",
                "core/settings.py",
                "chat/participation.py",
                "core/retrieval.py",
                "core/store.py",
                "tests/native_harness.py",
            )
        },
        "events": [],
        "learning": [],
        "learning_review_inputs": [],
        "learning_calls": [],
    }

    def persist(phase, error_type=None):
        """Checkpoint completed evidence without provider error text.

        Args:
            phase: Current phase or terminal state.
            error_type: Exception class name, if the run failed.
        """
        report.update(phase=phase, error_type=error_type)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({"phase": phase, "error_type": error_type}), flush=True)

    with tempfile.TemporaryDirectory(prefix="ovenheart-native-") as directory:
        sandbox = Path(directory)
        os.environ["ASTRBOT_ROOT"] = directory
        sys.path.insert(0, directory)
        data = sandbox / "data"
        (data / "config").mkdir(parents=True)
        destination = data / "plugins/astrbot_plugin_ovenheart"
        destination.mkdir(parents=True)
        for path in [
            *ROOT.glob("*.py"),
            ROOT / "metadata.yaml",
            ROOT / "_conf_schema.json",
            ROOT / "requirements.txt",
        ]:
            shutil.copyfile(path, destination / path.name)
            assert path.read_bytes() == (destination / path.name).read_bytes()
        for name in ("core", "chat", "memory", "integrations", "views", ".astrbot-plugin", "vendor"):
            shutil.copytree(ROOT / name, destination / name)
        (data / "cmd_config.json").write_text(
            json.dumps(
                {
                    "dashboard": {"enable": False},
                    "disable_metrics": True,
                    "log_level": "WARNING",
                    "trace_enable": True,
                    "t2i_endpoint": "http://127.0.0.1:9/unused",
                }
            ),
            encoding="utf-8",
        )

        import httpx
        from astrbot import __version__
        from astrbot.api.event import AstrMessageEvent
        from astrbot.api.message_components import Plain
        from astrbot.api.platform import (
            AstrBotMessage,
            Group,
            MessageMember,
            MessageType,
            PlatformMetadata,
        )
        from astrbot.core import (
            LogBroker,
            astrbot_config,
            core_lifecycle,
            db_helper,
            sp,
        )
        from astrbot.dashboard.server import AstrBotDashboard

        report["host_version"] = __version__
        astrbot_config["agent_runner"]["config"]["model"].update(
            provider_id="native-chat", request_max_retries=1
        )
        astrbot_config["provider_settings"]["proactive_capability"][
            "add_cron_tools"
        ] = False
        core = core_lifecycle.AstrBotCoreLifecycle(LogBroker(), db_helper)
        start = None
        deliveries = []
        raw_history = []

        class LoopbackEvent(AstrMessageEvent):
            async def send(self, message):
                """Collect the native send result without invoking an IM adapter.

                Args:
                    message: Final message chain from the native RespondStage.
                """
                if message:
                    deliveries.append(
                        {
                            "id": self.message_obj.message_id,
                            "source": self.get_extra(
                                "ovenheart_fallback_source", self.message_obj.message_id
                            ),
                            "text": "".join(
                                c.text for c in message.chain if isinstance(c, Plain)
                            ),
                        }
                    )
                await super().send(message)

        def incoming(identifier, text, sender="alice", group="synthetic"):
            """Create a synthetic native group event with its original message intact.

            Args:
                identifier: Unique synthetic message identifier.
                text: Fictional human utterance.
                sender: Fictional group member.
                group: Fictional group identifier.

            Returns:
                Native event with a loopback send implementation.
            """
            message = AstrBotMessage()
            message.type = MessageType.GROUP_MESSAGE
            message.self_id = "bot"
            message.sender = MessageMember(sender, sender)
            message.group = Group(group, "合成验收群")
            message.message_id = identifier
            message.message_str = text
            message.message = [Plain(text=text)]
            return LoopbackEvent(
                text,
                message,
                PlatformMetadata("aiocqhttp", "native-loopback", "native-loopback"),
                group,
            )

        async def submit(events, *, wait_background=True):
            """Wait for native trace completion, including stopped passive events.

            Args:
                events: Native events to enqueue together.
            """
            for event in events:
                await core.event_queue.put(event)
            deadline = time.monotonic() + 180
            while not all(event.trace._finished for event in events):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native pipeline did not finish")
                await asyncio.sleep(0.02)
            if wait_background and plugin.tasks:
                await asyncio.gather(*tuple(plugin.tasks), return_exceptions=True)
            for event in events:
                report["events"].append(
                    {
                        "id": event.message_obj.message_id,
                        "input": event.message_obj.message_str,
                        "pipeline_text": event.get_message_str(),
                        "umo": event.unified_msg_origin,
                        "stopped": event.is_stopped(),
                        "trace": {
                            key: event.trace._summary[key]
                            for key in (
                                "state",
                                "has_models",
                                "has_errors",
                                "has_commands",
                                "has_plugins",
                            )
                        },
                        "replies": [
                            d["text"]
                            for d in deliveries
                            if d["id"] == event.message_obj.message_id
                        ],
                        "decision": event.get_extra("ovenheart_decision"),
                        "application": event.get_extra("ovenheart_application"),
                    }
                )

        persist("initializing")
        try:
            # Suppress unrelated model-catalog downloads; do not replace pipeline behavior.
            with patch.object(core_lifecycle, "update_llm_metadata", AsyncMock()):
                await core.initialize()
            metadata = core.star_context.get_registered_star("astrbot_plugin_ovenheart")
            if metadata is None or not metadata.activated:
                raise RuntimeError("Native loader did not activate OvenHeart")
            plugin = metadata.star_cls
            report["plugin"] = {
                "name": metadata.name,
                "version": metadata.version,
                "views": metadata.views,
            }
            report["stages"] = [
                type(s).__name__
                for s in core.pipeline_scheduler_mapping["default"].stages
            ]
            plugin.worker.cancel()
            await asyncio.gather(plugin.worker, return_exceptions=True)
            dashboard = AstrBotDashboard(core, db_helper, core.dashboard_shutdown_event)
            transport = httpx.ASGITransport(app=dashboard.asgi_app)
            token = dashboard.asgi_app.state.services.auth.generate_jwt(
                astrbot_config["dashboard"]["username"]
            )
            headers = {"Authorization": "Bearer " + token}

            async def configure(payload):
                """Apply settings through the authenticated native Panel extension.

                Args:
                    payload: Non-secret plugin settings for this isolated run.
                """
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://native.test", headers=headers
                ) as client:
                    response = await client.post(
                        "/api/plug/astrbot_plugin_ovenheart/action",
                        json={"action": "settings", "payload": payload},
                    )
                    assert (
                        response.status_code == 200
                        and response.json()["status"] == "ok"
                    )

            async with httpx.AsyncClient(
                transport=transport, base_url="http://native.test"
            ) as client:
                blocked = await client.get("/api/plug/astrbot_plugin_ovenheart/state")
                state = await client.get(
                    "/api/plug/astrbot_plugin_ovenheart/state", headers=headers
                )
                entry = await client.get(
                    "/api/v1/plugins/astrbot_plugin_ovenheart/views/workshop",
                    headers=headers,
                )
                content_path = entry.json()["data"]["content_path"]
                html = await client.get(content_path)
                script = await client.get(urljoin(content_path, "app.js"))
                wrong_scope = await client.get(
                    content_path.replace("/workshop/", "/other/")
                )
                report["panel"] = {
                    "anonymous_status": blocked.status_code,
                    "state_status": state.status_code,
                    "state_ok": state.json().get("status") == "ok",
                    "entry_status": entry.status_code,
                    "html_status": html.status_code,
                    "bridge_present": "/api/plugin/page/bridge-sdk.js" in html.text,
                    "script_status": script.status_code,
                    "wrong_scope_status": wrong_scope.status_code,
                }
            await configure({"guidance_delivery": "dual", "session_isolation": True})
            start = asyncio.create_task(core.start())
            await submit([incoming("command", "/sid")])
            report["command_pending"] = sum(
                s["pending"] for s in (await plugin.store.call("state"))["scopes"]
            )
            persist("command_checked")
            await configure({"target_senders": ["mentor"]})
            target_events = [
                incoming(
                    "target-peer-1",
                    "PEER_CONTEXT：先别给我建议，我想慢慢说",
                    "visitor",
                    "target-probe",
                ),
                incoming(
                    "target-source-1",
                    "好，我先听你说完，不急着给办法",
                    "mentor",
                    "target-probe",
                ),
                incoming(
                    "target-peer-2",
                    "PEER_CONTEXT：现在想要两个能做的小步骤",
                    "visitor",
                    "target-probe",
                ),
                incoming(
                    "target-source-2",
                    "好，那就给你两个小步骤，先列问题再选动作",
                    "mentor",
                    "target-probe",
                ),
            ]
            await submit(target_events)
            target_scope, _, _ = await plugin.resolve_scope(target_events[-1])
            rows = await plugin.store.call("batch", target_scope, 100)
            report["target_capture"] = {
                "source_senders": [m["sender"] for m in rows],
                "peer_context_available": any(
                    "PEER_CONTEXT" in m["text"]
                    for m in plugin.learning_context.get(target_scope, ())
                ),
            }
            await configure({"target_senders": ["unmatched"]})
            filtered = await plugin.store.call(
                "state", target_scope, plugin.settings["target_senders"]
            )
            report["target_capture"].update(
                changed_target_pending=filtered["scope"]["pending"],
                raw_rows_retained=len(
                    await plugin.store.call("batch", target_scope, 100)
                ),
                context_cleared_on_change=target_scope not in plugin.learning_context,
            )
            async with httpx.AsyncClient(
                transport=transport, base_url="http://native.test", headers=headers
            ) as client:
                cleared = await client.post(
                    "/api/plug/astrbot_plugin_ovenheart/action",
                    json={"action": "clear_messages", "scope": target_scope},
                )
                assert cleared.status_code == 200 and cleared.json()["status"] == "ok"
            await configure({"target_senders": []})
            if args.smoke:
                await configure(
                    {
                        "participation_mode": "observe",
                        "planner_provider_id": "controlled-smoke",
                        "planner_mode": "legacy",
                        "person_memory_enabled": False,
                        "native_memory_enabled": False,
                    }
                )
                controlled = AsyncMock(
                    return_value=SimpleNamespace(
                        completion_text=json.dumps(
                            {
                                "action": "observe",
                                "confidence": 0.9,
                                "reason": "Controlled native burst regression.",
                                "intent": "",
                                "wait_seconds": 0,
                            }
                        )
                    )
                )
                with patch.object(plugin.context, "llm_generate", controlled):
                    await submit(
                        [
                            incoming(
                                f"smoke-{i}", f"普通群聊素材第{i}条", f"member-{i}"
                            )
                            for i in range(3)
                        ]
                    )
                report["smoke_planner_calls"] = controlled.await_count
                report["smoke_window_sizes"] = [
                    len(w["messages"]) for w in plugin.participation.windows.values()
                ]
                # Replace only the provider completion; loader, permissions,
                # native Agent, persona/history and platform send stay real.
                from astrbot.api.message_components import At, Reply
                from astrbot.api.provider import LLMResponse

                await core.provider_manager.load_provider(
                    {
                        "id": "native-chat",
                        "enable": True,
                        "provider_type": "chat_completion",
                        "type": "openai_chat_completion",
                        "api_base": "http://127.0.0.1:9/v1",
                        "key": ["controlled-no-network"],
                        "model": "controlled-smoke",
                        "max_context_tokens": 32768,
                    }
                )
                provider = await core.provider_manager.get_provider_by_id("native-chat")
                generated = AsyncMock(
                    return_value=LLMResponse(
                        role="assistant", completion_text="收到，我在。"
                    )
                )
                await configure(
                    {
                        "participation_mode": "off",
                        "mentioned_bot_reply": True,
                        "mention_names": ["小夏", "夏夏"],
                        "selection_mode": "lexical",
                        "learning_enabled": False,
                    }
                )
                named = incoming("mention-name", "我想听听小夏的建议")
                alias = incoming("mention-alias", "刚才夏夏说的呢？")
                at_bot = incoming("mention-at", "小夏，你在吗？")
                at_bot.message_obj.message.insert(0, At(qq="bot"))
                quoted = incoming("mention-quoted", "这个说法怎样？")
                quoted.message_obj.message.insert(
                    0, Reply(id="old", sender_id="peer", message_str="小夏在吗")
                )
                with (
                    patch.object(provider, "text_chat", generated),
                    patch.object(plugin.context, "llm_generate", controlled),
                ):
                    for mention_event in (named, alias, at_bot, quoted):
                        mention_event.set_extra("enable_streaming", False)
                        mention_event.set_extra("selected_provider", "native-chat")
                        await submit([mention_event])
                report["smoke_mention"] = {
                    "provider_calls": generated.await_count,
                    "planner_calls": controlled.await_count
                    - report["smoke_planner_calls"],
                    "name_tag": named.get_extra("ovenheart_mention"),
                    "alias_tag": alias.get_extra("ovenheart_mention"),
                    "native_at_tag": at_bot.get_extra("ovenheart_mention"),
                    "quoted_woke": quoted.is_at_or_wake_command,
                }
                await configure({"context_mode": "structured"})
                question = incoming(
                    "structured-question", "STRUCTURED_EARLIER_QUESTION", "alice"
                )
                peer = incoming("structured-peer", "UNRELATED_PEER_TOPIC", "bob")
                trigger = incoming(
                    "structured-trigger", "小夏，请回答前面的问题", "alice"
                )
                trigger.message_obj.message.insert(0, At(qq="bot"))
                trigger.set_extra("enable_streaming", False)
                trigger.set_extra("selected_provider", "native-chat")
                with patch.object(provider, "text_chat", generated):
                    await submit([question, peer])
                    await submit([trigger])
                snapshot = trigger.get_extra("ovenheart_context_snapshot") or {}
                report["native_structured_context"] = (
                    "structured-question"
                    in [m["message_id"] for m in snapshot.get("messages", [])]
                    and "STRUCTURED_EARLIER_QUESTION" in str(generated.await_args)
                    and not trigger.is_stopped()
                )
                await configure(
                    {
                        "planner_mode": "maibot",
                        "planner_provider_id": "native-chat",
                        "participation_mode": "auto",
                        "participation_rhythm": "maibot",
                        "participation_frequency": 1,
                        "participation_debounce": 1,
                        "memory_enabled": True,
                        "native_memory_enabled": True,
                        "jev_enabled": False,
                        "person_memory_enabled": True,
                        "person_memory_min_messages": 30,
                        "learning_enabled": True,
                    }
                )
                await configure(
                    {
                        "participation_mode": "off",
                        "memory_summary_enabled": True,
                        "memory_summary_min_messages": 1,
                        "memory_summary_interval": 0,
                        "memory_summary_provider_id": "native-chat",
                    }
                )
                summary_source = incoming(
                    "native-summary", "我们周六一起整理文件", "bob"
                )
                summary_requests = []

                async def consolidate_completion(**kwargs):
                    summary_requests.append(str(kwargs))
                    if "episode segmentation engine" in str(kwargs):
                        payload = {
                            "episodes": [
                                {
                                    "title": "整理文件的约定",
                                    "summary": "群聊经历：周六整理文件",
                                    "paragraph_indices": [1],
                                    "participants": ["bob"],
                                    "keywords": ["文件"],
                                    "time_confidence": 1,
                                    "llm_confidence": 1,
                                }
                            ]
                        }
                    else:
                        payload = {
                            "summary": "群聊经历：周六整理文件",
                            "entities": ["文件"],
                            "relations": [],
                            "facts": [],
                            "evidence": [
                                {
                                    "source_id": "native-summary",
                                    "quote": "我们周六一起整理文件",
                                }
                            ],
                        }
                    return LLMResponse(
                        role="assistant",
                        completion_text=json.dumps(payload, ensure_ascii=False),
                    )

                before_summary_deliveries = len(deliveries)
                with patch.object(provider, "text_chat", consolidate_completion):
                    await submit([summary_source])
                    await asyncio.gather(
                        *tuple(plugin.memory_consolidation.busy.values())
                    )
                summary_scope, summary_persona, _ = await plugin.resolve_scope(
                    summary_source
                )
                summary_context = await plugin.room_memory.context(
                    summary_source, summary_scope, summary_persona, "文件"
                )
                report["native_auto_memories"] = {
                    "model_calls": len(summary_requests),
                    "episode_available": any(
                        m["type"] == "episode" for m in summary_context
                    ),
                    "no_visible_send": len(deliveries) == before_summary_deliveries,
                    "source_retained": any(
                        e["source_id"] == "native-summary"
                        for m in summary_context
                        for e in m["metadata"]["evidence"]
                    ),
                }
                await configure({"participation_mode": "auto"})
                planner_calls, reply_calls, planner_inputs = [], [], []
                summary_waiting, summary_release = asyncio.Event(), asyncio.Event()

                async def planner_completion(**kwargs):
                    if "episode segmentation engine" in (kwargs.get("prompt") or ""):
                        return LLMResponse(
                            role="assistant", completion_text='{"episodes": []}'
                        )
                    if "AstrBot原文来源校验" in (kwargs.get("prompt") or ""):
                        summary_waiting.set()
                        await summary_release.wait()
                        return LLMResponse(
                            role="assistant",
                            completion_text=json.dumps(
                                {
                                    "summary": "",
                                    "entities": [],
                                    "relations": [],
                                    "facts": [],
                                    "evidence": [],
                                }
                            ),
                        )
                    tools = kwargs.get("func_tool")
                    if tools and tools.get_tool("reply"):
                        index = len(planner_calls)
                        planner_calls.append(index)
                        planner_inputs.append(str(kwargs))
                        if index < 6:
                            source = "planner-new" if index == 5 else "planner-old"
                            target = next(
                                m
                                for window in plugin.participation.windows.values()
                                for m in window["messages"]
                                if m.get("message_id") == source
                            )
                            if index == 0:
                                return LLMResponse(
                                    role="assistant",
                                    tools_call_name=["remember_memory"],
                                    tools_call_ids=["remember-room-note"],
                                    tools_call_args=[
                                        {
                                            "content": "群聊话题：文件位置",
                                            "evidence": [
                                                {
                                                    "msg_id": str(target["sequence"]),
                                                    "quote": "先问文件在哪儿",
                                                }
                                            ],
                                            "relations": [
                                                {
                                                    "subject": "bob",
                                                    "predicate": "询问",
                                                    "object": "文件位置",
                                                }
                                            ],
                                        }
                                    ],
                                )
                            if index == 1:
                                return LLMResponse(
                                    role="assistant",
                                    tools_call_name=["search_memory"],
                                    tools_call_ids=["query-room-note"],
                                    tools_call_args=[{"query": "文件位置"}],
                                )
                            if index == 2:
                                return LLMResponse(
                                    role="assistant",
                                    tools_call_name=["remember_person_fact"],
                                    tools_call_ids=["remember-student"],
                                    tools_call_args=[
                                        {
                                            "msg_id": str(target["sequence"]),
                                            "facts": [
                                                {
                                                    "key": "occupation",
                                                    "value": "职业：学生",
                                                    "section": "identity_settings",
                                                    "stability": "stable",
                                                    "cardinality": "single",
                                                    "polarity": "positive",
                                                    "corrects": [],
                                                    "evidence": [
                                                        {
                                                            "source_id": "planner-old",
                                                            "quote": "我是学生",
                                                        }
                                                    ],
                                                }
                                            ],
                                        }
                                    ],
                                )
                            if index == 3:
                                return LLMResponse(
                                    role="assistant",
                                    tools_call_name=["query_person_profile"],
                                    tools_call_ids=["query-student"],
                                    tools_call_args=[
                                        {"msg_id": str(target["sequence"])}
                                    ],
                                )
                            return LLMResponse(
                                role="assistant",
                                tools_call_name=["reply"],
                                tools_call_ids=[f"planner-{index}"],
                                tools_call_args=[
                                    {
                                        "msg_id": str(target["sequence"]),
                                        "reply_reference": f"具体回应 {source} 的问题。",
                                    }
                                ],
                            )
                        return LLMResponse(
                            role="assistant",
                            tools_call_name=["stop"],
                            tools_call_ids=["planner-stop"],
                            tools_call_args=[{}],
                        )
                    reply_calls.append(str(kwargs))
                    return LLMResponse(
                        role="assistant",
                        completion_text=(
                            "先看看下载目录。"
                            if len(reply_calls) == 1
                            else "另一个问题也说来听听。"
                        ),
                    )

                report["native_planner_debug"] = []
                with (
                    patch.object(provider, "text_chat", planner_completion),
                    patch.object(
                        plugin,
                        "debug",
                        side_effect=lambda name, **details: report[
                            "native_planner_debug"
                        ].append({"name": name, **details}),
                    ),
                ):
                    await plugin.capture(
                        incoming("native-summary-wait", "嗯嗯，知道了", "bob")
                    )
                    await asyncio.wait_for(summary_waiting.wait(), 10)
                    planner_old = incoming(
                        "planner-old", "我是学生，先问文件在哪儿", "bob"
                    )
                    planner_new = incoming("planner-new", "我还想问另一个问题", "alice")
                    planner_new.set_extra("selected_provider", "native-chat")
                    try:
                        await submit([planner_old, planner_new], wait_background=False)
                        report["native_auto_memories"]["reply_during_summary_wait"] = (
                            not summary_release.is_set() and len(reply_calls) == 2
                        )
                    finally:
                        summary_release.set()
                        await asyncio.gather(
                            *tuple(plugin.memory_consolidation.busy.values())
                        )
                actual = [d["text"] for d in deliveries if d["source"] == "planner-new"]
                cid = (
                    await plugin.context.conversation_manager.get_curr_conversation_id(
                        planner_new.unified_msg_origin
                    )
                )
                saved = await plugin.context.conversation_manager.get_conversation(
                    planner_new.unified_msg_origin, cid
                )
                saved_history = json.dumps(
                    json.loads(saved.history or "[]"), ensure_ascii=False
                )
                report["native_continuous_planner"] = {
                    "planner_calls": len(planner_calls),
                    "reply_calls": len(reply_calls),
                    "replies": actual,
                    "receipt_returned": len(planner_inputs) == 7
                    and "先看看下载目录。" in planner_inputs[5]
                    and "另一个问题也说来听听。" in planner_inputs[6],
                    "history_saved": "先看看下载目录。" in saved_history
                    and "另一个问题也说来听听。" in saved_history,
                    "stopped": not plugin.participation.planner.sessions,
                    "no_errors": not planner_new.trace._summary["has_errors"],
                    "last_receipt": planner_new.get_extra("ovenheart_planner_receipt"),
                    "planner_status": planner_new.get_extra("ovenheart_planner_status"),
                    "source_restored": planner_new.message_obj.message_id
                    == "planner-new"
                    and planner_new.get_sender_id() == "alice",
                    "person_memory_shared": len(planner_inputs) == 7
                    and "职业：学生" in planner_inputs[4]
                    and "职业：学生" in reply_calls[0],
                    "person_memory_temporary": "职业：学生" not in saved_history,
                    "room_memory_shared": len(planner_inputs) == 7
                    and "群聊话题：文件位置" in planner_inputs[2]
                    and "群聊话题：文件位置" in reply_calls[0],
                    "room_memory_temporary": "群聊话题：文件位置" not in saved_history,
                    "episode_shared": bool(planner_inputs)
                    and bool(reply_calls)
                    and "群聊经历：周六整理文件" in planner_inputs[0]
                    and "群聊经历：周六整理文件" in reply_calls[0],
                    "episode_temporary": "群聊经历：周六整理文件" not in saved_history,
                }
                planner_scope = (await plugin.resolve_scope(planner_new))[0]
                report["native_continuous_planner"]["decisions"] = (
                    await plugin.store.call("state", planner_scope)
                )["decisions"]
                await configure(
                    {
                        "participation_mode": "off",
                        "participation_frequency": 0,
                        "learning_enabled": False,
                        "memory_summary_enabled": False,
                    }
                )
                direct_inputs, direct_outputs = [], []

                async def direct_completion(**kwargs):
                    tools = kwargs.get("func_tool")
                    if tools and tools.get_tool("reply"):
                        index = len(direct_inputs)
                        direct_inputs.append(str(kwargs))
                        names = ("search_memory", "reply", "stop")
                        arguments = (
                            {"query": "文件位置"},
                            {
                                "msg_id": "1",
                                "reply_reference": "先处理被直接叫到的文件问题。",
                                "reply_style": "简短表达",
                            },
                            {},
                        )
                        return LLMResponse(
                            role="assistant",
                            tools_call_name=[names[index]],
                            tools_call_ids=[f"direct-plan-{index}"],
                            tools_call_args=[arguments[index]],
                        )
                    direct_outputs.append(str(kwargs))
                    return LLMResponse(
                        role="assistant", completion_text="文件在下载目录，你看看。"
                    )

                direct_event = incoming(
                    "direct-planner-native", "帮我找一下文件", group="direct-probe"
                )
                direct_event.message_obj.message.insert(0, At(qq="bot"))
                direct_event.set_extra("selected_provider", "native-chat")
                with patch.object(provider, "text_chat", direct_completion):
                    await submit([direct_event])
                direct_cid = (
                    await plugin.context.conversation_manager.get_curr_conversation_id(
                        direct_event.unified_msg_origin
                    )
                )
                direct_saved = (
                    await plugin.context.conversation_manager.get_conversation(
                        direct_event.unified_msg_origin, direct_cid
                    )
                )
                direct_history = json.dumps(
                    json.loads(direct_saved.history or "[]"), ensure_ascii=False
                )
                report["native_direct_planner"] = {
                    "planner_calls": len(direct_inputs),
                    "reply_calls": len(direct_outputs),
                    "replies": [
                        d["text"]
                        for d in deliveries
                        if d["source"] == "direct-planner-native"
                    ],
                    "trigger_explicit": bool(direct_inputs)
                    and '"route": "direct"' in direct_inputs[0],
                    "receipt_returned": len(direct_inputs) == 3
                    and "文件在下载目录，你看看。" in direct_inputs[2]
                    and "native_send_hook" in direct_inputs[2],
                    "reference_applied": bool(direct_outputs)
                    and "先处理被直接叫到的文件问题。" in direct_outputs[0],
                    "history_saved": "文件在下载目录，你看看。" in direct_history,
                    "temporary_not_saved": "reply_reference" not in direct_history
                    and "先处理被直接叫到的文件问题。" not in direct_history,
                    "stopped": not plugin.participation.planner.sessions,
                    "no_errors": not direct_event.trace._summary["has_errors"],
                }
            if not args.smoke:
                await core.persona_mgr.update_persona(
                    "default",
                    system_prompt="你叫小夏，是自然聊天的群友。名字和身份固定，不把群友的经历当成自己。用简短的中文回应当前对话，不展示内部分析。",
                    tools=[],
                    skills=[],
                )
                configs = [
                    {
                        "id": "native-chat",
                        "enable": True,
                        "provider_type": "chat_completion",
                        "type": "openai_chat_completion",
                        "api_base": args.chat_url,
                        "key": [os.environ["OVENHEART_CHAT_API_KEY"]],
                        "model": args.chat_model,
                        "timeout": args.chat_timeout,
                        "custom_extra_body": {"temperature": 0.2},
                        "max_context_tokens": 32768,
                    },
                    {
                        "id": "native-embed",
                        "enable": True,
                        "type": "openai_embedding",
                        "embedding_api_base": "https://api.siliconflow.cn/v1",
                        "embedding_api_key": os.environ["SILICONFLOW_API_KEY"],
                        "embedding_model": "BAAI/bge-m3",
                        "timeout": 20,
                    },
                    {
                        "id": "native-rank",
                        "enable": True,
                        "type": "vllm_rerank",
                        "rerank_api_base": "https://api.siliconflow.cn",
                        "rerank_api_suffix": "/v1/rerank",
                        "rerank_api_key": os.environ["SILICONFLOW_API_KEY"],
                        "rerank_model": "BAAI/bge-reranker-v2-m3",
                        "timeout": 20,
                    },
                ]
                for config in configs:
                    await core.provider_manager.load_provider(config)
                await configure(
                    {
                        "memory_enabled": False,
                        "activation_mode": "auto",
                        "embedding_provider_id": "native-embed",
                        "rerank_provider_id": "native-rank",
                    }
                )
                corpus_path = ROOT / "tests/fixtures/conversation_context.json"
                corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
                report["corpus_sha256"] = hashlib.sha256(
                    corpus_path.read_bytes()
                ).hexdigest()
                original_generate = plugin.context.llm_generate
                learning_started = time.monotonic()

                async def observe_learning(**kwargs):
                    """Retain only synthetic review sources and target context.

                    Args:
                        **kwargs: Native generation request without provider keys.

                    Returns:
                        Unchanged native provider response.
                    """
                    payload = json.loads(kwargs["prompt"])
                    if "candidates" in payload and "observations" in payload:
                        report["learning_review_inputs"].append(
                            {
                                key: payload[key]
                                for key in (
                                    "observations",
                                    "conversation_context",
                                    "candidates",
                                )
                            }
                        )
                    call = {
                        "batch": len(report["learning"]) + 1,
                        "phase": "review"
                        if "candidates" in payload
                        else "vocabulary"
                        if payload["enabled_kinds"] == ["jargon"]
                        else "habits",
                        "started_seconds": round(
                            time.monotonic() - learning_started, 3
                        ),
                        "status": "running",
                    }
                    report["learning_calls"].append(call)
                    started = time.monotonic()
                    try:
                        result = await original_generate(**kwargs)
                        call["status"] = "success"
                        return result
                    except asyncio.CancelledError:
                        call["status"] = "cancelled"
                        raise
                    except Exception as exc:
                        call["status"] = "error"
                        call["error_type"] = type(exc).__name__
                        raise
                    finally:
                        call["duration_seconds"] = round(time.monotonic() - started, 3)
                        persist("learning_call")

                plugin.context.llm_generate = observe_learning
                for index, batch in enumerate(corpus["batches"]):
                    await submit(
                        [
                            incoming(
                                f"batch-{index}-{number}", item["text"], item["sender"]
                            )
                            for number, item in enumerate(batch)
                        ]
                    )
                    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
                    outcome = await plugin.learn(scope)
                    report["learning"].append(
                        {
                            "batch": index + 1,
                            "outcome": outcome,
                            "state": await plugin.store.call("state", scope),
                        }
                    )
                    persist(f"learned_{index + 1}")
                plugin.context.llm_generate = original_generate
                await configure(
                    {
                        "learning_enabled": False,
                        "participation_mode": "auto",
                    }
                )
                await submit(
                    [
                        incoming(f"auto-{i}", text, sender)
                        for i, (sender, text) in enumerate(
                            [
                                ("alice", "今天又把事情搞砸了，心里堵得慌。"),
                                ("bob", "我现在不需要解决方案，只想有人听一听。"),
                                ("alice", "群里谁有空，陪我说说好不好？"),
                            ]
                        )
                    ]
                )
                persist("autonomous_checked")
                report["participation_decisions"] = (
                    await plugin.store.call("state", scope)
                )["decisions"]
                report["participation_window_sizes"] = [
                    len(w["messages"]) for w in plugin.participation.windows.values()
                ]
                await submit([incoming("direct", "/你叫什么名字？")])
                persist("direct_checked")
                denied = incoming(
                    "denied", "有谁能陪我说说？", group="disabled-session"
                )
                await sp.put_async(
                    scope="umo",
                    scope_id=denied.unified_msg_origin,
                    key="session_service_config",
                    value={"session_enabled": False},
                )
                await submit([denied])
                cid = await core.conversation_manager.get_curr_conversation_id(
                    incoming("lookup", "").unified_msg_origin
                )
                conversation = (
                    await core.conversation_manager.get_conversation(
                        incoming("lookup", "").unified_msg_origin, cid
                    )
                    if cid
                    else None
                )
                raw_history = json.loads(conversation.history) if conversation else []
                # Export conversation text, not the provider's reasoning blocks.
                report["native_history"] = [
                    message
                    | {
                        "content": [
                            p for p in message["content"] if p.get("type") == "text"
                        ]
                    }
                    if isinstance(message.get("content"), list)
                    else message
                    for message in raw_history
                ]
                report["retrieval"] = plugin.retriever.status
            panel = report["panel"]
            report["checks"] = {
                "native_group_context_ordering": plugin.group_context.status()[
                    "ordered"
                ],
                "command_excluded": report["command_pending"] == 0,
                "panel_anonymous_rejected": panel["anonymous_status"] == 401,
                "panel_native_state": panel["state_status"] == 200
                and panel["state_ok"],
                "panel_view_and_bridge": panel["entry_status"]
                == panel["html_status"]
                == panel["script_status"]
                == 200
                and panel["bridge_present"],
                "panel_scoped_asset_rejected": panel["wrong_scope_status"] == 401,
                "panel_settings_applied": plugin.settings["guidance_delivery"]
                == "dual",
                "native_target_only_material": report["target_capture"][
                    "source_senders"
                ]
                == ["mentor", "mentor"],
                "native_neighbor_context_available": report["target_capture"][
                    "peer_context_available"
                ],
                "native_target_count_refresh": report["target_capture"][
                    "changed_target_pending"
                ]
                == 0
                and report["target_capture"]["raw_rows_retained"] == 2,
                "native_context_cleared_on_target_change": report["target_capture"][
                    "context_cleared_on_change"
                ],
            }
            if args.smoke:
                direct_planner = report["native_direct_planner"]
                report["checks"]["native_direct_planner"] = (
                    direct_planner["planner_calls"] == 3
                    and direct_planner["reply_calls"] == 1
                    and direct_planner["replies"] == ["文件在下载目录，你看看。"]
                    and all(
                        direct_planner[key]
                        for key in (
                            "trigger_explicit",
                            "receipt_returned",
                            "reference_applied",
                            "history_saved",
                            "temporary_not_saved",
                            "stopped",
                            "no_errors",
                        )
                    )
                )
                continuous = report["native_continuous_planner"]
                report["checks"]["native_continuous_planner"] = (
                    continuous["planner_calls"] == 7
                    and continuous["reply_calls"] == 2
                    and continuous["replies"]
                    == ["先看看下载目录。", "另一个问题也说来听听。"]
                    and continuous["receipt_returned"]
                    and continuous["history_saved"]
                    and continuous["stopped"]
                    and continuous["no_errors"]
                    and continuous["source_restored"]
                    and continuous["person_memory_shared"]
                    and continuous["person_memory_temporary"]
                    and continuous["room_memory_shared"]
                    and continuous["room_memory_temporary"]
                    and continuous["episode_shared"]
                    and continuous["episode_temporary"]
                )
                report["checks"]["native_auto_memories"] = (
                    report["native_auto_memories"]["model_calls"] == 2
                    and report["native_auto_memories"]["episode_available"]
                    and report["native_auto_memories"]["no_visible_send"]
                    and report["native_auto_memories"]["source_retained"]
                    and report["native_auto_memories"]["reply_during_summary_wait"]
                )
                report["checks"]["native_structured_context"] = report[
                    "native_structured_context"
                ]
                report["checks"]["native_burst_counted"] = (
                    report["smoke_planner_calls"] == 1
                )
                mention = report["smoke_mention"]
                mention_events = {
                    e["id"]: e
                    for e in report["events"]
                    if e["id"].startswith("mention-")
                }
                report["checks"].update(
                    native_mention_once=all(
                        len(mention_events[key]["replies"]) == 1
                        for key in ("mention-name", "mention-alias", "mention-at")
                    )
                    and mention["provider_calls"] == 3,
                    native_mentions_skip_planner=mention["planner_calls"] == 0,
                    native_mention_tags=mention["name_tag"] == "小夏"
                    and mention["alias_tag"] == "夏夏"
                    and not mention["native_at_tag"],
                    native_quoted_name_passive=not mention["quoted_woke"]
                    and not mention_events["mention-quoted"]["replies"],
                    native_mention_no_errors=all(
                        not e["trace"]["has_errors"] for e in mention_events.values()
                    ),
                )
            if not args.smoke:
                auto = [e for e in report["events"] if e["id"].startswith("auto-")]
                direct = next(e for e in report["events"] if e["id"] == "direct")
                denied_record = next(e for e in report["events"] if e["id"] == "denied")
                history = json.dumps(raw_history, ensure_ascii=False)
                report["checks"].update(
                    native_learning_review_recorded=all(
                        batch["state"]["runs"][0]["review"].get("enabled")
                        and bool(batch["state"]["runs"][0]["review"].get("candidates"))
                        for batch in report["learning"]
                    )
                    and bool(report["learning"]),
                    learning_completed=all(
                        r["outcome"]["status"] == "success" for r in report["learning"]
                    ),
                    one_autonomous_send=sum(len(e["replies"]) for e in auto) == 1,
                    autonomous_model_executed=any(
                        e["trace"]["has_models"] for e in auto
                    ),
                    direct_identity=any("小夏" in text for text in direct["replies"]),
                    denied_no_model=not denied_record["trace"]["has_models"],
                    denied_no_send=not denied_record["replies"],
                    history_exists=bool(report["native_history"]),
                    history_excludes_temporary=all(
                        marker not in history
                        for marker in (
                            "OvenHeart turn context",
                            "reply_goal",
                            "Persona supplementary guidance",
                        )
                    ),
                    no_pipeline_errors=all(
                        not e["trace"]["has_errors"] for e in report["events"]
                    ),
                )
            if not all(report["checks"].values()):
                raise AssertionError("Native acceptance checks failed")
            persist("complete")
        except Exception as exc:
            persist("error", type(exc).__name__)
            raise
        finally:
            if hasattr(core, "dashboard_shutdown_event"):
                await core.stop()
            if start:
                start.cancel()
                await asyncio.gather(start, return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--chat-url", default=os.environ.get("OVENHEART_CHAT_BASE_URL"))
    parser.add_argument("--chat-model", default="cn:deepseek-v4-flash")
    parser.add_argument("--chat-timeout", type=int, default=120)
    try:
        args = parser.parse_args()
        if not args.smoke and not args.chat_url:
            parser.error(
                "--chat-url or OVENHEART_CHAT_BASE_URL is required outside smoke mode"
            )
        asyncio.run(main(args))
    except Exception as exc:
        print(
            f"Native evaluation failed ({type(exc).__name__}); provider diagnostics are not printed.",
            file=sys.stderr,
        )
        sys.exit(1)
