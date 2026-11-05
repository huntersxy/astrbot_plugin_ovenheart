"""Remove echoed internal context before the native reply is saved or sent."""

import re

from astrbot.core.message.components import Plain

INTERNAL_START = re.compile(
    r"(?m)^[ \t]*(?:\[OvenHeart [^\]\n]+\][ \t]*(?:\n|$)"
    r"|<system_reminder>(?=User ID:|Current datetime:))"
)
CODE = re.compile(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`")
DSML_START = re.compile(r"<\s*/?\s*[|｜]+\s*DSML\s*[|｜]+", re.IGNORECASE)


def clean_reply(text):
    marker = DSML_START.search(text)
    if marker:
        # Tool arguments can follow the tag. Drop the entire protocol tail.
        text = text[: marker.start()].rstrip()
    protected = [match.span() for match in CODE.finditer(text)]
    for match in INTERNAL_START.finditer(text):
        if not any(start <= match.start() < end for start, end in protected):
            return text[: match.start()].rstrip()
    return text


async def clean_reply_stream(stream):
    """Hold only a possible split DSML prefix before handing chunks to an adapter."""
    pending, template, blocked = "", None, False
    try:
        async for chunk in stream:
            if chunk is None:
                yield chunk
                continue
            if chunk.type == "break":
                if pending and not blocked:
                    yield template.derive([Plain(pending)])
                pending, template, blocked = "", None, False
                yield chunk
                continue
            if chunk.type in {"reasoning", "audio_chunk", "tool_call", "agent_stats"}:
                yield chunk
                continue
            chain = []
            for part in chunk.chain:
                if not isinstance(part, Plain):
                    if pending and not blocked:
                        chain.append(Plain(pending))
                        pending = ""
                    chain.append(part)
                    continue
                if blocked:
                    continue
                template = chunk
                pending += part.text
                marker = DSML_START.search(pending)
                if marker:
                    safe = pending[: marker.start()].rstrip()
                    if safe:
                        chain.append(Plain(safe))
                    pending, blocked = "", True
                    continue
                start = pending.rfind("<")
                if (
                    start >= 0
                    and ">" not in pending[start:]
                    and len(pending) - start <= 48
                ):
                    safe, pending = pending[:start], pending[start:]
                else:
                    safe, pending = pending, ""
                if safe:
                    chain.append(Plain(safe))
            if chain:
                yield chunk.derive(chain)
        if pending and not blocked:
            # A truncated protocol prefix must not leak at end of stream either.
            if not re.match(r"<\s*/?\s*[|｜]", pending):
                yield template.derive([Plain(pending)])
    finally:
        if hasattr(stream, "aclose"):
            await stream.aclose()
