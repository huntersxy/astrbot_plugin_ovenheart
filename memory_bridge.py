"""Optional runtime integration with the installed LivingMemory instance."""

from __future__ import annotations

import asyncio
import copy
import importlib
import time
from functools import partial
from weakref import WeakKeyDictionary

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.platform import MessageType
from astrbot.api.provider import ProviderRequest
from astrbot.core.agent.message import TextPart
from astrbot.core.star.session_plugin_manager import SessionPluginManager
from astrbot.core.star.star_handler import EventType, star_handlers_registry

from .learning import normalize_quote


class MemoryBridge:
    """Reuse LivingMemory's scope and permission policy without owning its data."""

    def __init__(self, context, can_extend=None):
        self.context = context
        self.can_extend = can_extend
        self._reply_sources = WeakKeyDictionary()
        self._hooks = []
        self._owner = None
        self.legacy_retired = None

    def disconnect(self) -> None:
        """Restore only dispatch entries still owned by this bridge."""
        for entry, original, wrapper in self._hooks:
            if entry.handler is wrapper:
                entry.handler = original
        self._hooks.clear()
        self._owner = None
        self._reply_sources.clear()

    def install(self) -> bool:
        """Adapt native memory hooks in memory without changing plugin files.

        Returns:
            Whether both supported LivingMemory hooks are currently routed.
        """
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        plugin = metadata.star_cls if metadata and metadata.activated else None
        if plugin is None:
            self.disconnect()
            return False
        if (
            self._owner is plugin
            and all(
                entry.enabled
                and entry.handler is wrapper
                and entry
                in star_handlers_registry.get_handlers_by_event_type(
                    entry.event_type, only_activated=False
                )
                for entry, _, wrapper in self._hooks
            )
            and len(self._hooks) == 2
        ):
            return True
        self.disconnect()
        selected = []
        for event_type, name in (
            (EventType.OnLLMRequestEvent, "handle_memory_recall"),
            (EventType.OnLLMResponseEvent, "handle_memory_reflection"),
        ):
            matches = [
                entry
                for entry in star_handlers_registry.get_handlers_by_event_type(
                    event_type, only_activated=False
                )
                if entry.handler_name == name
                and (
                    getattr(entry.handler, "__self__", None) is plugin
                    or (
                        isinstance(entry.handler, partial)
                        and bool(entry.handler.args)
                        and entry.handler.args[0] is plugin
                    )
                )
            ]
            if len(matches) != 1:
                return False
            selected.append(matches[0])
        self._owner = plugin
        for entry in selected:
            original = entry.handler

            async def routed(event, *args, _original=original, _owner=plugin, **kwargs):
                await self._dispatch(_owner, _original, event, *args, **kwargs)

            entry.handler = routed
            self._hooks.append((entry, original, routed))
        return True

    def clear_reply_source(self, event):
        """Release one completed reply before routing the next planner target."""
        self._reply_sources.pop(event, None)
        for key in (
            "ovenheart_memory_source_requested",
            "ovenheart_memory_source_blocked",
        ):
            event.get_extra().pop(key, None)

    def bind_reply_source(self, event, source, is_current) -> str:
        """Retain a real target for the two native memory hook invocations.

        Args:
            event: Native trigger whose identity is preserved for every other plugin.
            source: Captured same-session human target, or None to suppress memory.
            is_current: Synchronous admission guard checked before each memory hook.

        Returns:
            Operator-readable routing state. An unsupported hook shape marks this
            autonomous request blocked rather than silently using the wrong source.
        """
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        if not metadata or not metadata.activated or metadata.star_cls is None:
            return "未启用 LivingMemory，无需绑定回复来源。"
        if not self.install():
            event.set_extra("ovenheart_memory_source_blocked", True)
            return "LivingMemory 钩子接口不兼容，已取消本轮跨消息主动回复，避免记忆来源错位。"
        event.set_extra("ovenheart_memory_source_requested", True)
        if len(self._reply_sources) >= 128 and event not in self._reply_sources:
            del self._reply_sources[next(iter(self._reply_sources))]
        self._reply_sources[event] = None
        if (
            not isinstance(source, AstrMessageEvent)
            or event.get_message_type() != MessageType.GROUP_MESSAGE
            or source.get_message_type() != MessageType.GROUP_MESSAGE
            or source.unified_msg_origin != event.unified_msg_origin
            or source.get_platform_name() != event.get_platform_name()
            or source.get_self_id() != event.get_self_id()
            or not source.get_sender_id()
            or source.get_sender_id() == source.get_self_id()
            or not 0 <= time.time() - source.created_at <= 600
        ):
            return "回复来源缺失或过期，本轮跳过 LivingMemory 召回与反思。"
        view = copy.copy(source)
        view.session = copy.copy(source.session)
        view.platform_meta = copy.copy(source.platform_meta)
        view.message_obj = copy.copy(source.message_obj)
        if getattr(source.message_obj, "group", None) is not None:
            view.message_obj.group = copy.copy(source.message_obj.group)
        view.message_obj.sender = copy.copy(source.message_obj.sender)
        view.message_obj.message = list(source.get_messages())
        view._extras = {}
        self._reply_sources[event] = (view, is_current, self._owner)
        return "烤箱之心已绑定实际回复来源；兼容官方 LivingMemory，无需修改版。"

    async def _dispatch(self, owner, original, event, *args, **kwargs):
        """Route a single memory callback while retaining native hook semantics.

        Args:
            owner: LivingMemory instance owning this specific registered hook.
            original: Original bound native hook, called exactly once when allowed.
            event: Host trigger, unchanged for other plugins and tools.
            *args: Native request or response arguments.
            **kwargs: Native keyword arguments.
        """
        if self.legacy_retired is not None and await self.legacy_retired(event):
            return
        if event.get_extra("ovenheart_memory_source_requested", False) is not True:
            # Pre-upgrade requests can outlive the retired private adapter.
            if event.get_extra("livingmemory_reply_source_requested", False) is True:
                return
            await self._invoke(original, event, *args, **kwargs)
            return
        binding = self._reply_sources.get(event)
        if binding is None or binding[2] is not owner or self._owner is not owner:
            return
        source, is_current, _ = binding
        try:
            if not is_current():
                return
            if not await SessionPluginManager.is_plugin_enabled_for_session(
                event.unified_msg_origin, "astrbot_plugin_livingmemory"
            ):
                return
            metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
            plugin_set = self.context.get_config(event.unified_msg_origin).get(
                "plugin_set", ["*"]
            )
            if (
                not metadata
                or not metadata.activated
                or metadata.star_cls is not owner
                or (
                    plugin_set != ["*"]
                    and "astrbot_plugin_livingmemory" not in plugin_set
                )
                or self._owner is not owner
                or not is_current()
            ):
                return
            package = type(owner).__module__.rsplit(".", 1)[0]
            policy = importlib.import_module(f"{package}.core.memory_scope")
            if not policy.is_event_memory_allowed(
                owner.config_manager, event
            ) or not policy.is_event_memory_allowed(owner.config_manager, source):
                return
        except Exception as exc:
            logger.warning(
                "OvenHeart memory source validation failed (%s)", type(exc).__name__
            )
            return
        # The current turn owns timestamps/checkpoints; factual source fields
        # belong to the captured target. Each hook receives its own isolated extras.
        view = copy.copy(source)
        view.message_obj = copy.copy(source.message_obj)
        view.message_obj.timestamp = event.message_obj.timestamp
        view.created_at = event.created_at
        view._extras = {}
        checkpoint = event.get_extra("llm_checkpoint_id")
        if isinstance(checkpoint, str) and checkpoint:
            view.set_extra("llm_checkpoint_id", checkpoint)
        await self._invoke(original, view, *args, **kwargs)

    async def _invoke(self, original, event, *args, **kwargs):
        """Interpret the native memory injection socially without another search."""
        req = args[0] if args else kwargs.get("req")
        before = (
            len(req.extra_user_content_parts) if isinstance(req, ProviderRequest) else 0
        )
        await original(event, *args, **kwargs)
        if (
            not isinstance(req, ProviderRequest)
            or self.can_extend is None
            or not await self.can_extend(event)
        ):
            return
        # This is LivingMemory's published injection marker; unknown shapes are
        # left untouched. Reflection and memory content are never rewritten.
        if not any(
            isinstance(part, TextPart) and "<RAG-Faiss-Memory>" in part.text
            for part in req.extra_user_content_parts[before:]
        ):
            return
        marker = "[OvenHeart social memory]\n"
        if any(
            isinstance(part, TextPart) and part.text.startswith(marker)
            for part in req.extra_user_content_parts
        ):
            return
        req.extra_user_content_parts.append(
            TextPart(
                text=marker
                + "长期记忆用于理解相处方式和这次话题的来由，而不只是回答事实题。"
                "先核对记忆参与者、来源时间与本轮真实对象；同群发生不等于属于当前成员，缺少归属就不套用。"
                "有依据时自然延续熟悉程度、共同话题、承诺或此前分歧；不逐条背诵档案、不凭空亲昵，"
                "不把别人的经历当成自己经历。当前更正和明确意愿优先于旧印象；无关记忆忽略。"
            ).mark_as_temp()
        )

    def status(self) -> dict:
        """Report native memory readiness and the plugin-owned runtime adapter.

        Returns:
            Credential-free engine and request-source routing capabilities.
        """
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        if not metadata or not metadata.activated or metadata.star_cls is None:
            return {
                "available": False,
                "reply_source_binding": False,
                "message": "未启用 LivingMemory；可继续独立学习。",
            }
        try:
            initializer = metadata.star_cls.initializer
            ready = initializer.is_initialized and initializer.memory_engine is not None
        except AttributeError:
            return {
                "available": False,
                "reply_source_binding": False,
                "message": "LivingMemory 引擎接口不兼容。",
            }
        binding = self.install()
        return {
            "available": bool(ready),
            "reply_source_binding": bool(ready and binding),
            "message": (
                "已连接 LivingMemory；回复来源适配由烤箱之心维护，无需修改版。"
                if binding
                else "LivingMemory 钩子接口不兼容，跨消息主动回复将停止，避免记忆来源错位。"
            )
            if ready
            else "LivingMemory 尚未初始化完成。",
        }

    async def planning_context(self, event, persona, messages, settings):
        """Bring authorized shared recollections into the decision before speaking.

        Personal scope is deliberately deferred until a real addressee is known.
        Sequence links identify the query sources, never ownership of old facts.
        """
        if not settings["memory_enabled"]:
            return [], "本次未启用参与记忆。"
        sources = [
            {
                "id": m["sequence"],
                "sender": m["sender_id"],
                "text": m["text"],
                "reply_to": m.get("reply_to"),
            }
            for m in messages[-6:]
            if not m.get("self")
        ]
        if not sources:
            return [], "没有可关联的真实人类发言。"
        try:
            memories, status = await asyncio.wait_for(
                self.recall(
                    event,
                    persona,
                    settings | {"memory_top_k": min(3, settings["memory_top_k"])},
                    observations=sources,
                    defer_user_scope=True,
                    include_social_metadata=True,
                ),
                timeout=min(1.5, settings["retrieval_timeout"]),
            )
        except asyncio.TimeoutError:
            return [], "参与记忆超过短等待预算，最终回复仍走原生记忆召回。"
        except Exception as exc:
            logger.warning("参与记忆暂不可用 (%s)", type(exc).__name__)
            return [], "参与记忆暂不可用，保留真实对话。"
        return [
            {
                "id": m["id"],
                "content": m["content"][:300],
                "for_sequences": m.get("for_evidence_ids", []),
                "social": m.get("social", {}),
            }
            for m in memories[:3]
        ], status

    async def recall(
        self,
        event,
        persona: str,
        settings: dict,
        *,
        observations: list[dict] | None = None,
        defer_user_scope: bool = False,
        include_social_metadata: bool = False,
        search_query: str | None = None,
    ) -> tuple[list[dict], str]:
        """Retrieve context using LivingMemory's own policy and engine.

        Args:
            event: A permitted real source event in the selected scope.
            persona: Currently selected AstrBot persona.
            settings: Memory integration settings.
            observations: Optional actual learning batch used to construct the
                query and bind recalled context to eligible source IDs.
            defer_user_scope: Skip personal memory before a planner has selected
                its reply target; native final-reply recall still owns that path.
            include_social_metadata: Preserve bounded native participant, fact
                and time fields for attribution during social planning.

        Returns:
            Bounded memory excerpts and a visible integration status.
        """
        if not settings["memory_enabled"]:
            return [], "本次未启用记忆联动。"
        if (
            event is not None
            and self.legacy_retired is not None
            and await self.legacy_retired(event)
        ):
            return (
                [],
                "当前会话和人格已迁入原生记忆，本桥不再重复召回旧LivingMemory；原库保留。",
            )
        status = self.status()
        if not status["available"]:
            return [], status["message"]
        if event is None:
            return [], "本批素材没有可用的真实来源事件，本次仅使用已保留的聊天素材。"
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        plugin = metadata.star_cls
        try:
            if not await SessionPluginManager.is_plugin_enabled_for_session(
                event.unified_msg_origin, "astrbot_plugin_livingmemory"
            ):
                return [], "当前会话已停用 LivingMemory，本次仅使用聊天素材。"
            plugin_set = self.context.get_config(event.unified_msg_origin).get(
                "plugin_set", ["*"]
            )
            if plugin_set != ["*"] and "astrbot_plugin_livingmemory" not in plugin_set:
                return [], "当前 AstrBot 配置未启用 LivingMemory，本次仅使用聊天素材。"
            package = type(plugin).__module__.rsplit(".", 1)[0]
            policy = importlib.import_module(f"{package}.core.memory_scope")
            if not policy.is_event_memory_allowed(plugin.config_manager, event):
                return [], "LivingMemory 的访问规则禁止读取当前会话记忆。"
            scope = policy.resolve_memory_scope(plugin.config_manager, event)
            if defer_user_scope and scope and scope.startswith("livingmemory:user:"):
                return [], "参与决策尚未选定回复对象，用户记忆留待实际回复阶段读取。"
            sources = observations
            if sources is not None:
                # User memory remains tied to the real event's identity. Do not
                # impersonate another batch member or widen the plugin's scope.
                if scope and scope.startswith("livingmemory:user:"):
                    sources = [
                        m for m in sources if m["sender"] == event.get_sender_id()
                    ]
                if not sources:
                    return (
                        [],
                        "本批素材为空或与当前记忆作用域不匹配，本次仅使用聊天素材。",
                    )
                sample = (
                    [sources[i * (len(sources) - 1) // 7] for i in range(8)]
                    if len(sources) > 8
                    else sources
                )
                per_source = (500 - len(sample) + 1) // len(sample)
                query = "\n".join(
                    (
                        m["text"]
                        + "\n"
                        + (
                            normalize_quote(
                                m.get("reply_to"), settings["blocked_senders"]
                            )
                            or {}
                        ).get("text", "")
                    )[:per_source].rstrip()
                    for m in sample
                )
            else:
                query = event.get_message_str()[:500]
            use_persona = plugin.config_manager.filtering_settings.get(
                "use_persona_filtering", True
            )
            engine = plugin.initializer.memory_engine
            if search_query is not None:
                query = search_query[:500]
            memories = await asyncio.wait_for(
                engine.search_memories(
                    query=query,
                    k=settings["memory_top_k"],
                    # None deliberately means unfiltered in LivingMemory's legacy mode.
                    session_id=scope,
                    persona_id=(None if persona == "[%None]" else persona)
                    if use_persona
                    else None,
                ),
                timeout=12,
            )
            latest = self.context.get_registered_star("astrbot_plugin_livingmemory")
            enabled = await SessionPluginManager.is_plugin_enabled_for_session(
                event.unified_msg_origin, "astrbot_plugin_livingmemory"
            )
            plugin_set = self.context.get_config(event.unified_msg_origin).get(
                "plugin_set", ["*"]
            )
            if (
                not latest
                or not latest.activated
                or latest.star_cls is not plugin
                or not plugin.initializer.is_initialized
                or plugin.initializer.memory_engine is not engine
                or not enabled
                or (
                    plugin_set != ["*"]
                    and "astrbot_plugin_livingmemory" not in plugin_set
                )
                or not policy.is_event_memory_allowed(plugin.config_manager, event)
                or policy.resolve_memory_scope(plugin.config_manager, event) != scope
                or plugin.config_manager.filtering_settings.get(
                    "use_persona_filtering", True
                )
                != use_persona
            ):
                return (
                    [],
                    "LivingMemory 访问、实例或作用域在检索期间改变，已丢弃旧记忆结果。",
                )
            excerpts = [
                {
                    "id": str(m.doc_id),
                    "content": m.content[:700],
                    **(
                        {"for_evidence_ids": [source["id"] for source in sources]}
                        if sources is not None
                        else {}
                    ),
                }
                for m in memories[: settings["memory_top_k"]]
            ]
            if include_social_metadata:
                for excerpt, memory in zip(excerpts, memories, strict=False):
                    metadata = getattr(memory, "metadata", None)
                    if not isinstance(metadata, dict):
                        continue
                    social = {}
                    for key, limit, chars in (
                        ("participants", 8, 48),
                        ("key_facts", 3, 120),
                    ):
                        values = metadata.get(key)
                        if isinstance(values, list):
                            social[key] = [str(v)[:chars] for v in values[:limit]]
                    if metadata.get("create_time") is not None:
                        social["source_time"] = str(metadata["create_time"])[:40]
                    excerpt["social"] = social
            return (
                excerpts,
                f"参考 {len(excerpts)} 条长期记忆；遵循 LivingMemory 的访问与作用域设置。",
            )
        except asyncio.TimeoutError:
            return [], "LivingMemory 检索超时，本次仅使用聊天素材。"
        except (ImportError, AttributeError):
            return [], "LivingMemory 接口与当前适配版本不兼容，请更新插件。"
