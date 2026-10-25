"""Run the pinned real MaiBot replyer in a separate instance, never send IM.

Private provider/persona input is read only from stdin and stays in memory.
The report contains synthetic input, visible replies and bounded metadata only.
This compares the replyer stage; it does not establish whole-bot parity.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

PINNED = "5c7637e6913c84d1c3d5e742fa9e95a7e70d3501"


class ReferenceFailure(RuntimeError):
    """Only sanitized source errors may cross the private-input boundary."""


def reference_commit(source):
    if (source / ".git").exists():
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "diff",
                "--quiet",
                "HEAD",
                "--",
                "src",
                "prompts",
                "locales",
                "pyproject.toml",
            ],
            check=True,
        )
        return commit
    manifest = json.loads(
        (source / ".reference-source.json").read_text(encoding="utf-8")
    )
    if "src/chat/replyer/maisaka_generator_base.py" not in manifest["files"]:
        raise ValueError("Reference archive does not include the original replyer")
    for relative, expected in manifest["files"].items():
        path = (source / relative).resolve()
        if (
            not path.is_relative_to(source)
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError("Reference archive source verification failed")
    return manifest["commit"]


async def run(args, packet):
    source = args.source.resolve()
    commit = await asyncio.to_thread(reference_commit, source)
    if commit != PINNED:
        raise ValueError("Reference source differs from the pinned MaiBot commit")
    instance = args.instance.resolve()
    if instance == source or source in instance.parents:
        raise ValueError("Reference instance must be outside the source repository")
    instance.mkdir(parents=True, exist_ok=True)
    os.environ["MAIBOT_INSTANCE_DIR"] = str(instance)
    os.chdir(instance)
    sys.path.insert(0, str(source))

    from src.chat.message_receive.chat_manager import chat_manager
    from src.chat.message_receive.message import SessionMessage
    from src.chat.replyer import maisaka_generator_base
    from src.chat.replyer.maisaka_expression_selector import maisaka_expression_selector
    from src.chat.replyer.maisaka_generator import MaisakaReplyGenerator
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
    from src.maisaka.context.messages import SessionBackedMessage

    provider = packet["provider"]
    if provider["type"] not in ("openai_chat_completion", "openai_responses"):
        raise ValueError("Reference probe supports only matching OpenAI transports")
    query = packet["query"]
    if not isinstance(query, str) or not 1 <= len(query) <= 1200:
        raise ValueError("Synthetic query must contain 1-1200 characters")
    for key in ("id", "api_base", "model"):
        if not isinstance(provider.get(key), str) or not provider[key]:
            raise ValueError("Provider input is incomplete")
    keys = provider.get("key")
    if not isinstance(keys, list) or not keys or not isinstance(keys[0], str):
        raise ValueError("Provider credentials are missing")
    persona = packet["persona"]
    if not isinstance(persona, str):
        raise ValueError("Persona input is invalid")
    body = provider.get("custom_extra_body") or {}
    clock_match = re.search(
        r"Current datetime: (\d{4}-\d{2}-\d{2} \d{2}:\d{2})",
        packet.get("ovenheart", {}).get("time_context") or "",
    )
    replay_time = (
        datetime.strptime(clock_match[1], "%Y-%m-%d %H:%M")
        if clock_match
        else datetime.now()
    )
    if clock_match:

        class ReplayClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return replay_time if tz is None else replay_time.astimezone(tz)

        maisaka_generator_base.datetime = ReplayClock
    api = APIProvider(
        name="native-reference",
        base_url=provider["api_base"],
        api_key=keys[0],
        client_type="openai_responses"
        if provider["type"] == "openai_responses"
        else "openai",
        timeout=90,
        max_retry=0,
        default_headers=provider.get("custom_headers") or {},
    )
    reference_model = ModelInfo(
        model_type="llm",
        name="native-reference",
        model_identifier=provider["model"],
        api_provider=api.name,
        send_temperature=False,
        max_tokens=body.get("max_tokens") or 4096,
        extra_params={"body": body},
    )
    model_config.api_providers = [api]
    model_config.models = [reference_model]
    model_config.model_task_config.replyer = TaskConfig(
        model_list=[reference_model.name],
        max_tokens=reference_model.max_tokens,
        hard_timeout=90,
    )
    global_config.personality.reply_style = (
        packet.get("profile", {}).get("reply_style") or ""
    )
    global_config.debug.save_maisaka_reasoning = False

    class NativeIdentityReplyer(MaisakaReplyGenerator):
        def _build_personality_prompt(self):
            return persona

        def _build_request_messages(self, **kwargs):
            messages = super()._build_request_messages(**kwargs)
            normalized = " ".join(query.split())
            self.input_verified = any(
                normalized in " ".join(get_item_text(item).split()) for item in messages
            )
            self.persona_verified = any(
                persona in get_item_text(item) for item in messages
            )
            if not self.input_verified or not self.persona_verified:
                raise ValueError(
                    "Reference input or persona was filtered before the model request"
                )
            return messages

    session = await chat_manager.get_or_create_session(
        platform="ovenheart-reference",
        user_id="synthetic-human",
        group_id="synthetic-room",
    )
    message = SessionMessage(
        "synthetic-reference-input", replay_time, platform="ovenheart-reference"
    )
    message.session_id = session.session_id
    message.message_info = MessageInfo(
        user_info=UserInfo("synthetic-human", "合成群友"),
        group_info=GroupInfo("synthetic-room", "隔离对照群"),
    )
    message.raw_message = MessageSequence([TextComponent(text=query)])
    await message.process(
        enable_heavy_media_analysis=False, enable_voice_transcription=False
    )
    replyer = NativeIdentityReplyer(chat_stream=session)
    traits = packet.get("traits", [])
    expressions = [
        {"situation": trait["situation"], "style": trait["content"]}
        for trait in traits
        if trait["kind"] == "expression"
    ]
    requirements = [
        str(packet.get("profile", {}).get(key) or "")
        for key in ("identity", "behavior")
    ] + [
        f"当{trait['situation']}时：{trait['content']}"
        for trait in traits
        if trait["kind"] != "expression"
    ]
    habits = maisaka_expression_selector._build_expression_habits_block(expressions)
    history = [
        SessionBackedMessage.from_session_message(
            message,
            raw_message=message.raw_message,
            visible_text=message.processed_plain_text,
        )
    ]
    success, result = await asyncio.wait_for(
        replyer.generate_reply_with_context(
            chat_history=history,
            reply_message=message,
            stream_id=session.session_id,
            expression_habits=habits,
            selected_expression_ids=[trait["id"] for trait in traits],
            reply_tool_args={
                "_plugin_reply_prompt": "\n".join(
                    value for value in requirements if value
                )
            },
        ),
        timeout=120,
    )
    if not success:
        reason = (
            result.error_message or "Reference replyer did not produce a visible reply"
        )
        for secret in [
            *keys,
            persona,
            *(provider.get("custom_headers") or {}).values(),
        ]:
            if secret:
                reason = reason.replace(str(secret), "[redacted]")
        reason = re.sub(r"https?://\S+", "[endpoint]", reason)
        raise ReferenceFailure(reason[:350])
    return {
        "stage": "replyer_only",
        "source_commit": commit,
        "provider_id": provider["id"],
        "configured_model": provider["model"],
        "model": result.completion.model_name,
        "persona_sha256": hashlib.sha256(persona.encode()).hexdigest(),
        "query": query,
        "reply": result.completion.response_text,
        "input_tokens": result.completion.prompt_tokens,
        "output_tokens": result.completion.completion_tokens,
        "seconds": result.metrics.overall_ms / 1000,
        "reference_max_output_tokens": reference_model.max_tokens,
        "request_chars": len(result.completion.request_prompt),
        "persona_literal_in_prompt_dump": persona in result.completion.request_prompt,
        "query_literal_in_prompt_dump": query in result.completion.request_prompt,
        "query_in_request": replyer.input_verified,
        "persona_in_request": replyer.persona_verified,
        "clock_frozen_to_native_minute": bool(clock_match),
        "selected_expression_ids": result.selected_expression_ids,
        "traits": [
            {
                "id": trait["id"],
                "kind": trait["kind"],
                "example_count": len(trait.get("examples", [])),
            }
            for trait in traits
        ],
        "ovenheart": {
            key: packet.get("ovenheart", {}).get(key)
            for key in (
                "reply",
                "provider_id",
                "model",
                "usage",
                "selected_ids",
                "seconds",
                "time_context",
            )
        },
        "limitations": [
            "No Planner, action tools, learning, memory or IM delivery is exercised.",
            "Native persona is returned verbatim by the identity adapter.",
            "Native selector choices are fixed for this replyer-only comparison.",
            "Only the isolated source clock is fixed to the native example's captured minute.",
            "Reference expression.style maps native content; separate native evidence examples are not copied into that field.",
            "Reference output cap defaults to 4096 when native config has no explicit cap.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--instance", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    try:
        packet = json.load(sys.stdin)
        report = asyncio.run(run(args, packet))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            json.dumps(
                {"phase": "complete", "output": str(args.output)}, ensure_ascii=True
            )
        )
    except Exception as exc:
        # Do not expose provider errors or validation inputs containing secrets.
        print(
            json.dumps(
                {
                    "phase": "failed",
                    "error": type(exc).__name__,
                    "reason": str(exc) if isinstance(exc, ReferenceFailure) else None,
                    "location": [
                        {
                            "file": Path(frame.filename).name,
                            "line": frame.lineno,
                            "function": frame.name,
                        }
                        for frame in traceback.extract_tb(exc.__traceback__)[-4:]
                    ],
                }
            ),
            file=sys.stderr,
        )
        sys.exit(1)
