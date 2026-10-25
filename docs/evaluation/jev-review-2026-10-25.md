# Jev 输入、调度与费用优化记录

日期：2026-10-25。对照基线：OvenHeart `c46aa60`（0.60.0）、Oven Multi `3f5686e`（1.57.0）。目标是减少重复调用与输入，同时保留原有规划、自检、人工审核及明确唤醒优先级。

## 来源与许可证

从 AstrBot 当前市场清单定位截图中的 10 项：Oven Multi 使用本地仓库，其余 9 项已浅克隆。下列提交与当时市场版本一致。只阅读第三方源码，不安装或执行其代码；本轮实现独立编写，没有复制第三方源码片段。两个未发现明确许可证的仓库仅作机制参考。

| 插件 / 固定源码 | 许可证 | 主要观察 |
| --- | --- | --- |
| [唤醒增强](https://github.com/Zhalslar/astrbot_plugin_wakepro/tree/bee5925310f3a6b45e7355adcd377ba0627831ce) | GPL-3.0 | `core/step/jev.py` 先排除命令、直接唤醒和空消息；`core/jev.py` 区分候选与背景，按完整记录控制输入预算。 |
| [主动会话](https://github.com/singularity2000/astrbot_plugin_active_message/tree/a36c5ea3bc8e56ea7f5d3c98b0aa81a4d4384ba5) | AGPL-3.0 | `active_message/decisions.py` 对最终渲染的说明与上下文共同计预算，并保留结构化调用诊断。 |
| [心流主动回复](https://github.com/advent259141/Astrbot_plugin_Heartflow/tree/d986b5e4b8e5480e30b2ef241e68e2782a0dd6fa) | AGPL-3.0 | `main.py` 把五项 Score 放入一次判断；`jev_client.py` 有独立图像数量和请求时限。每次重建会话不适合我们的长驻链路。 |
| [消息防抖](https://github.com/advent259141/astrbot_plugin_debounce/tree/0d8aaaf3285d535b76e2878159a9a8679e81a3f5) | AGPL-3.0 | `main.py` 与 `jev_client.py` 把完成度判断、连续图文合并和调用预算分开；不宜让完成度判断阻塞明确 @。 |
| [Jev 主动回复](https://github.com/muyouzhi6/astrbot_plugin_jev_active_reply/tree/2952a61b891c873989759e8faee132a7dc9ec602) | 未发现明确声明 | 只研究 `client.py` 的过载退避、有限调度和输入绑定设计，不复制代码。 |
| [Jev 主动回复门控](https://github.com/Sodapopper-pixel/astrbot_plugin_jev_gate/tree/c4636b83616f20e71545544fca2e24255f1e4f37) | MIT | `main.py` 单请求询问多个 Noul/Score，分开“提名、接续、等待”；其注释也指出所有维度 AND 容易过严。我们继续只问必要问题。 |
| [群间 Chat Dynamics](https://github.com/ysyhlly/astrbot_plugin_chat_dynamics/tree/c91f15cf1f8ad97b0922fa6267153b546b9014e7) | MIT | `core/jev_decision.py` 使用有限目标选项、身份/引用关系与整请求预算，区分参与判断和表现风格置信度。 |
| [Jev 意图雷达](https://github.com/Zyw052/astrbot_plugin_jev_radar/tree/ae7501e594a87119e18acc5aea3a4bcebf839c4a) | MIT | `main.py` 有调用计数、额度与冷却；`radar_core.py` 使用消息哈希和本地脱敏。我们的新增统计只记录计数和用量，不记录聊天原文。 |
| [QQ 群聊增强](https://github.com/rytte/astrbot_plugin_qq_group_enhance/tree/71c80de50be5ed1a8ff99423d44f5d4065beb18b) | 未发现明确声明 | 只研究 `jev.py` 将多个候选放入同请求、按预算处理完整记录的机制，不复制代码。 |

## 本轮落地

- 两插件的 Jev 请求按连接、密钥、模型、完整输入、问题和超时精确缓存；同一时刻相同请求共享一次 HTTP。最多八个不同请求同时进行，过时请求不排队。一个等待者取消不伤及其他调用，最后一个取消会中止请求。
- Oven Multi 的主动判断和意图/情绪判断共用传输层、连接池、精确缓存、退避和统计。重试仍受配置控制，但全部尝试共用单轮时限；尊重 `Retry-After`，鉴权/配置错误及无效测量不会反复付费重试或进入成功缓存。
- 学习核验把英文规则放在公共状态中，每题只引用候选编号；同一原始证据只发送一份，候选按证据引用。不同原始消息即使文本相同仍保留独立证据，不改变入库的证据数量。只保留与这些证据有关的上下文，不把旁人的发言变为学习证据。
- 主动参与保留不同成员别名、引用作者、自己的发言和媒体状态。预算紧时先移除无关背景。若规划器选择旧消息，将其后已有回答一并提供，减少重复接话。
- 工坊显示本次插件加载以来的 Jev 请求、缓存、并发合并及上游报告 token；不把缓存命中重复计费，不暴露连接凭据或原文。

主模型规划和学习自检仍保留。直接用 Jev 多个分类结果替换完整规划器会改变人格、记忆和表达意图的使用，需要单独评估，不能从这批小样本推导其等价性。意图/情绪开关保持操作者设置。

## 实测与限制

[公开合成用例](jev-cases.json)，[原始数值](jev-benchmark-2026-10-25.json)。从生产容器经已配置中转调用 `jev-1.14.0`，共 6 个场景、12 次请求，每版各有 9 项预期判断；阈值使用 0.6。没有使用真实群聊原文、发送 QQ 消息或启动真实学习批次。

| 范围 | 旧版输入 token | 新版输入 token | 变化 |
| --- | ---: | ---: | ---: |
| 三候选共享学习证据 | 865 | 600 | -30.6% |
| 引用作者/一次性反应核验 | 630 | 539 | -14.4% |
| 学习两场景合计 | 1495 | 1139 | -23.8% |
| 全部六场景合计 | 3305 | 3093 | -6.4% |

两版输出均为 182 token。中位请求耗时 0.249 秒与 0.248 秒，基本持平，不声称网络推理显著加速。参与场景为保留成员/引用关系及后续回答，部分输入略有增长。

预期判断从 8/9 到 9/9；唯一旧版未通过的是“较早问题已被机器人回答，成员也已致谢”，旧版输入没有后续消息，对该问题给出 0.98；新版保留完整局部进展后为 0.50，不再越过 0.6 门槛。这是可复核的小样本现象，不是整体准确率、概率校准或生产长期效果结论。

本地 HTTP 回归另验证：12 个相同并发请求只发生 1 次网络调用，缓存复用不重复累计 token；不同密钥/模型不混用结果；取消、服务端退避、无效返回、关闭和并发上限均覆盖。不能把该重复请求压力场景的节省率套用到全部真实流量。

仅生成新旧请求而不调用模型：

```powershell
python scripts/benchmark_jev_payloads.py --baseline c46aa60 --output synthetic-payloads.json
```

该脚本只生成公共合成输入，不读取线上凭据。实际费用取决于中转定价，文中的 token 节省不是账单金额承诺。
