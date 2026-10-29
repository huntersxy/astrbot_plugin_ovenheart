"""Keep interactive text on the native reply pipeline, without a second sender."""

import copy

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
    if TEXT_DELIVERY_RULE not in request.system_prompt:
        request.system_prompt += TEXT_DELIVERY_RULE
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
    # skills_like first sees only this description, then a parameter-only
    # schema. Both phases must describe the same delivery contract.
    routed.description = (
        "Send standalone media (image, record, video, file) or mentions. "
        "For the current chat, write text and media captions directly in the final assistant response; "
        "never use this tool for ordinary text replies. "
        "The plain type is only for another explicitly specified session. "
        "Do not replace the actual reply with a statement that it was sent."
    )
    routed.parameters = copy.deepcopy(tool.parameters)
    properties = routed.parameters["properties"]
    properties["messages"]["description"] = (
        "Ordered media or mention components. Current-chat text and captions belong "
        "in the final assistant response. Plain text requires another explicit session."
    )
    component = properties["messages"]["items"]["properties"]
    component["type"]["description"] = (
        "image, record (voice), video, file, or mention_user for the current chat. "
        "plain is only for another explicitly specified session."
    )
    component["text"]["description"] = (
        "Plain text for another explicitly specified session only. "
        "Write current-chat text directly in the final assistant response instead."
    )
    properties["session"]["description"] = (
        "Leave empty for current-chat media or mentions. To send plain text, specify "
        "another session as platform_id:message_type:session_id; do not target the current chat."
    )
    local.tools = [routed if item is tool else item for item in local.tools]
    request.func_tool = local
