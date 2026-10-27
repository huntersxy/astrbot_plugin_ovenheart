"""Cover the editable prompt templates: rendering, validation and storage."""

import pytest

from astrbot_plugin_ovenheart.core import prompts
from astrbot_plugin_ovenheart.core.store import Store


@pytest.fixture
async def store(tmp_path):
    instance = Store(tmp_path / "persona.sqlite3")
    await instance.call("initialize")
    return instance


def test_render_substitutes_bound_values_and_keeps_unknown_references():
    template = "人格：{{ persona }} / 候选：{{candidates}} / 笔误：{{nope}} / 布尔：{{wait_elapsed}}"
    rendered = prompts.render(
        template,
        {"persona": "小夏", "candidates": [{"id": 3}], "wait_elapsed": False},
    )
    assert rendered == '人格：小夏 / 候选：[{"id": 3}] / 笔误：{{nope}} / 布尔：false'


def test_render_leaves_plain_braces_untouched():
    template = '输出 {"action":"reply"} 与 {{persona}}'
    assert (
        prompts.render(template, {"persona": "小夏"})
        == '输出 {"action":"reply"} 与 小夏'
    )


def test_every_default_template_only_uses_declared_placeholders():
    for key in prompts.KEYS:
        assert (
            prompts.missing(
                prompts.default(key), set(prompts.SPECS[key]["placeholders"])
            )
            == []
        ), key


def test_validate_rejects_unknown_blank_and_oversized_bodies():
    with pytest.raises(ValueError):
        prompts.validate("unknown", "x")
    with pytest.raises(ValueError):
        prompts.validate("planner", "   ")
    with pytest.raises(ValueError):
        prompts.validate("planner", "长" * (prompts.MAX_BODY + 1))
    assert prompts.validate("planner", "规则") == "规则"


def test_describe_reports_customization_and_unknown_references():
    default = prompts.default("planner")
    plain = prompts.describe("planner", default)
    assert plain["customized"] is False and plain["unknown"] == []
    assert plain["title"] and plain["placeholders"]
    edited = prompts.describe("planner", "只回复 {{persona}} 与 {{typo}}")
    assert edited["customized"] is True
    assert edited["unknown"] == ["typo"]


def test_merge_keeps_defaults_for_unknown_or_blank_overrides():
    effective = prompts.merge({"planner": "自定义", "review": "  ", "missing": "x"})
    assert effective["planner"] == "自定义"
    assert effective["review"] == prompts.default("review")
    assert "missing" not in effective


async def test_prompt_overrides_round_trip_and_restore_default(store):
    assert await store.call("prompt_overrides") == {}
    stored = await store.call("prompt_overrides", {"planner": "自定义决策规则"})
    assert stored == {"planner": "自定义决策规则"}
    assert await store.call("prompt_overrides") == {"planner": "自定义决策规则"}
    restored = await store.call("prompt_overrides", {"planner": None})
    assert restored == {}
    assert await store.call("prompt_overrides") == {}


async def test_prompt_override_survives_reinitialize(tmp_path):
    path = tmp_path / "persona.sqlite3"
    first = Store(path)
    await first.call("initialize")
    await first.call("prompt_overrides", {"selector": "只选一条 {{max_selected}}"})
    assert await first.call("prompt_overrides") == {
        "selector": "只选一条 {{max_selected}}"
    }

    second = Store(path)
    await second.call("initialize")
    assert await second.call("prompt_overrides") == {
        "selector": "只选一条 {{max_selected}}"
    }
