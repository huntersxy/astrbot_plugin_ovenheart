"""Native quotes preserve authorship through capture, extraction and writes."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from astrbot.core.message.components import At, Plain, Reply

from astrbot_plugin_ovenheart.chat.conversation import ConversationContext
from tests.test_memory_consolidation import drain, summary
from tests.test_person_memory import fact
from tests.test_runtime import event

pytest_plugins = ["tests.test_person_memory", "tests.test_memory_consolidation"]


def quoted_event(*, text="我是学生。", flattened=True, author="alice"):
    incoming = event("bob-reply", text=text, sender="bob", sender_name="B")
    incoming.message_obj.message = [
        Reply(
            id="alice-original",
            sender_id=author,
            sender_nickname="A",
            message_str="我是医生。",
        ),
        Plain(text),
    ]
    if flattened:
        incoming.message_str = incoming.get_message_outline()
    return incoming


@pytest.mark.parametrize("flattened", [False, True])
async def test_quote_is_context_not_personal_or_room_evidence(person, flattened):
    incoming = quoted_event(flattened=flattened)
    await person.capture(incoming)
    scope, persona, _ = await person.resolve_scope(incoming)
    inputs = await person.store.call("person_memory_inputs", scope, "bob")
    assert len(inputs) == 1 and inputs[0]["text"] == "我是学生。"
    assert inputs[0]["reply_to"] == {
        "message_id": "alice-original",
        "sender": "alice",
        "sender_name": "A",
        "text": "我是医生。",
        "self": False,
    }
    assert await person.store.call("room_memory_inputs", scope) == inputs
    record = ConversationContext.record(incoming)
    assert record["text"] == "我是学生。"
    assert record["reply_to"]["sender_id"] == "alice"
    person.participation.sources[scope, "bob-reply"] = incoming
    message = {"sequence": 1, "source": "bob-reply", "sender_id": "bob"}
    with pytest.raises(ValueError, match="原文证据"):
        await person.person_memory.remember(
            incoming,
            scope,
            persona,
            message,
            {"facts": [fact("bob-reply", "我是医生", "职业：医生")]},
        )
    result = await person.person_memory.remember(
        incoming,
        scope,
        persona,
        message,
        {"facts": [fact("bob-reply", "我是学生", "职业：学生")]},
    )
    assert result["facts"][0]["evidence"][0]["metadata"]["sender"] == "bob"
    assert not (
        await person.person_memory.query(incoming, scope, persona, "alice")
    ).get("facts")
    person.settings["native_memory_enabled"] = True
    with pytest.raises(ValueError, match="真实作者"):
        await person.room_memory.verified_evidence(
            incoming, scope, persona, [{"msg_id": "1", "quote": "我是医生"}], [message]
        )
    evidence = await person.room_memory.verified_evidence(
        incoming, scope, persona, [{"msg_id": "1", "quote": "我是学生"}], [message]
    )
    assert evidence[0]["sender"] == "bob" and evidence[0]["source_id"] == "bob-reply"


@pytest.mark.parametrize("author", ["alice", "", "blocked"])
async def test_person_extraction_keeps_quote_author_and_respects_exclusions(
    person, author
):
    incoming = quoted_event(author=author)
    person.settings["blocked_senders"] = ["blocked"] if author == "blocked" else []
    await person.capture(incoming)
    scope, persona, _ = await person.resolve_scope(incoming)
    messages = await person.store.call("person_memory_inputs", scope, "bob")
    person.context.llm_generate.side_effect = None
    person.context.llm_generate.return_value = SimpleNamespace(
        completion_text=json.dumps(
            {"facts": [fact("bob-reply", "我是学生", "职业：学生")]}
        )
    )
    person_id = person.person_memory.identity(incoming, scope, persona, "bob")
    await person.person_memory.extract(incoming, scope, persona, person_id, messages)
    prompt = json.loads(person.context.llm_generate.call_args.kwargs["prompt"])
    assert prompt["sender"] == prompt["messages"][0]["sender"] == "bob"
    assert prompt["messages"][0]["text"] == "我是学生。"
    quote = prompt["messages"][0]["reply_to"]
    if author == "blocked":
        assert quote is None
        assert "我是医生" not in json.dumps(prompt, ensure_ascii=False)
    else:
        assert quote["sender"] == author
        assert quote["message_id"] == "alice-original"
    result = await person.person_memory.query(incoming, scope, persona, "bob")
    assert "职业：学生" in result["profile_text"]


async def test_quote_without_own_words_does_not_create_person_material(person):
    incoming = quoted_event(text="")
    await person.capture(incoming)
    scope, _, _ = await person.resolve_scope(incoming)
    assert await person.store.call("room_memory_inputs", scope) == []
    assert ConversationContext.record(incoming)["text"] == ""
    assert not person.person_memory.busy


async def test_quoted_reply_preserves_the_subject_of_a_native_mention(person):
    incoming = quoted_event(text="是学生。")
    incoming.message_obj.message.insert(1, At(qq="carol"))
    await person.capture(incoming)
    scope, _, _ = await person.resolve_scope(incoming)
    messages = await person.store.call("person_memory_inputs", scope, "bob")
    assert messages[0]["text"] == "[At:carol] 是学生。"
    assert ConversationContext.record(incoming)["mentions"] == ["carol"]


@pytest.mark.parametrize("evidence_quote", ["我是学生", "我是医生"])
async def test_native_summary_separates_quote_context_from_writable_evidence(
    consolidator, evidence_quote
):
    p = consolidator
    p.settings["memory_summary_enabled"] = False
    incoming = quoted_event()
    await p.capture(incoming)
    scope, persona, _ = await p.resolve_scope(incoming)
    p.settings["memory_summary_enabled"] = True
    p.context.llm_generate.side_effect = [
        summary("bob-reply", evidence_quote, "bob说自己是学生。", entities=["bob"]),
        SimpleNamespace(completion_text='{"episodes": []}'),
    ]
    await p.memory_consolidation.observe(incoming, scope, persona)
    await asyncio.wait_for(drain(p), 10)
    prompt = p.context.llm_generate.call_args_list[0].kwargs["prompt"]
    assert '"sender": "alice"' in prompt and '"message_id": "alice-original"' in prompt
    assert "sender=bob: 我是学生。" in prompt
    state = p.room_memory.rooms[scope]
    assert state.metadata.count_paragraphs() == (
        1 if evidence_quote == "我是学生" else 0
    )
    if evidence_quote == "我是学生":
        paragraphs = state.metadata.get_paragraphs_by_source("chat_summary:" + scope)
        proof = paragraphs[0]["metadata"]["evidence"][0]
        assert proof["sender"] == "bob" and proof["quote"] == "我是学生"
