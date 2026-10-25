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
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PINNED = "5c7637e6913c84d1c3d5e742fa9e95a7e70d3501"


async def run(args, packet):
    source = args.source.resolve()
    commit = (
        await asyncio.to_thread(
            subprocess.check_output,
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
        )
    ).strip()
    if commit != PINNED:
        raise ValueError("Reference source differs from the pinned MaiBot commit")
    instance = args.instance.resolve()
    if instance == source or source in instance.parents:
        raise ValueError("Reference instance must be outside the source repository")
    instance.mkdir(parents=True, exist_ok=True)
    os.environ["MAIBOT_INSTANCE_DIR"] = str(instance)
    os.chdir(instance)
    sys.path.insert(0, str(source))

    from src.chat.replyer.maisaka_generator import MaisakaReplyGenerator
    from src.common.data_models.message_component_data_model import (
        MessageSequence,
        TextComponent,
    )
    from src.config.config import global_config, model_config
    from src.config.model_configs import APIProvider, ModelInfo, TaskConfig
    from src.maisaka.context.messages import ContentContextMessage

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

    replyer = NativeIdentityReplyer()
    history = [
        ContentContextMessage(
            raw_message=MessageSequence([TextComponent(text=query)]),
            visible_text=query,
            timestamp=datetime.now(),
        )
    ]
    success, result = await asyncio.wait_for(
        replyer.generate_reply_with_context(chat_history=history), timeout=120
    )
    if not success:
        raise RuntimeError("Reference replyer did not produce a visible reply")
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
        "selected_expression_ids": result.selected_expression_ids,
        "limitations": [
            "No Planner, action tools, learning, memory or IM delivery is exercised.",
            "Native persona is returned verbatim by the identity adapter.",
            "Reference expressions are empty; compare only with an empty native selection.",
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
            json.dumps({"phase": "failed", "error": type(exc).__name__}),
            file=sys.stderr,
        )
        sys.exit(1)
