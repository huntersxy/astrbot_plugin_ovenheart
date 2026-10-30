"""Remove echoed internal context before the native reply is saved or sent."""

import re

INTERNAL_START = re.compile(
    r"(?m)^[ \t]*(?:\[OvenHeart [^\]\n]+\][ \t]*(?:\n|$)"
    r"|<system_reminder>(?=User ID:|Current datetime:))"
)
CODE = re.compile(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`")


def clean_reply(text):
    protected = [match.span() for match in CODE.finditer(text)]
    for match in INTERNAL_START.finditer(text):
        if not any(start <= match.start() < end for start, end in protected):
            return text[: match.start()].rstrip()
    return text
