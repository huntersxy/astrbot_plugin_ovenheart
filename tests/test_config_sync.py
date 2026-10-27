"""Exercise shared settings through native config persistence and dashboard services."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from astrbot.api import AstrBotConfig
from astrbot.dashboard.services import config_service

from astrbot_plugin_ovenheart.integrations.jev import host_settings
from astrbot_plugin_ovenheart.main import PLUGIN_NAME, PersonaPlugin
from tests.test_runtime import api_call

pytest_plugins = ["tests.test_runtime"]
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(
        encoding="utf-8"
    )
)


@pytest.fixture
async def native_plugin(plugin, tmp_path):
    config = AstrBotConfig(str(tmp_path / "native_config.json"), schema=SCHEMA)
    config.save_config(plugin.settings)
    config.save_config(
        {
            "jev_connection": {
                "base_url": "http://example.invalid/v1",
                "model": "jev-latest",
                "api_key": "synthetic-native-secret",
            }
        }
    )
    plugin.config = config
    plugin.host_settings = host_settings(config)
    return plugin


async def test_panel_save_is_visible_in_native_config_and_dashboard(
    native_plugin, monkeypatch
):
    plugin = native_plugin
    metadata = SimpleNamespace(name=PLUGIN_NAME, config=plugin.config, i18n={})
    monkeypatch.setattr(config_service, "star_registry", [metadata])
    display = config_service.ConfigDisplayService(SimpleNamespace(astrbot_config={}))
    result = await api_call(
        plugin.page_api.action,
        {
            "action": "settings",
            "payload": {
                "debug_enabled": True,
                "blocked_senders": ["123456", "234567"],
                "session_isolation": False,
                "guidance_delivery": "cached",
                "memory_top_k": 5,
            },
        },
    )
    assert result["status"] == "ok"
    disk = json.loads(
        await asyncio.to_thread(
            Path(plugin.config.config_path).read_text, encoding="utf-8-sig"
        )
    )
    assert host_settings(disk) == plugin.settings
    assert disk == display.get_plugin_config(PLUGIN_NAME)["config"]
    state = (await api_call(plugin.page_api.state))["data"]
    assert (
        state["settings"] == host_settings(disk)
        and state["settings_revision"] == plugin.settings_revision
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"debug_enabled": True},
        {"memory_enabled": False},
        {"participation_followup": False},
    ],
)
async def test_panel_toggle_preserves_other_settings_after_native_reload(
    native_plugin, monkeypatch, patch
):
    plugin = native_plugin
    await plugin.update_settings(
        {
            "provider_id": "learning-provider",
            "embedding_provider_id": "embedding-provider",
            "rerank_provider_id": "rerank-provider",
            "planner_provider_id": "planner-provider",
            "blocked_senders": ["123456", "234567"],
            "autonomous_tools": ["search", "read_file"],
            "memory_top_k": 7,
        }
    )
    expected = plugin.settings | patch
    saved = []
    original = AstrBotConfig.save_config

    def capture_save(self, replace_config=None, **kwargs):
        saved.append(json.loads(json.dumps(replace_config)))
        return original(self, replace_config, **kwargs)

    monkeypatch.setattr(AstrBotConfig, "save_config", capture_save)
    response = await api_call(
        plugin.page_api.action, {"action": "settings", "payload": patch}
    )
    assert response["status"] == "ok"
    # Guard the full-snapshot contract at the host's shallow-merge boundary.
    assert saved == [expected]
    disk = json.loads(
        await asyncio.to_thread(
            Path(plugin.config.config_path).read_text, encoding="utf-8-sig"
        )
    )
    reloaded = AstrBotConfig(plugin.config.config_path, schema=SCHEMA)
    assert disk == dict(reloaded) == dict(plugin.config)
    assert host_settings(disk) == plugin.settings == expected
    assert reloaded["jev_connection"]["api_key"] == "synthetic-native-secret"
    assert "jev_connection" not in await plugin.store.call("settings")


async def test_native_config_save_and_reload_replaces_old_panel_guards(
    native_plugin, monkeypatch
):
    plugin = native_plugin
    await plugin.update_settings({"debug_enabled": True, "session_isolation": True})
    old_revision = plugin.settings_revision
    holder = {}

    async def reload_plugin(name):
        assert name == PLUGIN_NAME
        await plugin.terminate()
        config = AstrBotConfig(plugin.config.config_path, schema=SCHEMA)
        reloaded = PersonaPlugin(plugin.context, config)
        holder["plugin"] = reloaded
        await reloaded.initialize()

    metadata = SimpleNamespace(name=PLUGIN_NAME, config=plugin.config, i18n={})
    monkeypatch.setattr(config_service, "star_registry", [metadata])
    service = config_service.ConfigFileService(
        SimpleNamespace(
            plugin_manager=SimpleNamespace(reload=AsyncMock(side_effect=reload_plugin))
        )
    )
    try:
        await service.save_plugin_configs(
            dict(plugin.config)
            | {
                "debug_enabled": False,
                "session_isolation": False,
                "memory_top_k": 6,
            },
            PLUGIN_NAME,
        )
        reloaded = holder["plugin"]
        state = (await api_call(reloaded.page_api.state))["data"]
        assert state["settings"]["debug_enabled"] is False
        assert state["settings"]["session_isolation"] is False
        assert state["settings"]["memory_top_k"] == 6
        assert not await reloaded.store.call("settings")
        stale = await api_call(
            reloaded.page_api.action,
            {
                "action": "settings",
                "settings_revision": old_revision,
                "payload": {"debug_enabled": True},
            },
        )
        assert stale["status"] == "error" and "另一页面" in stale["message"]
        assert reloaded.settings["debug_enabled"] is False
    finally:
        if holder:
            await holder["plugin"].terminate()




async def test_native_write_failure_does_not_report_success_or_publish_changes(
    native_plugin, monkeypatch
):
    plugin = native_plugin
    before = plugin.settings.copy()
    disk = await asyncio.to_thread(Path(plugin.config.config_path).read_bytes)
    guards = await plugin.store.call("settings")
    revision = plugin.settings_revision

    def fail_write(self, *args, **kwargs):
        raise OSError("Synthetic write failure")

    monkeypatch.setattr(AstrBotConfig, "_write_config_snapshot", fail_write)
    response = await api_call(
        plugin.page_api.action,
        {"action": "settings", "payload": {"debug_enabled": True}},
    )
    assert response["status"] == "error" and "配置" in response["message"]
    assert plugin.settings == host_settings(plugin.config) == before
    assert await asyncio.to_thread(Path(plugin.config.config_path).read_bytes) == disk
    assert await plugin.store.call("settings") == guards
    assert plugin.settings_revision == revision


async def test_two_settings_forms_cannot_overwrite_each_other(native_plugin):
    plugin = native_plugin
    revision = plugin.settings_revision
    results = await asyncio.gather(
        *(
            api_call(
                plugin.page_api.action,
                {
                    "action": "settings",
                    "settings_revision": revision,
                    "payload": {"memory_top_k": count},
                },
            )
            for count in (4, 5)
        )
    )
    assert sorted(r["status"] for r in results) == ["error", "ok"]
    assert plugin.settings == host_settings(plugin.config)
    assert json.loads(
        await asyncio.to_thread(
            Path(plugin.config.config_path).read_text, encoding="utf-8-sig"
        )
    ) == dict(plugin.config)


async def test_cancelled_save_finishes_native_persistence_and_runtime_publication(
    native_plugin, monkeypatch
):
    plugin = native_plugin
    started, release = threading.Event(), threading.Event()
    original = AstrBotConfig.save_config

    def delayed(self, *args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AstrBotConfig, "save_config", delayed)
    task = asyncio.create_task(plugin.update_settings({"memory_top_k": 8}))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert plugin.settings["memory_top_k"] == plugin.config["memory_top_k"] == 8
    assert (await plugin.store.call("settings"))["memory_top_k"] == 8
    assert (
        json.loads(
            await asyncio.to_thread(
                Path(plugin.config.config_path).read_text, encoding="utf-8-sig"
            )
        )["memory_top_k"]
        == 8
    )


async def test_host_save_before_reload_rejects_old_panel_form(native_plugin):
    plugin = native_plugin
    plugin.config.save_config({"memory_top_k": 6})
    response = await api_call(
        plugin.page_api.action, {"action": "settings", "payload": {"memory_top_k": 4}}
    )
    assert response["status"] == "error" and "另一页面" in response["message"]
    assert plugin.config["memory_top_k"] == 6
