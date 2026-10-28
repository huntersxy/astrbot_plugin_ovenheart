"""Scoped AstrBot transport and typing settings for the original reply algorithms."""

from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace

_response = ContextVar("ovenheart_response_host")


@contextmanager
def bind_response(generate, speed):
    token = _response.set((generate, speed))
    try:
        yield
    finally:
        _response.reset(token)


class ResponseConfig:
    @property
    def response_post_process(self):
        return SimpleNamespace(typing_speed=_response.get()[1])


global_config = ResponseConfig()


class LLMServiceClient:
    def __init__(self, **_):
        pass

    async def generate_response(self, prompt):
        return SimpleNamespace(response=await _response.get()[0](prompt))
