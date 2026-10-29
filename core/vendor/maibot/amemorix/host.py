"""Explicit AstrBot host bindings for the namespaced A_memorix port.

No modules are installed under MaiBot's global ``src`` package. The original
profile service reads only this identity table and the bound model interface.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace

from sqlmodel import Field, Session, SQLModel


class PersonInfo(SQLModel, table=True):
    __tablename__ = "ovenheart_amemorix_people"
    __table_args__ = {"extend_existing": True}
    person_id: str = Field(primary_key=True)
    user_id: str
    platform: str = ""
    person_name: str = ""
    user_nickname: str = ""
    group_cardname: str = "[]"
    memory_points: str = "[]"


_binding = ContextVar("ovenheart_amemorix_host")
_integration = ContextVar("ovenheart_amemorix_integration", default=SimpleNamespace(feedback_correction_paragraph_hard_filter_enabled=True))


class NativeGlobalConfig:
    @property
    def a_memorix(self):
        return SimpleNamespace(integration=_integration.get())


global_config = NativeGlobalConfig()




@contextmanager
def bind(engine, generate=None, model_name="native", *, integration=None):
    token = _binding.set((engine, generate, model_name))
    integration_token = _integration.set(integration) if integration is not None else None
    try:
        yield
    finally:
        _binding.reset(token)
        if integration_token is not None:
            _integration.reset(integration_token)


@contextmanager
def get_db_session(auto_commit=False):
    with Session(_binding.get()[0]) as session:
        yield session
        if auto_commit:
            session.commit()


class TaskConfig(SimpleNamespace):
    """Only model-routing data; the AstrBot provider remains the executor."""


@dataclass
class LLMServiceRequest:
    task_name: str
    request_type: str
    prompt: str
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass
class LLMServiceResult:
    success: bool
    completion: object = None


def get_available_models():
    if not _binding.get()[1]:
        return {}
    return {"memory": TaskConfig(
        model_list=[_binding.get()[2]], temperature=None, max_tokens=None,
        selection_strategy="native", hard_timeout=None,
    )}


async def generate(request):
    completion = await _binding.get()[1](request)
    return LLMServiceResult(True, SimpleNamespace(response=completion))
