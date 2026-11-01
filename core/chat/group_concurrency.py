"""Experimental per-sender group concurrency owned by OvenHeart.

Unlocks parallel LLM turns for different members in one group while keeping:
- same-sender serialization
- per-group outbound send ordering
- conservative conversation-history merge on concurrent writes
- follow-up capture keyed by sender

Off by default. Install only when the experimental setting is enabled.
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import hashlib
import inspect
import json
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import logger

SENDER_MARKER = "#ovenheart_sender:"
_BASE_SNAPSHOT_EXTRA = "ovenheart_gsc_history_base"
_TURN_ANCHOR_EXTRA = "ovenheart_gsc_turn_anchor"
_COMMIT_RECEIPT_LIMIT = 256

_CURRENT_EVENT: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "ovenheart_group_concurrency_event", default=None
)
_CURRENT_SAVE_CONTEXT: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "ovenheart_group_concurrency_save", default=None
)
_CURRENT_SEND_ROUND: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "ovenheart_group_concurrency_send", default=None
)
_GROUP_SEND_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


@dataclass(frozen=True)
class GroupSenderKey:
    umo: str
    sender_id: str


@dataclass
class LockScope:
    umo: str
    sender_id: str | None


@dataclass
class SaveContext:
    umo: str
    conversation_id: str
    base_history: list[Any]
    unit_start: int | None = None
    expected_total: int | None = None


@dataclass
class SendRound:
    umo: str
    group_lock: asyncio.Lock
    acquired: bool = False
    closed: bool = False
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def ensure_acquired(self) -> bool:
        async with self._state_lock:
            if self.closed:
                return False
            if not self.acquired:
                await self.group_lock.acquire()
                if self.closed:
                    self.group_lock.release()
                    return False
                self.acquired = True
            return True

    def close_now(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.acquired:
            self.group_lock.release()
            self.acquired = False


class GroupConcurrencyGate:
    """Allow many sender locks, but exclusive when sender_id is absent."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    async def acquire_shared(self) -> None:
        async with self._condition:
            while self._writer or self._writers_waiting:
                await self._condition.wait()
            self._readers += 1

    async def release_shared(self) -> None:
        async with self._condition:
            self._readers = max(0, self._readers - 1)
            if self._readers == 0:
                self._condition.notify_all()

    async def acquire_exclusive(self) -> None:
        async with self._condition:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers:
                    await self._condition.wait()
                self._writer = True
            finally:
                self._writers_waiting = max(0, self._writers_waiting - 1)

    async def release_exclusive(self) -> None:
        async with self._condition:
            self._writer = False
            self._condition.notify_all()


class SharedGroupLockContext:
    def __init__(self, gate: GroupConcurrencyGate, lock: Any):
        self._gate = gate
        self._lock = lock

    async def __aenter__(self):
        await self._gate.acquire_shared()
        await self._lock.__aenter__()
        return self

    async def __aexit__(self, *exc):
        try:
            return await self._lock.__aexit__(*exc)
        finally:
            await self._gate.release_shared()


class ExclusiveGroupLockContext:
    def __init__(self, gate: GroupConcurrencyGate, lock: Any):
        self._gate = gate
        self._lock = lock

    async def __aenter__(self):
        await self._gate.acquire_exclusive()
        await self._lock.__aenter__()
        return self

    async def __aexit__(self, *exc):
        try:
            return await self._lock.__aexit__(*exc)
        finally:
            await self._gate.release_exclusive()


class GroupConcurrencyBridge:
    """Runtime patches for experimental group sender concurrency."""

    _active: GroupConcurrencyBridge | None = None
    _session_lock_manager = None
    _original_acquire_lock = None
    _lock_wrapper = None
    _internal_stage_cls = None
    _original_internal_process = None
    _process_wrapper = None
    _third_party_stage_cls = None
    _original_third_party_process = None
    _third_party_process_wrapper = None
    _original_save_to_history = None
    _save_history_wrapper = None
    _conversation_manager_cls = None
    _original_update_conversation = None
    _update_conversation_wrapper = None
    _context_cls = None
    _original_context_send_message = None
    _context_send_message_wrapper = None
    _follow_up_module = None
    _internal_module = None
    _original_register_active_runner = None
    _original_unregister_active_runner = None
    _original_try_capture_follow_up = None
    _original_internal_register_active_runner = None
    _original_internal_unregister_active_runner = None
    _original_internal_try_capture_follow_up = None
    _register_runner_wrapper = None
    _unregister_runner_wrapper = None
    _try_capture_wrapper = None
    _internal_register_runner_wrapper = None
    _internal_unregister_runner_wrapper = None
    _internal_try_capture_wrapper = None

    def __init__(self, plugin):
        self.plugin = plugin
        self._installed = False
        self._group_gates: dict[tuple[int, str], GroupConcurrencyGate] = {}
        self._write_locks: dict[tuple[int, str], asyncio.Lock] = {}
        self._active_runners: dict[tuple[str, str], Any] = {}
        self._commit_receipts: OrderedDict[tuple[int, str, str], str] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._installed and type(self)._active is self

    def sync(self, enabled: bool) -> bool:
        """Install or tear down patches to match the experimental setting."""
        if enabled:
            return self.install()
        self.disconnect()
        return False

    def install(self) -> bool:
        if self.enabled:
            return True
        ok = (
            self._install_session_lock_patch()
            and self._install_process_patch()
            and self._install_save_history_patch()
            and self._install_update_conversation_patch()
            and self._install_context_send_message_patch()
            and self._install_follow_up_patch()
        )
        if not ok:
            self.disconnect()
            logger.warning("OvenHeart 群聊并发：宿主入口不完整，已跳过安装。")
            return False
        type(self)._active = self
        self._installed = True
        logger.info("OvenHeart 实验性群聊并发已启用（按发送者并行，出站仍整群串行）。")
        return True

    def disconnect(self) -> None:
        cls = type(self)
        if cls._active is self:
            self._restore_all()
        self._installed = False
        self._active_runners.clear()
        self._commit_receipts.clear()
        self._group_gates.clear()
        self._write_locks.clear()

    def sender_lock_key(self, event: Any) -> str | None:
        key = build_group_sender_key(event)
        return format_sender_scoped_umo(key) if key else None

    def capture_base_snapshot(self, event: Any, req: Any) -> None:
        if not self.enabled:
            return
        try:
            sender_key = build_group_sender_key(event)
            if sender_key is None:
                return
            conversation = getattr(req, "conversation", None)
            conversation_id = _text(getattr(conversation, "cid", None))
            if not conversation_id:
                return
            base_history = parse_history_value(getattr(conversation, "history", None))
            if base_history is None:
                return
            setter = getattr(event, "set_extra", None)
            if callable(setter):
                setter(
                    _BASE_SNAPSHOT_EXTRA,
                    {
                        "umo": sender_key.umo,
                        "conversation_id": conversation_id,
                        "base_history": copy.deepcopy(base_history),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OvenHeart 群聊并发：捕获历史原稿失败: %s", exc)

    def capture_turn_anchor(self, event: Any, runner: Any) -> None:
        try:
            snapshot = _extra(event, _BASE_SNAPSHOT_EXTRA)
            if not isinstance(snapshot, dict):
                return
            req = getattr(runner, "req", None)
            conversation = getattr(req, "conversation", None)
            conversation_id = _text(getattr(conversation, "cid", None))
            if not conversation_id or conversation_id != snapshot.get("conversation_id"):
                return
            messages = getattr(getattr(runner, "run_context", None), "messages", None)
            if not isinstance(messages, list) or not messages:
                return
            anchor = messages[-1]
            if message_role(anchor) != "user" or message_no_save(anchor):
                return
            setter = getattr(event, "set_extra", None)
            if callable(setter):
                setter(_TURN_ANCHOR_EXTRA, anchor)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OvenHeart 群聊并发：捕获轮次锚点失败: %s", exc)

    def _restore_all(self) -> None:
        cls = type(self)
        self._restore_callable(
            cls._session_lock_manager,
            "acquire_lock",
            cls._lock_wrapper,
            cls._original_acquire_lock,
        )
        self._restore_callable(
            cls._internal_stage_cls,
            "process",
            cls._process_wrapper,
            cls._original_internal_process,
        )
        self._restore_callable(
            cls._third_party_stage_cls,
            "process",
            cls._third_party_process_wrapper,
            cls._original_third_party_process,
        )
        self._restore_callable(
            cls._internal_stage_cls,
            "_save_to_history",
            cls._save_history_wrapper,
            cls._original_save_to_history,
        )
        self._restore_callable(
            cls._conversation_manager_cls,
            "update_conversation",
            cls._update_conversation_wrapper,
            cls._original_update_conversation,
        )
        self._restore_callable(
            cls._context_cls,
            "send_message",
            cls._context_send_message_wrapper,
            cls._original_context_send_message,
        )
        for module, wrappers, originals in (
            (
                cls._follow_up_module,
                (
                    cls._register_runner_wrapper,
                    cls._unregister_runner_wrapper,
                    cls._try_capture_wrapper,
                ),
                (
                    cls._original_register_active_runner,
                    cls._original_unregister_active_runner,
                    cls._original_try_capture_follow_up,
                ),
            ),
            (
                cls._internal_module,
                (
                    cls._internal_register_runner_wrapper,
                    cls._internal_unregister_runner_wrapper,
                    cls._internal_try_capture_wrapper,
                ),
                (
                    cls._original_internal_register_active_runner,
                    cls._original_internal_unregister_active_runner,
                    cls._original_internal_try_capture_follow_up,
                ),
            ),
        ):
            if module is None:
                continue
            for name, wrapper, original in zip(
                (
                    "register_active_runner",
                    "unregister_active_runner",
                    "try_capture_follow_up",
                ),
                wrappers,
                originals,
                strict=True,
            ):
                self._restore_callable(module, name, wrapper, original)
        cls._session_lock_manager = None
        cls._original_acquire_lock = None
        cls._lock_wrapper = None
        cls._internal_stage_cls = None
        cls._original_internal_process = None
        cls._process_wrapper = None
        cls._third_party_stage_cls = None
        cls._original_third_party_process = None
        cls._third_party_process_wrapper = None
        cls._original_save_to_history = None
        cls._save_history_wrapper = None
        cls._conversation_manager_cls = None
        cls._original_update_conversation = None
        cls._update_conversation_wrapper = None
        cls._context_cls = None
        cls._original_context_send_message = None
        cls._context_send_message_wrapper = None
        cls._follow_up_module = None
        cls._internal_module = None
        cls._original_register_active_runner = None
        cls._original_unregister_active_runner = None
        cls._original_try_capture_follow_up = None
        cls._original_internal_register_active_runner = None
        cls._original_internal_unregister_active_runner = None
        cls._original_internal_try_capture_follow_up = None
        cls._register_runner_wrapper = None
        cls._unregister_runner_wrapper = None
        cls._try_capture_wrapper = None
        cls._internal_register_runner_wrapper = None
        cls._internal_unregister_runner_wrapper = None
        cls._internal_try_capture_wrapper = None
        cls._active = None

    @staticmethod
    def _restore_callable(owner: Any, name: str, wrapper: Any, original: Any) -> None:
        if owner is None or original is None or wrapper is None:
            return
        current = getattr(owner, name, None)
        if same_callable(current, wrapper):
            setattr(owner, name, original)

    def _install_session_lock_patch(self) -> bool:
        cls = type(self)
        manager = _load_session_lock_manager()
        if manager is None:
            return False
        original = getattr(manager, "acquire_lock", None)
        if not callable(original):
            return False
        if cls._original_acquire_lock is None:
            cls._session_lock_manager = manager
            cls._original_acquire_lock = original

            def ovenheart_acquire_lock(session_id: str):
                active = cls._active
                session = str(session_id)
                if active is None or SENDER_MARKER in session:
                    return original(session)
                event = _CURRENT_EVENT.get()
                scope = build_lock_scope(event)
                if scope is None or session != scope.umo:
                    return original(session)
                if scope.sender_id:
                    lock_key = format_sender_scoped_umo(
                        GroupSenderKey(umo=scope.umo, sender_id=scope.sender_id)
                    )
                else:
                    lock_key = session
                locked = original(lock_key)
                return active.wrap_group_lock(scope, locked)

            cls._lock_wrapper = ovenheart_acquire_lock
            manager.acquire_lock = ovenheart_acquire_lock
        return True

    def _install_process_patch(self) -> bool:
        internal = _load_internal_stage_cls()
        third = _load_third_party_stage_cls()
        if internal is None or third is None:
            return False
        return self._install_stage_process(internal, third_party=False) and self._install_stage_process(
            third, third_party=True
        )

    def _install_stage_process(self, stage_cls: type, *, third_party: bool) -> bool:
        cls = type(self)
        stage_attr = "_third_party_stage_cls" if third_party else "_internal_stage_cls"
        original_attr = (
            "_original_third_party_process"
            if third_party
            else "_original_internal_process"
        )
        wrapper_attr = (
            "_third_party_process_wrapper" if third_party else "_process_wrapper"
        )
        original = getattr(stage_cls, "process", None)
        if not callable(original):
            return False
        if getattr(cls, original_attr) is None:
            setattr(cls, stage_attr, stage_cls)
            setattr(cls, original_attr, original)

            async def ovenheart_process(stage_self, event, *args, **kwargs):
                active = cls._active
                token = send_token = None
                send_round = None
                restore = None
                try:
                    if active is not None:
                        token = _CURRENT_EVENT.set(event)
                        send_round = active.build_send_round(event)
                        if send_round is not None:
                            setter = getattr(event, "set_extra", None)
                            if callable(setter):
                                setter("enable_streaming", False)
                            send_token = _CURRENT_SEND_ROUND.set(send_round)
                            restore = active.install_event_send_guards(event, send_round)
                    processed = original(stage_self, event, *args, **kwargs)
                    if inspect.isasyncgen(processed):
                        async for item in processed:
                            yield item
                    elif inspect.isawaitable(processed):
                        await processed
                finally:
                    if send_round is not None:
                        send_round.close_now()
                    if restore is not None:
                        restore()
                    if send_token is not None:
                        try:
                            _CURRENT_SEND_ROUND.reset(send_token)
                        except ValueError:
                            pass
                    if token is not None:
                        try:
                            _CURRENT_EVENT.reset(token)
                        except ValueError:
                            pass

            setattr(cls, wrapper_attr, ovenheart_process)
            stage_cls.process = ovenheart_process
        return True

    def _install_save_history_patch(self) -> bool:
        cls = type(self)
        stage = _load_internal_stage_cls()
        if stage is None:
            return False
        original = getattr(stage, "_save_to_history", None)
        if not callable(original):
            return False
        if cls._original_save_to_history is None:
            cls._internal_stage_cls = stage
            cls._original_save_to_history = original

            async def ovenheart_save_to_history(*args, **kwargs):
                active = cls._active
                token = None
                if active is not None:
                    context = active.build_save_context(args, kwargs)
                    if context is not None:
                        token = _CURRENT_SAVE_CONTEXT.set(context)
                try:
                    return await original(*args, **kwargs)
                finally:
                    if token is not None:
                        _CURRENT_SAVE_CONTEXT.reset(token)

            cls._save_history_wrapper = ovenheart_save_to_history
            stage._save_to_history = ovenheart_save_to_history
        return True

    def _install_update_conversation_patch(self) -> bool:
        cls = type(self)
        manager_cls = _load_conversation_manager_cls()
        if manager_cls is None:
            return False
        original = getattr(manager_cls, "update_conversation", None)
        if not callable(original):
            return False
        if cls._original_update_conversation is None:
            cls._conversation_manager_cls = manager_cls
            cls._original_update_conversation = original

            async def ovenheart_update_conversation(manager_self, *args, **kwargs):
                active = cls._active
                save_context = _CURRENT_SAVE_CONTEXT.get()
                if active is None or save_context is None:
                    return await original(manager_self, *args, **kwargs)
                try:
                    return await active.update_conversation_with_merge(
                        original, manager_self, args, kwargs, save_context
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "OvenHeart 群聊并发：历史合并失败，回退宿主保存: %s", exc
                    )
                    return await original(manager_self, *args, **kwargs)

            cls._update_conversation_wrapper = ovenheart_update_conversation
            manager_cls.update_conversation = ovenheart_update_conversation
        return True

    def _install_context_send_message_patch(self) -> bool:
        cls = type(self)
        context_cls = _load_context_cls()
        if context_cls is None:
            return False
        original = getattr(context_cls, "send_message", None)
        if not callable(original):
            return False
        if cls._original_context_send_message is None:
            cls._context_cls = context_cls
            cls._original_context_send_message = original

            async def ovenheart_send_message(context_self, *args, **kwargs):
                active = cls._active
                send_round = _CURRENT_SEND_ROUND.get()
                session = args[0] if args else kwargs.get("session")
                if (
                    active is None
                    or send_round is None
                    or _text(session) != send_round.umo
                ):
                    return await original(context_self, *args, **kwargs)
                return await active.send_with_round(
                    send_round, original, context_self, *args, **kwargs
                )

            cls._context_send_message_wrapper = ovenheart_send_message
            context_cls.send_message = ovenheart_send_message
        return True

    def _install_follow_up_patch(self) -> bool:
        cls = type(self)
        follow_up = _load_follow_up_module()
        internal = _load_internal_module()
        if follow_up is None or internal is None:
            return False
        names = (
            "register_active_runner",
            "unregister_active_runner",
            "try_capture_follow_up",
        )
        originals = [getattr(follow_up, name, None) for name in names]
        internal_originals = [getattr(internal, name, None) for name in names]
        if not all(callable(item) for item in originals + internal_originals):
            return False
        if cls._original_register_active_runner is None:
            cls._follow_up_module = follow_up
            cls._internal_module = internal
            (
                cls._original_register_active_runner,
                cls._original_unregister_active_runner,
                cls._original_try_capture_follow_up,
            ) = originals
            (
                cls._original_internal_register_active_runner,
                cls._original_internal_unregister_active_runner,
                cls._original_internal_try_capture_follow_up,
            ) = internal_originals

            def wrap_register(original):
                def ovenheart_register(umo, runner):
                    active = cls._active
                    if active is not None and active.register_sender_runner(umo, runner):
                        return None
                    return original(umo, runner)

                return ovenheart_register

            def wrap_unregister(original):
                def ovenheart_unregister(umo, runner):
                    active = cls._active
                    if active is not None and active.unregister_sender_runner(
                        umo, runner
                    ):
                        return None
                    return original(umo, runner)

                return ovenheart_unregister

            def wrap_capture(original):
                def ovenheart_capture(event):
                    active = cls._active
                    if active is not None:
                        key = build_group_sender_key(event)
                        if key is not None:
                            return active.try_capture_sender_follow_up(
                                follow_up, key, event
                            )
                    return original(event)

                return ovenheart_capture

            cls._register_runner_wrapper = wrap_register(originals[0])
            cls._unregister_runner_wrapper = wrap_unregister(originals[1])
            cls._try_capture_wrapper = wrap_capture(originals[2])
            cls._internal_register_runner_wrapper = wrap_register(internal_originals[0])
            cls._internal_unregister_runner_wrapper = wrap_unregister(
                internal_originals[1]
            )
            cls._internal_try_capture_wrapper = wrap_capture(internal_originals[2])
            follow_up.register_active_runner = cls._register_runner_wrapper
            follow_up.unregister_active_runner = cls._unregister_runner_wrapper
            follow_up.try_capture_follow_up = cls._try_capture_wrapper
            internal.register_active_runner = cls._internal_register_runner_wrapper
            internal.unregister_active_runner = cls._internal_unregister_runner_wrapper
            internal.try_capture_follow_up = cls._internal_try_capture_wrapper
        return True

    def wrap_group_lock(self, lock_scope: LockScope, original_lock: Any) -> Any:
        gate = self.get_group_gate(lock_scope.umo)
        if lock_scope.sender_id:
            return SharedGroupLockContext(gate, original_lock)
        return ExclusiveGroupLockContext(gate, original_lock)

    def get_group_gate(self, key: str) -> GroupConcurrencyGate:
        loop_key = (id(asyncio.get_running_loop()), key)
        gate = self._group_gates.get(loop_key)
        if gate is None:
            gate = GroupConcurrencyGate()
            self._group_gates[loop_key] = gate
        return gate

    def get_write_lock(self, key: str) -> asyncio.Lock:
        loop_key = (id(asyncio.get_running_loop()), key)
        lock = self._write_locks.get(loop_key)
        if lock is None:
            lock = asyncio.Lock()
            self._write_locks[loop_key] = lock
        return lock

    def get_group_send_lock(self, umo: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        loop_locks = _GROUP_SEND_LOCKS.get(loop)
        if loop_locks is None:
            loop_locks = weakref.WeakValueDictionary()
            _GROUP_SEND_LOCKS[loop] = loop_locks
        lock = loop_locks.get(umo)
        if lock is None:
            lock = asyncio.Lock()
            loop_locks[umo] = lock
        return lock

    def build_send_round(self, event: Any) -> SendRound | None:
        key = build_group_sender_key(event)
        if key is None:
            return None
        return SendRound(umo=key.umo, group_lock=self.get_group_send_lock(key.umo))

    def install_event_send_guards(self, event: Any, send_round: SendRound):
        guards = []
        for name in ("send", "send_streaming"):
            original = getattr(event, name, None)
            if not callable(original):
                continue

            def build(original_send):
                async def guarded(*args, **kwargs):
                    return await self.send_with_round(
                        send_round, original_send, *args, **kwargs
                    )

                return guarded

            wrapper = build(original)
            try:
                setattr(event, name, wrapper)
            except Exception:  # noqa: BLE001
                continue
            guards.append((name, original, wrapper))

        def restore():
            for name, original, wrapper in reversed(guards):
                if same_callable(getattr(event, name, None), wrapper):
                    try:
                        setattr(event, name, original)
                    except Exception:  # noqa: BLE001
                        pass

        return restore

    async def send_with_round(self, send_round: SendRound, original_send, *args, **kwargs):
        try:
            await send_round.ensure_acquired()
        except Exception as exc:  # noqa: BLE001
            logger.warning("OvenHeart 群聊并发：获取发送锁失败，放行原发送: %s", exc)
        result = original_send(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def build_save_context(self, args, kwargs) -> SaveContext | None:
        event = args[1] if len(args) > 1 else kwargs.get("event")
        req = args[2] if len(args) > 2 else kwargs.get("req")
        all_messages = args[4] if len(args) > 4 else kwargs.get("all_messages")
        sender_key = build_group_sender_key(event)
        if sender_key is None:
            return None
        snapshot = _extra(event, _BASE_SNAPSHOT_EXTRA)
        if not isinstance(snapshot, dict) or snapshot.get("umo") != sender_key.umo:
            return None
        conversation = getattr(req, "conversation", None)
        conversation_id = _text(getattr(conversation, "cid", None))
        if not conversation_id or conversation_id != snapshot.get("conversation_id"):
            return None
        base_history = snapshot.get("base_history")
        if not isinstance(base_history, list):
            return None
        unit_start = expected_total = None
        anchor = _extra(event, _TURN_ANCHOR_EXTRA)
        if anchor is not None and isinstance(all_messages, list):
            checkpoint_id = _extra(event, "llm_checkpoint_id")
            located = locate_current_unit(
                all_messages, anchor, isinstance(checkpoint_id, str) and bool(checkpoint_id)
            )
            if located is not None:
                unit_start, expected_total = located
        return SaveContext(
            umo=sender_key.umo,
            conversation_id=conversation_id,
            base_history=base_history,
            unit_start=unit_start,
            expected_total=expected_total,
        )

    async def update_conversation_with_merge(
        self, original_update, manager_self, args, kwargs, save_context: SaveContext
    ):
        unified_msg_origin = args[0] if args else kwargs.get("unified_msg_origin")
        conversation_id = args[1] if len(args) > 1 else kwargs.get("conversation_id")
        history = args[2] if len(args) > 2 else kwargs.get("history")
        if (
            str(unified_msg_origin) != save_context.umo
            or str(conversation_id) != save_context.conversation_id
            or not isinstance(history, list)
        ):
            return await original_update(manager_self, *args, **kwargs)
        async with self.get_write_lock(save_context.umo):
            chosen = history
            branch = "fallback-no-latest"
            latest = await fetch_latest_history(
                manager_self, save_context.umo, save_context.conversation_id
            )
            if latest is not None:
                try:
                    chosen, branch = self.choose_merged_history(
                        save_context, latest, history
                    )
                except Exception as exc:  # noqa: BLE001
                    chosen = history
                    branch = "fallback-error"
                    logger.warning("OvenHeart 群聊并发：合并比较失败: %s", exc)
            merged_args = list(args)
            merged_kwargs = dict(kwargs)
            if len(merged_args) > 2:
                merged_args[2] = chosen
            else:
                merged_kwargs["history"] = chosen
            if chosen is not history and not digests_equal(chosen, history):
                if len(merged_args) > 5:
                    merged_args[5] = 0
                else:
                    merged_kwargs["token_usage"] = 0
            result = await original_update(manager_self, *merged_args, **merged_kwargs)
            self._remember_receipt(save_context, chosen)
            logger.info(
                "OvenHeart 群聊并发历史保存 branch=%s base=%d latest=%s new=%d chosen=%d",
                branch,
                len(save_context.base_history),
                len(latest) if isinstance(latest, list) else "-",
                len(history),
                len(chosen),
            )
            return result

    def choose_merged_history(
        self, save_context: SaveContext, latest_history: list[Any], new_history: list[Any]
    ) -> tuple[list[Any], str]:
        unit_start = save_context.unit_start
        if (
            unit_start is None
            or save_context.expected_total != len(new_history)
            or not 0 <= unit_start < len(new_history)
            or message_role(new_history[unit_start]) != "user"
        ):
            return new_history, "fallback-no-anchor"
        base_history = save_context.base_history
        latest_digest = structural_digest(latest_history)
        if latest_digest == structural_digest(base_history):
            return new_history, "no-concurrent-write"
        receipt = self._get_receipt(save_context)
        if receipt is None or latest_digest != receipt:
            return new_history, "fallback-untrusted-latest"
        current_unit = new_history[unit_start:]
        if len(latest_history) > len(base_history) and structural_digest(
            latest_history[: len(base_history)]
        ) == structural_digest(base_history):
            merged = (
                new_history[:unit_start]
                + latest_history[len(base_history) :]
                + current_unit
            )
            return merged, "merge-append"
        return list(latest_history) + current_unit, "merge-after-rewrite"

    def _receipt_key(self, save_context: SaveContext):
        try:
            loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            loop_id = 0
        return (loop_id, save_context.umo, save_context.conversation_id)

    def _get_receipt(self, save_context: SaveContext) -> str | None:
        key = self._receipt_key(save_context)
        digest = self._commit_receipts.get(key)
        if digest is not None:
            self._commit_receipts.move_to_end(key)
        return digest

    def _remember_receipt(self, save_context: SaveContext, chosen: list[Any]) -> None:
        try:
            digest = structural_digest(chosen)
        except Exception:  # noqa: BLE001
            return
        key = self._receipt_key(save_context)
        self._commit_receipts[key] = digest
        self._commit_receipts.move_to_end(key)
        while len(self._commit_receipts) > _COMMIT_RECEIPT_LIMIT:
            self._commit_receipts.popitem(last=False)

    def register_sender_runner(self, umo: str, runner: Any) -> bool:
        event = get_runner_event(runner)
        key = build_group_sender_key(event)
        if key is None or key.umo != str(umo):
            return False
        self._active_runners[(key.umo, key.sender_id)] = runner
        self.capture_turn_anchor(event, runner)
        return True

    def unregister_sender_runner(self, umo: str, runner: Any) -> bool:
        event = get_runner_event(runner)
        key = build_group_sender_key(event)
        if key is None or key.umo != str(umo):
            for stored_key, active in list(self._active_runners.items()):
                if active is runner:
                    self._active_runners.pop(stored_key, None)
                    return True
            return False
        stored = (key.umo, key.sender_id)
        if self._active_runners.get(stored) is runner:
            self._active_runners.pop(stored, None)
            return True
        return False

    def try_capture_sender_follow_up(self, follow_up_module, sender_key, event):
        runner = self._active_runners.get((sender_key.umo, sender_key.sender_id))
        if runner is None:
            return None
        runner_event = get_runner_event(runner)
        active_sender = _text(_call(getattr(runner_event, "get_sender_id", None)))
        if not active_sender or active_sender != sender_key.sender_id:
            return None
        if _extra(runner_event, "agent_stop_requested"):
            return None
        event_text = getattr(follow_up_module, "_event_follow_up_text", None)
        allocate = getattr(follow_up_module, "_allocate_follow_up_order", None)
        monitor = getattr(follow_up_module, "_monitor_follow_up_ticket", None)
        capture_cls = getattr(follow_up_module, "FollowUpCapture", None)
        if not all(callable(item) for item in (event_text, allocate, monitor)) or capture_cls is None:
            return None
        ticket = runner.follow_up(message_text=event_text(event))
        if not ticket:
            return None
        follow_umo = format_sender_scoped_umo(sender_key)
        order_seq = allocate(follow_umo)
        monitor_task = asyncio.create_task(monitor(follow_umo, ticket, order_seq))
        return capture_cls(
            umo=follow_umo,
            ticket=ticket,
            order_seq=order_seq,
            monitor_task=monitor_task,
        )


def build_group_sender_key(event: Any) -> GroupSenderKey | None:
    scope = build_lock_scope(event)
    if scope is None or not scope.sender_id:
        return None
    return GroupSenderKey(umo=scope.umo, sender_id=scope.sender_id)


def build_lock_scope(event: Any) -> LockScope | None:
    if event is None:
        return None
    private = _call(getattr(event, "is_private_chat", None))
    if private is not False:
        return None
    group_id = _text(_call(getattr(event, "get_group_id", None))) or _text(
        getattr(getattr(event, "message_obj", None), "group_id", None)
    )
    umo = _text(getattr(event, "unified_msg_origin", None))
    if (not group_id and not _is_group_umo(umo)) or not umo:
        return None
    if _is_proactive(event):
        return LockScope(umo=umo, sender_id=None)
    sender_id = _text(_call(getattr(event, "get_sender_id", None))) or _text(
        getattr(getattr(getattr(event, "message_obj", None), "sender", None), "user_id", None)
    )
    return LockScope(umo=umo, sender_id=sender_id or None)


def format_sender_scoped_umo(key: GroupSenderKey) -> str:
    return f"{key.umo}{SENDER_MARKER}{key.sender_id}"


def same_callable(first: Any, second: Any) -> bool:
    if first is second:
        return True
    return getattr(first, "__func__", first) is getattr(
        second, "__func__", second
    ) and getattr(first, "__self__", None) is getattr(second, "__self__", None)


def message_role(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("role"))
    return _text(getattr(value, "role", None))


def message_no_save(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get("_no_save"))
    return bool(getattr(value, "_no_save", False))


def message_checkpoint_after(value: Any) -> Any:
    if isinstance(value, dict):
        return None
    return getattr(value, "_checkpoint_after", None)


def locate_current_unit(all_messages, anchor, checkpoint_present) -> tuple[int, int] | None:
    try:
        matches = 0
        anchor_index = None
        dump_index = 0
        skipped_system = False
        for message in all_messages:
            role = message_role(message)
            if role == "system" and not skipped_system:
                skipped_system = True
                continue
            if role in ("assistant", "user") and message_no_save(message):
                continue
            if message is anchor:
                matches += 1
                anchor_index = dump_index
            dump_index += 1
            if message_checkpoint_after(message) is not None:
                dump_index += 1
        if matches != 1 or anchor_index is None:
            return None
        return anchor_index, dump_index + (1 if checkpoint_present else 0)
    except Exception:  # noqa: BLE001
        return None


def normalize_for_digest(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): normalize_for_digest(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_for_digest(item) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return normalize_for_digest(dump())
    raise TypeError(f"unsupported history value type: {type(value)!r}")


def structural_digest(value: Any) -> str:
    payload = json.dumps(
        normalize_for_digest(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digests_equal(left: Any, right: Any) -> bool:
    try:
        return structural_digest(left) == structural_digest(right)
    except Exception:  # noqa: BLE001
        return False


async def fetch_latest_history(manager, umo, conversation_id) -> list[Any] | None:
    getter = getattr(manager, "get_conversation", None)
    if callable(getter):
        conversation = getter(umo, conversation_id)
        if inspect.isawaitable(conversation):
            conversation = await conversation
        parsed = parse_history_value(getattr(conversation, "history", None))
        if parsed is not None:
            return parsed
        content = getattr(conversation, "content", None)
        if isinstance(content, list):
            return content
    db = getattr(manager, "db", None)
    db_getter = getattr(db, "get_conversation_by_id", None)
    if callable(db_getter):
        conversation = db_getter(cid=conversation_id)
        if inspect.isawaitable(conversation):
            conversation = await conversation
        content = getattr(conversation, "content", None)
        if isinstance(content, list):
            return content
        parsed = parse_history_value(getattr(conversation, "history", None))
        if parsed is not None:
            return parsed
    return None


def parse_history_value(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def get_runner_event(runner: Any) -> Any:
    return getattr(
        getattr(getattr(runner, "run_context", None), "context", None), "event", None
    )


def _is_group_umo(umo: str) -> bool:
    parts = umo.split(":", 2)
    return len(parts) >= 2 and parts[1] == "GroupMessage"


def _is_proactive(event: Any) -> bool:
    if event.__class__.__name__ == "CronMessageEvent":
        return True
    action = _text(_extra(event, "action_type")).lower()
    if action in {"cron", "proactive", "live"}:
        return True
    return _extra(event, "cron_job") is not None or _extra(event, "cron_payload") is not None


def _extra(event: Any, key: str) -> Any:
    getter = getattr(event, "get_extra", None)
    return getter(key) if callable(getter) else None


def _call(func: Any, *args) -> Any:
    if not callable(func):
        return None
    try:
        return func(*args)
    except Exception:  # noqa: BLE001
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _load_session_lock_manager():
    try:
        from astrbot.core.utils.session_lock import session_lock_manager

        return session_lock_manager
    except Exception:  # noqa: BLE001
        return None


def _load_internal_stage_cls():
    try:
        from astrbot.core.pipeline.process_stage.method.agent_sub_stages.internal import (
            InternalAgentSubStage,
        )

        return InternalAgentSubStage
    except Exception:  # noqa: BLE001
        return None


def _load_third_party_stage_cls():
    try:
        from astrbot.core.pipeline.process_stage.method.agent_sub_stages.third_party import (
            ThirdPartyAgentSubStage,
        )

        return ThirdPartyAgentSubStage
    except Exception:  # noqa: BLE001
        return None


def _load_internal_module():
    try:
        from astrbot.core.pipeline.process_stage.method.agent_sub_stages import internal

        return internal
    except Exception:  # noqa: BLE001
        return None


def _load_follow_up_module():
    try:
        from astrbot.core.pipeline.process_stage import follow_up

        return follow_up
    except Exception:  # noqa: BLE001
        return None


def _load_conversation_manager_cls():
    try:
        from astrbot.core.conversation_mgr import ConversationManager

        return ConversationManager
    except Exception:  # noqa: BLE001
        return None


def _load_context_cls():
    try:
        from astrbot.core.star.context import Context

        return Context
    except Exception:  # noqa: BLE001
        return None
