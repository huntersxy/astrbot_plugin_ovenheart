"""Keep native human-body definitions ahead of unrelated quote terms."""

import sqlite3

from astrbot.api.provider import ProviderRequest
from astrbot.core.message.components import Reply

from tests.test_runtime import event

pytest_plugins = ["tests.test_runtime"]


async def test_native_request_prioritizes_the_human_body_before_quote_scoring(plugin):
    plugin.settings.update(
        selection_mode="lexical", guidance_delivery="cached", max_traits=1
    )
    incoming = event("focus", text="LUX 是什么意思？")
    incoming.message_obj.message = [
        Reply(id="old", sender_id="bob", message_str="青栈这是什么意思的意思？")
    ]
    await plugin.capture(incoming)
    scope = (await plugin.store.call("state"))["scopes"][0]["id"]
    with sqlite3.connect(plugin.store.path) as db:
        for identifier, content, rating, evidence in (
            (101, "LUX：本群改期记号", -1, 2),
            (102, "青栈：这是什么意思的意思", 1, 1000),
        ):
            db.execute(
                "INSERT INTO traits(id,scope,kind,situation,content,confidence,evidence,status,fingerprint,created,updated,rating,evidence_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    scope,
                    "jargon",
                    "本群交流时",
                    content,
                    0.95,
                    "[]",
                    "active",
                    str(identifier),
                    1,
                    1,
                    rating,
                    evidence,
                ),
            )
    request = ProviderRequest(
        prompt=incoming.get_message_str(), system_prompt="宿主人格"
    )
    await plugin.apply_persona(incoming, request)
    assert incoming.get_extra("ovenheart_application")["ids"] == [101]
    assert "LUX：本群改期记号" in request.extra_user_content_parts[-1].text
    assert "青栈：这是什么意思的意思" not in request.extra_user_content_parts[-1].text
    assert request.extra_user_content_parts[-1].model_dump_for_context()["_no_save"]
    assert request.prompt == "LUX 是什么意思？"
    assert incoming.message_obj.message_id == "focus"
    plugin.context.llm_generate.assert_not_awaited()
