"""Keep interactive text on the native reply pipeline, without a second sender."""

import copy

from astrbot.core.agent.message import TextPart

TEXT_DELIVERY_RULE = (
    "\n[OvenHeart text delivery]\n"
    "本次是当前会话的互动回复。需要查询或处理媒体时可先使用相应工具，"
    "需要回复文字时，结束时将要说的话直接输出为非空回复正文，交由宿主分段和投递。"
    "仅需媒体且已成功发送时，无需补充文字。"
    "send_message_to_user 只用于单独发送媒体、提及或向明确指定的其他会话发送消息；"
    "不要用它发送当前会话的文字或媒体配文，不要用空正文或‘已回复’代替实际答复。\n"
)


def bind_text_delivery(event, request):
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.tools.message_tools import SendMessageToUserTool

    if isinstance(event, CronMessageEvent) or request.func_tool is None:
        return
    tool = request.func_tool.get_tool("send_message_to_user")
    if not isinstance(tool, SendMessageToUserTool):
        return
    # Keep system/tools schemas cache-stable for days. Remind via temporary
    # user content and enforce with the call wrapper instead of rewriting the
    # host tool definition or the system prefix every request.
    if not any(
        isinstance(part, TextPart) and TEXT_DELIVERY_RULE.strip() in part.text
        for part in request.extra_user_content_parts
    ):
        request.extra_user_content_parts.append(
            TextPart(text=TEXT_DELIVERY_RULE).mark_as_temp()
        )
    if getattr(tool, "ovenheart_text_delivery", False):
        return
    native_call = tool.call

    async def call(context, **arguments):
        source = context.context.event
        session = arguments.get("session") or source.unified_msg_origin
        current = session in {source.unified_msg_origin, source.get_session_id()}
        if (
            not isinstance(source, CronMessageEvent)
            and current
            and any(item["type"].lower() == "plain" for item in arguments["messages"])
        ):
            return (
                "此调用未发送任何消息。当前聊天的文字（包括媒体配文）请直接作为回复正文输出，"
                "由宿主统一分段和投递，不要汇报已回复。图片、文件、语音等请另用本工具单独发送。"
            )
        return await native_call(context, **arguments)

    local = copy.copy(request.func_tool)
    routed = copy.copy(tool)
    routed.call = call
    routed.ovenheart_text_delivery = True
    local.tools = [routed if item is tool else item for item in local.tools]
    request.func_tool = local
