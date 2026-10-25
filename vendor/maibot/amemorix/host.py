"""Explicit AstrBot host bindings for the namespaced A_memorix port.

No modules are installed under MaiBot's global ``src`` package. The original
profile service reads only this identity table and the bound model interface.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace

from astrbot.api import logger
from sqlmodel import Field, Session, SQLModel


class PersonInfo(SQLModel, table=True):
    __tablename__ = "ovenheart_amemorix_people"
    __table_args__ = {"extend_existing": True}
    person_id: str = Field(primary_key=True)
    user_id: str
    person_name: str = ""
    user_nickname: str = ""
    group_cardname: str = "[]"
    memory_points: str = "[]"


_binding = ContextVar("ovenheart_amemorix_host")
global_config = SimpleNamespace(
    a_memorix=SimpleNamespace(
        integration=SimpleNamespace(
            feedback_correction_paragraph_hard_filter_enabled=True,
        )
    )
)


def get_logger(name):
    return logger


@contextmanager
def bind(engine, generate=None):
    token = _binding.set((engine, generate))
    try:
        yield
    finally:
        _binding.reset(token)


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
    return {"memory": TaskConfig(model_list=["native"])} if _binding.get()[1] else {}


async def generate(request):
    completion = await _binding.get()[1](request)
    return LLMServiceResult(True, SimpleNamespace(response=completion))
