<div align="center">

<img src="logo.png" alt="OvenHeart Q 版角色图标" width="112" height="112">

# 烤箱之心 · OvenHeart

**把相处中的习惯，慢慢变成自己的表达。**

面向 AstrBot 的人格、语言与对话行为学习插件。<br>
从聊天证据中积累习惯，由你决定它们如何生效。

[![AstrBot](https://img.shields.io/badge/AstrBot-4.29.1%2B-49768b?style=flat-square)](https://github.com/AstrBotDevs/AstrBot)
[![Version](https://img.shields.io/badge/version-0.56.3-49768b?style=flat-square)](CHANGELOG.md)
[![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-68766b?style=flat-square)](LICENSE)

[快速开始](#快速开始) · [功能](#从聊天到习惯) · [中文指南](docs/zh/README.md) · [English](docs/en/README.md) · [更新记录](CHANGELOG.md)

</div>

---

OvenHeart 的相处方式来自 [MaiBot（麦麦）](https://github.com/Mai-with-u/MaiBot)。麦麦是一个以真人风格聊天为目标的独立数字生命框架：在群聊里模仿身边人的说话风格，自主理解新词和圈内黑话，把握聊天气氛决定何时开口、何时闭嘴，并基于人格理论在相处中不断积累对人的了解。OvenHeart 把这套「从相处中学习」的思路带进 AstrBot，四条主线与麦麦一一对应：

- **表达方式学习**：同麦麦的表达学习器——从群成员的真实发言中提炼句式、语气和发言节奏，每条习惯保留原话片段作为证据；回复前按本轮情境检索相符的表达注入提示词，只影响措辞，不提供事实。
- **群聊词汇**：同麦麦的黑话学习——理解一个群的新词、梗和圈内说法在本地语境的含义，只在学到的会话里使用。
- **参与决策**：同麦麦的心流意愿系统——先判断这条消息值不值得接：接话（reply）、等一等（wait）还是旁观（observe），再看说什么，不做每句必回的客服。
- **人格倾向**：同麦麦的「成为人类」——在长期相处中积累自己的性格倾向和情境应对方式，而不是停留在一份写死的人设。

两者都不训练模型权重：习惯是带证据的数据条目，靠检索与提示词生效。

不同的地方在宿主和掌控权。麦麦自成一体，自带消息管线、人格、记忆和表情包等完整系统；OvenHeart 只是 AstrBot 的插件——收发、唤醒、权限、工具和原有人格都属于宿主，它不接管机器人，只往既有回复管线里补充习惯与参与判断，也不自带记忆，可选联动官方原版 LivingMemory。掌控权上，麦麦的学习闭环以自动运行为主；OvenHeart 默认每条新习惯都停在人工审核，证据、自检结论和置信度在学习库里分开呈现，由你批准、修改或停用，确认可靠后才切换为自动生效。范围上它只做表达、词汇、人格倾向与群聊参与这几件事，不是麦麦的复刻。

插件市场分类为「AI 增强」，当前支持 **QQ / OneBot v11（aiocqhttp）**；其他平台未适配。

## 从聊天到习惯

| 能力 | 你能得到什么 |
| --- | --- |
| 人格与行为学习 | 在身份底稿约束下，积累性格倾向与适合具体情境的应对方式 |
| 表达方式学习 | 学习用词、句式、语气和发言节奏，保留有原文依据的短片段 |
| 群聊词汇 | 学习本地词义，始终按来源会话与人格隔离 |
| 可管理的学习库 | 全会话查看，按类别、状态、自检结论和问题分类筛选，支持本页批量审核 |
| 群聊参与 | 可选旁观或自主接话；名称提及可直接交给宿主回复，工具支持搜索与黑白名单 |
| 上下文与记忆联动 | 复用宿主历史，可选接入 LivingMemory、嵌入和重排序 |

```text
聊天素材 → 候选习惯 → 原文核对与自检 → 人工审核 / 自动生效
                                            ↓
                              按本轮情境检索 → 供回复参考
```

模型置信度是提取阶段的自评，不等于自检通过或真实准确率。面板分别展示三者，避免只凭一个百分比判断质量。

<details>
<summary><strong>看看人格工坊与学习库</strong> · 演示数据，非真实聊天记录</summary>

**人格底稿与预览**

![人格底稿与预览示意](docs/assets/profile.jpg)

**自检分类与审核**

![学习库自检分类与审核示意](docs/assets/library-triage-v0.56-desktop.jpg)

截图为本地模拟界面；布局与文案以当前版本为准。

</details>

## 快速开始

### 1. 安装

使用 **AstrBot ≥ 4.29.1、< 5**，先配置一个可用的聊天模型。在 WebUI 的插件管理中选择通过链接安装，填写：

```text
https://github.com/huntersxy/astrbot_plugin_ovenheart
```

也可在 AstrBot 根目录手动安装：

```bash
git clone --branch main https://github.com/huntersxy/astrbot_plugin_ovenheart.git data/plugins/astrbot_plugin_ovenheart
```

安装后重载插件，打开插件卡片的 **人格工坊** 页面。

### 2. 设定底色

在「人格底稿」补充身份、表达基调和行为边界。AstrBot 原有人格继续生效；这里留空也能使用。

在「学习设置」指定学习对象，或直接填写不学习的 QQ 号。新结果默认需要审核，自主参与默认关闭。

### 3. 开始学习

让机器人在允许的会话中收到普通文字消息。默认积累至少 **12 条**素材后进入后台学习，也可在工坊点击「开始学习」。

打开「学习库」，先看自检分组和证据，再批准希望应用的习惯。用人格底稿中的预览检查注入内容；调用模型生成示例回复会消耗模型额度。

> 密钥只在 AstrBot 提供商设置中填写。本插件选择的是提供商 **ID**，无需把密钥复制进插件、提示词或仓库。

## 按需要开启

| 需要 | 配置入口与行为 |
| --- | --- |
| 自动生效 | 学习设置 → 生效方式；仍检查证据、置信度、自检与人工锁定 |
| 分开培养不同会话 | 学习设置 → 会话隔离；默认共用底稿与非词汇习惯，原始素材不混用 |
| 点名字就回复 | 学习设置 → 提及时马上回复；填写名字、别名，默认关闭 |
| 主动参与群聊 | 学习设置 → 自主参与；先用仅观察查看决策，再按需启用自动模式 |
| 控制自主工具 | 在 AstrBot 当前工具目录中搜索、多选；默认空白名单只发文字 |
| 更精细地召回习惯 | 配置嵌入、重排序提供商 ID；不可用时按检索模式回退 |
| 查看模型实际收到的补充 | 学习与参与 → 每轮记录；Debug 可完整输出注入和阶段日志 |

## 与现有生态配合

- **AstrBot**：保留原有唤醒、会话、媒体、工具与权限处理。原生群聊上下文已开启时，插件不重复注入一份群聊记录。
- **LivingMemory**：可选，未安装也可独立学习。自主回复的记忆来源绑定需要兼容接口，是否可用以工坊状态提示为准。
- **QQ 增强插件**：可选。放行的工具仍受宿主和对应插件的权限约束；自主工具名单不限制原生直接回复。

缓存友好模式将本轮习惯放在临时尾部，不保存进宿主历史。历史裁剪、摘要、系统或工具变化仍可能破坏缓存；[配置与排查](docs/zh/README.md#缓存与上下文)说明了宿主轮数限制的影响。

## 数据与边界

原始素材默认保留 **14 天**，每个来源会话与人格最多 **300 条**。条目证据和版本快照另行保留，清除原始素材不会一并删除它们。Debug 日志可能包含完整聊天和注入正文，共享前请脱敏。

学习会调用已配置的模型服务，相关聊天片段可能发送给这些服务。请按实际群聊约定设置学习对象、黑名单和允许会话。

本插件通过检索与提示词应用习惯，不训练模型权重。当前以 AstrBot 本地 Agent 为主要路径；自主回复需要本地运行器，其他平台与第三方运行器的能力取决于各自实现。

## 文档与贡献

[中文使用指南](docs/zh/README.md) · [English guide](docs/en/README.md) · [更新记录](CHANGELOG.md) · [问题反馈](https://github.com/huntersxy/astrbot_plugin_ovenheart/issues)

提交问题时请附 AstrBot / 插件版本、平台、复现步骤及脱敏日志。不要提交密钥、数据库或真实群聊导出。

本地开发：

```bash
uv sync --python 3.12
uv pip install -e ../AstrBot
uv run --no-sync ruff format .
uv run --no-sync ruff check .
uv run --no-sync python -m pytest -q
node --test tests/test_frontend.mjs
uv run --no-sync python scripts/package.py
```

SDK 集成测试需要相邻的兼容 AstrBot 源码。

## 致谢与许可

运行于 [AstrBot](https://github.com/AstrBotDevs/AstrBot)，可联动 [LivingMemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory)。表达学习和聊天机制对照 [MaiBot](https://github.com/Mai-with-u/MaiBot)；0.67开发版直接移植其动态门控与概率评分，来源、原文哈希和 GPL-3.0 许可证保留于 [vendor/maibot](vendor/maibot/NOTICE.md)。其余实现遵循本仓库许可。

采用 [AGPL-3.0-or-later](LICENSE) 许可。
