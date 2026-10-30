# 结构化上下文窗口与 MaiBot 的比较

研究日期：2026-10-30。

比较基准：OvenHeart 接管模式实现（本次版本 `0.72.0`）与本机 MaiBot `5c7637e6913c84d1c3d5e742fa9e95a7e70d3501`。MaiBot 参考仓库为工作区相邻的 `MaiBot/`。结论针对这些源码快照，不表示上游最新版本，也不表示模型实际回复效果已经验收。

本次以 OvenHeart 的 `structured` 群聊路径与 MaiBot 的 Maisaka 普通群聊路径为主。数值使用当前 OvenHeart 默认窗口和 MaiBot 源码默认配置；MaiBot 的实际运行配置可能不同。Focus 聚合模式有独立的上下文压缩路径，不能套用普通模式的全部数值。

## 核心区别

OvenHeart 在 AstrBot 的原生对话历史和请求管线上，补充一份有界的结构化群聊窗口、明确的回复目标和相关记忆。MaiBot 自己维护运行时历史，再从这份历史构造 Planner 与 replyer 的不同输入。

两者都有消息身份、回复目标、机器人自己的发言、媒体占位和旧记忆。主要区别在于历史由谁维护、如何裁切，以及规划与回复分别保留了哪些信息。

| 比较项 | OvenHeart structured | MaiBot Maisaka 普通群聊 |
| --- | --- | --- |
| 历史归属 | AstrBot 原生历史继续保存；接管 runner 时只替换模型可见的群聊时间线，结束后恢复原生消息供宿主保存 | 自己维护 `_chat_history`，其中包含真实收发消息及内部规划、工具和参考信息 |
| 收集单位 | 消息记录包含成员 ID、消息 ID、时间、正文、引用、提及、媒体、自身标记 | `SessionBackedMessage` 保留原始 `SessionMessage`，其他类型表示模型输出、工具结果或参考 |
| 规划输入 | 与回复共用最近最多 2 倍基准条数；默认基准 40，不再有独立 24000 字符裁切 | 群聊基础窗口默认 40，选择器使用 2 倍缓存窗口，并维护完整工具轮次 |
| 回复历史 | 与规划共用同一条群聊时间线；AstrBot 原生历史中的结构化窗口只标记为临时，不会重复写入 | reply 工具复制共享历史，replyer 过滤后保留真实收发的 IM 消息；没有同样的独立 12 条附加窗口 |
| 角色表达 | 群聊窗口以结构化数据放入本轮临时 user 内容；原生 user/assistant 历史继续保留 | replyer 将真实用户消息转为 user 内容，将实际发送的 `guided_reply` 转为 assistant 内容 |
| 回复对象 | Planner 使用本轮消息序号，宿主校验并冻结目标；直接回复也构造明确目标 | Planner 的 reply 工具选择 `msg_id`，定位原始消息后交给 replyer |
| 相关前文 | 额外保留目标、该成员 10 分钟内最近 4 条前文、机器人最近 3 条自身发言及引用 | replyer 使用共享历史中过滤出的真实聊天，并接收明确目标与 `reply_reference` |
| 媒体 | 附加窗口主要提供描述、状态及图片引用；当前原请求的媒体由 AstrBot 保留，历史图片可通过工具查询 | 保留原消息组件，支持将窗口内图片作为实际多模态内容输入，也支持文字占位、描述刷新与图片数量限制 |
| 淘汰和回想 | 接管账本超过基准两倍后裁回基准；裁下的真实消息交给中期记忆，原生对话另行保留 | 普通历史超过基础窗口 2 倍后，在轮末裁至基础窗口附近；工具轮次按整体处理，裁下的历史可触发聊天回想和学习 |
| 重启与清理 | 持久账本按会话、人格及原生对话 ID/epoch 管理；新范围可从宿主消息库补种 | 启动从消息库恢复基础窗口一半的近期记录，尊重 `/clear` 边界，并附加离线间隔说明 |

## 数值的实际含义

接管模式只保留两层：

1. **账本**：`Store.chat_context()` 每个范围保留最近不超过两倍基准条数；首次恢复从宿主消息库补种半个基准窗口。
2. **共享模型时间线**：`ConversationContext.model_messages()` 将同一批真实成员、引用、媒体和机器人消息分别转换为临时的 user/assistant 消息，规划器和最终回复都读取它。裁切只按消息条数，不另加一个 12 条或 12000 字符窗口。

当前目标、`reply_focus`、人物画像、召回记忆、人格、原生历史、工具定义以及自定义模板另行计入请求。接管时间线的消息本身不写回原生历史，避免同一群聊记录在持久化层重复增长。

MaiBot 普通群聊的基础默认值也是 40，但统计的是 `count_in_context` 条目，包含适用的内部消息或模型输出，不等同于 40 条群友发言。上下文选择器使用 `CONTEXT_SELECTION_CACHE_STABILITY_RATIO = 2.0`；历史超过 80 个可计数条目后，轮末处理会尝试裁到 40。参考条目及完整工具轮次可能使最终输入超出基础数量。replyer 再过滤真实 IM 消息，因此这些数字也不能直接当成 replyer 的真实聊天条数。

这两套机制都不能只用一个“窗口条数”代表最终模型输入量。

## 离线样例：较早目标保住了，附近对话未必保住

此前使用 OvenHeart `0.71.34` 的真实 `snapshot()`、`reply_messages()`、`focus()` 和 `payload_view()`，构造 40 条不超预算的结构化记录，选择第 10 条为目标。这个样例记录的是本次接管实现前的旧行为。

结果：

- Planner 快照包含 40 条。
- 回复附加窗口包含第 29～40 条。
- 第 10 条作为 `reply_message` 单独保留。
- 第 11、21、31 条通过自身发言 focus 保留。
- 目标附近其他成员的第 9、12 条不在这份附加上下文中。
- 此样例窗口为 2642 字符，整个回复数据为 3454 字符；两者预算口径不同。
- 将附加窗口条数或字符预算设为 0，仍保留明确目标与 focus。
- 原始 40 条记录没有被上述模型视图函数修改。

接管实现后的本地测试增加了两项可观察保证：规划和回复的 `reply_messages()` 都保留同一批 2 倍基准记录；runner 视图中的临时历史在结束后恢复为原生消息，宿主仍可按原规则保存。上述测试不证明模型实际回复质量。

旧样例中的“规划见过、回复附加窗口没保住”的差异已由接管模式的共享时间线消除；剩余风险转为模型 token 上限、多媒体解析和跨重启恢复效果，需要真实消息观察。

## 当前设计值得保留的部分

- 回复目标独立于附加窗口，不因把窗口调小而直接丢失。
- 同成员前文与机器人自身发言分开组织，便于核对“你刚才说的”和后续更正。
- 账本按实际会话和人格管理，不依赖昵称判断身份；共享习惯设置不等于合并群聊账本。
- 接管 runner 的模型输入与 AstrBot 持久化历史分离，切换回 `native` 时仍使用宿主原生对话。
- 图片描述未完成时保留占位与消息身份，描述完成后更新原记录。
- 新对话、重置及过期规划都有会话边界检查。
- OvenHeart 自身发言记录和 Planner 回执使用实际成功发送的段落；AstrBot 原生历史仍按宿主机制保存，两者的发送记录口径需要分别观察。

## 优先研究的问题

1. **整个最终请求的 token 上限。** 记录统一时间线、目标/focus、人格、记忆和工具定义的实际 token，确认不同模型上下文长度下不会过早丢掉当前目标。
2. **媒体信息是否足以完成任务。** 区分当前图片、历史描述、可检索原图和仍在识别的图片；使用需要视觉细节的实际问题观察输入。
3. **跨轮和长时间间隔。** 比较重启、清空、新对话、长时间静默及中期回想的行为。MaiBot 有专门的离线时间说明；OvenHeart 仍需结合真实请求检查。

第一优先项已经落实为规划与回复共用同一条群聊时间线；仍需真实 QQ 消息观察引用、多媒体和长时间运行下的回复质量。

## 源码入口

OvenHeart，路径相对本仓库：

- `core/store.py:388`：有界持久账本、对话 ID/epoch 与清理。
- `core/chat/conversation.py:73`：structured 生效条件。
- `core/chat/conversation.py:105`：消息、引用与媒体的记录形式。
- `core/chat/conversation.py:149`：宿主历史恢复。
- `core/chat/conversation.py:423`：规划快照。
- `core/chat/conversation.py:456`：目标、同成员前文与自身发言 focus。
- `core/chat/conversation.py:561`：发送后写入自身发言。
- `core/chat/reply_payload.py:53`：回复窗口条数与紧凑字符预算。
- `core/chat/reply_payload.py:72`：模型视图及 focus 内部引用。
- `core/memory/midterm.py:41`：窗口外记录的中期记忆处理。
- `main.py:1308`：继续使用宿主请求及原生对话。
- `main.py:2154`：回复请求附加目标与上下文。
- `main.py:3161`：实际发送记录与部分发送回执。

MaiBot，路径相对相邻的 `MaiBot/`，对应上述固定提交：

- `src/config/official_configs.py:807`：群聊基础窗口默认值。
- `src/maisaka/runtime.py:236`：当前会话的基础窗口。
- `src/maisaka/runtime.py:321`：启动恢复。
- `src/maisaka/runtime.py:454`：离线间隔参考说明。
- `src/maisaka/chat_loop_service.py:1006`：模型 Context Items 组装。
- `src/maisaka/chat_loop_service.py:1394`：缓存窗口、消息选择与工具轮次补齐。
- `src/maisaka/context/post_processor.py:30`：轮末裁切与工具关系规范化。
- `src/maisaka/context/messages.py:617`：保留原始消息的 SessionBackedMessage。
- `src/maisaka/context/planner_messages.py:17`：消息 ID、引用、自身标记等前缀。
- `src/maisaka/builtin_tool/reply.py:459`：向 replyer 传递共享历史副本。
- `src/chat/replyer/maisaka_generator_base.py:627`：真实聊天的模型角色及媒体转换。
- `src/chat/replyer/maisaka_generator_base.py:955`：replyer 排除非真实聊天内容。
- `src/maisaka/reasoning_engine.py:1267`：轮末裁切、回想与学习衔接。
- `src/maisaka/focus/group_runtime.py:792`：Focus 独立压缩流程。

本次验证为源码走读和本地合成数据探查，没有执行付费模型调用，没有发送 QQ 测试消息，没有修改生产配置或运行逻辑。
