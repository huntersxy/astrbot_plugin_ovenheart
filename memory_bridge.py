"""Optional runtime integration with the installed LivingMemory instance."""

from __future__ import annotations

import asyncio
import importlib

from astrbot.core.star.session_plugin_manager import SessionPluginManager


class MemoryBridge:
    """Reuse LivingMemory's scope and permission policy without owning its data."""

    def __init__(self, context):
        self.context = context

    def bind_reply_source(self, event, source, is_current) -> str:
        """Use LivingMemory's optional same-session reply-source protocol.

        Args:
            event: Native trigger carrying the admitted autonomous reply.
            source: Verified real target event, or None to suppress wrong recall.
            is_current: Synchronous live-admission guard used by both memory hooks.

        Returns:
            A credential-free integration status, without making a memory call.
        """
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        if not metadata or not metadata.activated or metadata.star_cls is None:
            return "未启用 LivingMemory，无需绑定回复来源。"
        handler = getattr(metadata.star_cls, "event_handler", None)
        bind = getattr(handler, "bind_reply_source", None)
        if not callable(bind):
            return "当前 LivingMemory 不支持回复来源绑定，仍使用原始触发事件。请安装配套适配版。"
        try:
            return (
                "LivingMemory 已绑定实际回复来源。"
                if bind(event, source, is_current)
                else "回复来源缺失、过期或无权限，本轮跳过 LivingMemory 召回与反思。"
            )
        except Exception:
            event.set_extra("livingmemory_reply_source_requested", True)
            return "回复来源绑定失败，本轮跳过适配版 LivingMemory 召回与反思。"

    def status(self) -> dict:
        """Inspect integration readiness without initializing another plugin.

        Returns:
            Availability and an operator-facing explanation.
        """
        metadata = self.context.get_registered_star("astrbot_plugin_livingmemory")
        if not metadata or not metadata.activated or metadata.star_cls is None:
            return {
                "available": False,
                "message": "未启用 LivingMemory；可继续独立学习。",
            }
        try:
            initializer = metadata.star_cls.initializer
            ready = initializer.is_initialized and initializer.memory_engine is not None
        except AttributeError:
            return {
                "available": False,
                "message": "LivingMemory 接口与当前适配版本不兼容。",
            }
        return {
            "available": bool(ready),
            "message": "已连接 LivingMemory"
            if ready
            else "LivingMemory 尚未初始化完成。",
        }

    async def recall(
        self,
        event,
        persona: str,
        settings: dict,
        *,
        observations: list[dict] | None = None,
    ) -> tuple[list[dict], str]:
        """Retrieve context using LivingMemory's own policy and engine.

        Args:
            event: A permitted real source event in the selected scope.
            persona: Currently selected AstrBot persona.
            settings: Memory integration settings.
            observations: Optional actual learning batch used to construct the
                query and bind recalled context to eligible source IDs.

        Returns:
            Bounded memory excerpts and a visible integration status.
        """
        if not settings["memory_enabled"]:
            return [], "本次未启用记忆联动。"
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
                query = "\n".join(m["text"][:per_source] for m in sample)
            else:
                query = event.get_message_str()[:500]
            use_persona = plugin.config_manager.filtering_settings.get(
                "use_persona_filtering", True
            )
            memories = await asyncio.wait_for(
                plugin.initializer.memory_engine.search_memories(
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
            return (
                excerpts,
                f"参考 {len(excerpts)} 条长期记忆；遵循 LivingMemory 的访问与作用域设置。",
            )
        except asyncio.TimeoutError:
            return [], "LivingMemory 检索超时，本次仅使用聊天素材。"
        except (ImportError, AttributeError):
            return [], "LivingMemory 接口与当前适配版本不兼容，请更新插件。"
