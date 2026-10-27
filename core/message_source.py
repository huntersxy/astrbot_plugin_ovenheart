"""Keep the current author's words separate from native quoted context."""

from astrbot.core.message.components import Reply


def own_message_text(event):
    parts = event.get_messages()
    if any(isinstance(part, Reply) for part in parts):
        # Summaries and other plugins may flatten the quote into message_str.
        # Reuse the host renderer to retain mentions and media markers while
        # excluding the quoted chain; losing an @ would also lose its subject.
        return event._outline_chain(
            [part for part in parts if not isinstance(part, Reply)]
        )
    return event.get_message_str()
