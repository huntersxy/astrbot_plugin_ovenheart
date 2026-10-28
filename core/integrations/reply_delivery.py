"""Keep interactive text on the native reply pipeline, without a second sender."""

import copy


def bind_text_delivery(event, request):
    from astrbot.core.cron.events import CronMessageEvent
    from astrbot.core.tools.message_tools import SendMessageToUserTool

    if isinstance(event, CronMessageEvent) or request.func_tool is None:
        return
    tool = request.func_tool.get_tool("send_message_to_user")
    if not isinstance(tool, SendMessageToUserTool) or getattr(
        tool, "ovenheart_text_delivery", False
    ):
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
    routed.description += (
        " 当前互动回合：本会话的文字与媒体配文直接输出为回复正文；"
        "本工具用于单独发送媒体或向其他会话发送消息，不用于当前聊天的文字回复。"
    )
    local.tools = [routed if item is tool else item for item in local.tools]
    request.func_tool = local
