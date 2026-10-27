"""Native context compression and semantic recall using MaiBot's pure algorithms."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from astrbot.api import logger

from ..vendor.maibot.mid_term import (
    MID_TERM_MEMORY_DEFAULT_RECALL_THRESHOLD,
    MemorySummaryMessage,
    MidTermMemoryRecallCandidate,
    _build_summary_message_id,
    _format_mid_term_memory_reference,
    _parse_summary_response,
    _select_best_recall_candidate,
)
from .memory_embedding import EmbeddingAPIAdapter


class MidTermMemory:
    def __init__(self, plugin):
        self.plugin = plugin
        self.busy = {}
        self.attempted = {}
        self.seen = {}
        self.template = (
            Path(__file__).parent.parent / "vendor/maibot/mid_term_summary.txt"
        ).read_text(encoding="utf-8")

    async def permitted(self, event, scope, persona, learning=False):
        return self.plugin.settings[
            "midterm_memory_enabled"
        ] and await self.plugin.room_memory.permitted(event, scope, persona, learning)

    async def observe(self, event, scope, messages, selected):
        if scope in self.busy or time.monotonic() - self.attempted.get(scope, 0) < 600:
            return
        if not selected:
            return
        first = next(
            (
                i
                for i, row in enumerate(messages)
                if row["source"] == selected[0]["source"]
            ),
            0,
        )
        removed = [
            row
            for row in messages[:first]
            if not row.get("self") and row["text"].strip()
        ]
        if len(removed) < 12:
            return
        _, persona, _ = await self.plugin.resolve_scope(event)
        if not await self.permitted(event, scope, persona, True):
            return
        self.attempted[scope] = time.monotonic()
        self.busy[scope] = self.plugin.track(
            self.compress(event, scope, persona, removed)
        )

    async def observe_native(self, event, scope, persona):
        if self.plugin.conversation.active(event) or scope in self.busy:
            return
        if time.monotonic() - self.attempted.get(scope, 0) < 600:
            return
        if not await self.permitted(event, scope, persona, True):
            return
        self.seen[scope] = self.seen.get(scope, 0) + 1
        if self.seen[scope] % 12:
            return
        cid = (
            await self.plugin.context.conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            or ""
        )
        if not cid:
            return
        messages = await self.plugin.conversation._seed(event, cid, limit=160)
        prefixes = [
            "/",
            *self.plugin.context.get_config(event.unified_msg_origin).get(
                "wake_prefix", []
            ),
        ]
        messages = [
            row
            for row in messages
            if not any(prefix and row["text"].startswith(prefix) for prefix in prefixes)
        ]
        # Native mode reuses AstrBot's actual transcript. The latest thirty are
        # the existing native planner window, rather than a second chat ledger.
        await self.observe(event, scope, messages, messages[-30:])

    async def compress(self, event, scope, persona, removed):
        revision = self.plugin.settings_revision
        binding = event.get_extra("ovenheart_context_binding")
        try:
            if not await self.permitted(event, scope, persona, True):
                return
            cid = (
                await self.plugin.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
                or ""
            )
            status = await self.plugin.room_memory.call(
                scope, "midterm_records", cid, 10, event=event
            )
            settings = self.plugin.settings
            removed = [
                r
                for r in removed
                if r["time"] > status["cutoff"]
                and r["sender_id"] not in settings["blocked_senders"]
                and (
                    not settings["target_senders"]
                    or r["sender_id"] in settings["target_senders"]
                )
            ]
            if len(removed) < 12:
                return
            if revision != self.plugin.settings_revision or not await self.permitted(
                event, scope, persona, True
            ):
                return
            # Bound source text without inventing extra IM messages or copying
            # the whole transcript into a second history database.
            batch, chars = [], 0
            for row in removed:
                text = row["text"][: 16000 - chars]
                if not text:
                    break
                batch.append(row | {"text": text})
                chars += len(text)
            participants = list(dict.fromkeys(r["sender_id"] for r in batch))
            time_range = " ~ ".join(
                datetime.fromtimestamp(t, timezone.utc).isoformat()
                for t in (batch[0]["time"], batch[-1]["time"])
            )
            provider = (
                settings["memory_summary_provider_id"]
                or settings["provider_id"]
                or await self.plugin.context.get_current_chat_provider_id(
                    event.unified_msg_origin
                )
            )
            response = await self.plugin.auxiliary.generate(
                "midterm_summary",
                chat_provider_id=provider,
                system_prompt=self.template.format(
                    time_range=time_range, participants_text="、".join(participants)
                ),
                prompt=json.dumps(
                    [
                        {
                            "source_id": r["source"],
                            "sender_id": r["sender_id"],
                            "text": r["text"],
                        }
                        for r in batch
                    ],
                    ensure_ascii=False,
                ),
            )
            parsed = _parse_summary_response(response.completion_text)
            if parsed is None:
                return
            embedding = EmbeddingAPIAdapter(
                self.plugin.context,
                settings["embedding_provider_id"],
                asyncio.get_running_loop(),
            )
            cues = []
            if embedding.available and parsed.recall_cues:
                try:
                    vectors = await embedding.encode(parsed.recall_cues)
                    cues = [
                        {"text": cue, "embedding": vector.tolist()}
                        for cue, vector in zip(parsed.recall_cues, vectors, strict=True)
                    ]
                except (RuntimeError, ValueError) as exc:
                    logger.warning(
                        "聊天回想线索未嵌入，保留文字摘要 (%s)", type(exc).__name__
                    )
            if revision != self.plugin.settings_revision or not await self.permitted(
                event, scope, persona, True
            ):
                return
            if binding and not await self.plugin.conversation.valid(
                event, scope, binding[1]
            ):
                return
            if (
                await self.plugin.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
                or ""
            ) != cid:
                return
            payload = {
                "id": _build_summary_message_id(
                    timestamp=datetime.fromtimestamp(batch[-1]["time"], timezone.utc),
                    time_range=time_range,
                    participants=participants,
                    summary=parsed.summary,
                ),
                "cid": cid,
                "end": batch[-1]["time"],
                "summary": parsed.summary,
                "time_range": time_range,
                "participants": participants,
                "recall_cues": cues,
                "embedding_key": embedding.get_embedding_configuration_key(),
            }
            evidence = [
                {
                    "source_id": r["source"],
                    "sender": r["sender_id"],
                    "quote": r["text"][:160],
                    "created": r["time"],
                }
                for r in batch
            ]
            await self.plugin.room_memory.call(
                scope, "midterm_store", payload, evidence, event=event
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("聊天回想未完成，保留现有记录 (%s)", type(exc).__name__)
        finally:
            self.busy.pop(scope, None)

    async def context(self, event, scope, persona, messages):
        if not await self.permitted(event, scope, persona):
            return []
        embedding = EmbeddingAPIAdapter(
            self.plugin.context,
            self.plugin.settings["embedding_provider_id"],
            asyncio.get_running_loop(),
        )
        if not embedding.available:
            return []
        query = "\n".join(r["text"][-360:] for r in messages[-12:] if r.get("text"))[
            -2400:
        ]
        if not query:
            query = event.get_message_str()[-2400:]
        if not query:
            return []
        revision = self.plugin.settings_revision
        try:
            cid = (
                await self.plugin.context.conversation_manager.get_curr_conversation_id(
                    event.unified_msg_origin
                )
                or ""
            )
            result = await self.plugin.room_memory.call(
                scope,
                "midterm_records",
                cid,
                self.plugin.settings["midterm_memory_limit"],
                event=event,
            )
            candidates, ids = [], {}
            for row in result["records"]:
                payload = row["metadata"]["midterm"]
                if (
                    payload["embedding_key"]
                    != embedding.get_embedding_configuration_key()
                ):
                    continue
                for cue in payload["recall_cues"]:
                    candidate = MidTermMemoryRecallCandidate(
                        MemorySummaryMessage(payload["id"]), payload, cue["text"], 0
                    )
                    candidates.append(candidate)
                    ids[payload["id"]] = row["hash"]
            if not candidates:
                return []
            vector = (await embedding.encode([query]))[0]
            best = _select_best_recall_candidate(
                candidates,
                query_embedding=vector.tolist(),
                threshold=MID_TERM_MEMORY_DEFAULT_RECALL_THRESHOLD,
            )
            if (
                best is None
                or revision != self.plugin.settings_revision
                or not await self.permitted(event, scope, persona)
            ):
                return []
            return [
                {
                    "id": ids[best.message.message_id],
                    "type": "paragraph",
                    "score": best.score,
                    "source": "chat_summary:" + scope,
                    "content": _format_mid_term_memory_reference(best),
                    "metadata": {"source_type": "midterm"},
                }
            ]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("聊天回想通道暂不可用 (%s)", type(exc).__name__)
            return []
