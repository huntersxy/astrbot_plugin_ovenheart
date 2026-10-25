"""Explicit native transports for the original A_memorix SDK services."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace

from . import host, summary_host
from .host import PersonInfo, TaskConfig, get_db_session, get_logger
from .utils.async_work import run_sync_work

_native = ContextVar("ovenheart_memory_sdk_host")
global_config = host.global_config
config_manager = summary_host.config_manager
message_service = summary_host
LLMServiceRequest = host.LLMServiceRequest
LLMServiceResult = host.LLMServiceResult


@contextmanager
def bind_native(*, data_root, embedding, session=None, generate=None):
    token = _native.set(SimpleNamespace(data_root=Path(data_root), embedding=embedding, session=session, generate=generate))
    try:
        yield
    finally:
        _native.reset(token)


def get_instance_root():
    return _native.get().data_root


def create_embedding_api_adapter(**config):
    adapter = _native.get().embedding
    if not adapter.available:
        raise RuntimeError("未配置可用的 AstrBot 嵌入提供商。")
    return adapter


def get_available_models():
    return host.get_available_models()


async def generate(request):
    native = _native.get(None)
    if native is not None and native.generate is not None:
        return LLMServiceResult(True, SimpleNamespace(response=await native.generate(request)))
    return await host.generate(request)


class LLMServiceClient:
    def __init__(self, *, task_name, request_type):
        self.task_name = task_name
        self.request_type = request_type

    async def generate_response(self, prompt, **options):
        result = await generate(LLMServiceRequest(self.task_name, self.request_type, prompt, options.get("temperature"), options.get("max_tokens")))
        return result.completion


def load_prompt(name, locale="zh-CN", **values):
    if name == "a_memorix_chat_summary":
        return summary_host.load_prompt(name, locale=locale, **values)
    return (Path(__file__).parent / "prompts" / f"{name}.{locale}.txt").read_text(encoding="utf-8").format(**values)


class ChatManager:
    def get_existing_session_by_session_id(self, session_id):
        session = _native.get().session
        return session if session is not None and session.session_id == session_id else None


chat_manager = ChatManager()
llm_service = sys.modules[__name__]
