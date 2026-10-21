# Persona 人格工坊

一个面向 AstrBot 的人格、语言和对话行为学习插件。借鉴 MaiBot 将身份底稿、表达习惯、情境化学习结果分开的思路，通过真实聊天证据积累习惯，并与 LivingMemory 联动理解长期上下文。

**原生 AstrBot Panel 页面，无额外端口、无前端构建、无新增运行时依赖。**

## 使用

1. 使用 **AstrBot 4.29.1 或以上的 4.x 版本**，配置可用的聊天模型。
2. 将发行 ZIP 上传到 AstrBot 的本地插件安装入口，或将插件目录放入宿主的 `data/plugins/astrbot_plugin_persona`，然后重载插件。
3. 打开 Panel 的插件页面 **人格工坊**，让机器人在已启用的群聊或私聊收到几条普通文字消息。会话会自动出现。
4. 在「学习设置」选择审核或高置信度自动生效，也可指定学习对象与学习模型。
5. 积累到默认 12 条素材后后台学习，或点击「开始学习」。在「学习库」检查来源、修改与批准结果。

详细步骤与限制见 [中文指南](docs/zh/README.md) / [English guide](docs/en/README.md)。

## 学习什么

| 类别 | 学习内容 | 应用方式 |
| --- | --- | --- |
| 人格倾向 | 与身份锚点一致的稳定性格、相处习惯 | 补充现有 AstrBot 人格 |
| 表达方式 | 特定情境中的句式、语气、说话节奏 | 回复时挑选相关习惯 |
| 群聊词汇 | 反复出现、含义明确的本地词汇 | 与当前输入相关时参考 |
| 行为策略 | 共情、澄清、倾听、接玩笑等对话应对 | 在宿主已触发的回复中应用 |

人格底稿可人工编辑，支持按会话暂停学习或暂停应用。新结果默认待审核；切换自动生效后，达到置信度与证据门槛的新结果直接生效，其余仍待审核。已有人工修改、拒绝和停用状态不会被相同候选覆盖。

学习条目保留原始证据摘录；人格和条目操作有最多 30 个可恢复快照。恢复旧版本后，后来新增的条目保留为停用状态，防止下一批学习立即把它们重新激活。

## 与现有插件配合

- **LivingMemory**：可选。已适配所提供的 `2.7.0-beta.3` 接口，运行时检测当前实例，复用其访问控制、会话/用户/全局作用域与人格过滤配置。只在批次学习时检索，避免每次回复重复调用记忆。未安装、未就绪、停用或检索失败时仍可独立学习。
- **QQ 能力增强**：可选。不依赖其内部实现，不替换它的工具和事件处理。人格工坊不会自动发起 QQ 操作。
- **AstrBot**：保留宿主原有提示词，不修改宿主人格、不修改其他插件源码。插件数据按完整会话来源与宿主当前生效人格共同隔离。

LivingMemory 检索可能更新访问时间或触发其原有旧作用域迁移；本插件不向记忆库另行写入人格或用户记忆。

## 行为边界

这是通过结构化学习和提示词应用实现的习惯学习，不训练模型权重，也没有复制 MaiBot 的完整自主调度器。当前版本不主动唤醒群聊、不自行决定发送时机、不自动调用 QQ 工具；行为学习作用于 AstrBot 正常触发的回复。模型的实际学习质量需要用真实会话和所选模型观察。

采集队列、批次、素材保留、模型超时和学习并发都有边界。失败批次不消耗素材，后台经过冷却后重试。原始消息默认保留 14 天、每个会话人格最多 300 条，定期清理；条目证据摘录随学习条目与版本快照保留。页面提供清除当前会话原始素材的入口，**该操作不删除条目证据摘录**。

## 开发与验证

```powershell
uv sync --python 3.12
# Install a compatible local host for SDK integration tests:
uv pip install --python .venv/Scripts/python.exe -e ../AstrBot
uv run --no-sync pytest -q
uv run --no-sync ruff format .
uv run --no-sync ruff check .
node --check views/workshop/app.js
node --test tests/test_frontend.mjs
uv run --no-sync python scripts/package.py
```

Linux/macOS 的解释器路径为 `.venv/bin/python`。插件核心语法兼容 Python 3.10+，实际宿主 4.29.1 需要 Python 3.12+。未安装 AstrBot 时 SDK 集成测试会明确跳过，SQLite 与学习校验测试仍可独立运行。

`tests/preview_server.py` 是显式启动的本地验收夹具，使用临时数据库和模拟模型，不随插件启动、不进入发行 ZIP。启动命令见文件说明。文档截图标注演示数据，不能作为真实模型输出证明。

## License

AGPL-3.0-or-later. Independent implementation inspired by MaiBot's public architecture; neither MaiBot nor LivingMemory source implementations are bundled.
