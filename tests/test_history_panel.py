"""Bounded history categories, stable pages and source-bound lazy details."""

import json
import sqlite3

import pytest

from astrbot_plugin_ovenheart.settings import SHARED_SCOPE
from tests.test_runtime import api_call, sources

pytest_plugins = ["tests.test_runtime"]


async def seed(plugin):
    scope = await sources(plugin)
    review = {
        "enabled": True,
        "passed": 0,
        "flagged": 1,
        "candidates": [{"reason": "LARGE_REVIEW" * 300}],
    }
    with sqlite3.connect(plugin.store.path) as db:
        for i in range(23):
            db.execute(
                "INSERT INTO runs(scope,status,started,finished,count,message,review) VALUES (?,?,?,?,?,?,?)",
                (
                    scope,
                    "error" if i % 4 == 0 else "success",
                    i + 1,
                    i + 2,
                    1,
                    f"batch {i}",
                    json.dumps(review),
                ),
            )
    return scope


async def test_pages_are_bounded_and_anchor_survives_new_records(plugin):
    scope = await seed(plugin)
    first = await plugin.store.read("panel_history", scope)
    assert (
        first["scope"] == scope and len(first["items"]) == 10 and first["total"] == 23
    )
    assert "LARGE_REVIEW" not in json.dumps(first)
    assert first["items"][0]["flagged"] == 1
    second = await plugin.store.read(
        "panel_history", scope, "learning", "all", 10, first["anchor"]
    )
    with sqlite3.connect(plugin.store.path) as db:
        db.execute(
            "INSERT INTO runs(scope,status,started) VALUES (?,?,?)",
            (scope, "running", 100),
        )
    same = await plugin.store.read(
        "panel_history", scope, "learning", "all", 10, first["anchor"]
    )
    assert all(
        same[key] == second[key] for key in ["items", "offset", "anchor", "total"]
    )
    assert {r["id"] for r in first["items"]}.isdisjoint(
        r["id"] for r in second["items"]
    )
    last = await plugin.store.read(
        "panel_history", scope, "learning", "all", 9990, first["anchor"]
    )
    assert last["offset"] == 20 and len(last["items"]) == 3


async def test_filtered_rows_and_detail_stay_in_the_requested_scope(plugin):
    scope = await seed(plugin)
    filtered = await api_call(
        plugin.page_api.history, query=f"scope={scope}&section=learning&status=error"
    )
    assert filtered["status"] == "ok" and filtered["data"]["total"] == 6
    assert all(r["status"] == "error" for r in filtered["data"]["items"])
    identifier = filtered["data"]["items"][0]["id"]
    detail = await api_call(
        plugin.page_api.history_detail,
        query=f"scope={scope}&section=learning&id={identifier}",
    )
    assert "LARGE_REVIEW" in json.dumps(detail)
    other = "b" * 24
    await plugin.store.call("touch_scope", other, "other", "other", "other")
    wrong = await api_call(
        plugin.page_api.history_detail,
        query=f"scope={other}&section=learning&id={identifier}",
    )
    assert wrong["status"] == "error"


async def test_versions_include_common_space_only_in_shared_mode_and_hide_snapshots(
    plugin,
):
    scope = await sources(plugin)
    with sqlite3.connect(plugin.store.path) as db:
        db.execute(
            "INSERT INTO revisions(scope,action,snapshot,created) VALUES (?,?,?,?)",
            (SHARED_SCOPE, "profile", "PRIVATE_SNAPSHOT", 10),
        )
    shared = await plugin.store.read(
        "panel_history", scope, "versions", "all", 0, 0, True
    )
    assert shared["items"] and "PRIVATE_SNAPSHOT" not in json.dumps(shared)
    identifier = shared["items"][0]["id"]
    detail = await plugin.store.read(
        "history_detail", scope, "versions", identifier, True
    )
    assert "snapshot" not in detail and detail["scope"] == SHARED_SCOPE
    isolated = await plugin.store.read("panel_history", scope, "versions")
    assert isolated["items"] == []
    with pytest.raises(ValueError):
        await plugin.store.read("history_detail", scope, "versions", identifier, False)


@pytest.mark.parametrize(
    "query",
    ["section=unknown", "offset=-1", "anchor=wrong", "status=admitted", "offset=1e8"],
)
async def test_bad_history_parameters_are_rejected(plugin, query):
    scope = await sources(plugin)
    response = await api_call(plugin.page_api.history, query=f"scope={scope}&{query}")
    assert response["status"] == "error"
