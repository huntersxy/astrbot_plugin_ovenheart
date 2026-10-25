# LivingMemory 迁移与停用

用户在额度保留线后明确授权：只完成本次迁移和停用。MaiBot 完整对齐目标继续暂停。

## 已完成

- 生产烤箱之心仍为 0.67.0、代码 `aa67fea8cc17a0f98627c442f10ad230ac99d0ae`，迁移后已恢复启用。
- 官方 LivingMemory 2.7.1 已通过原生 Panel 停用；源码、版本、配置和原始数据库完整保留。
- 14 个现有及历史会话完成迁入，其中补登记两个仅在旧库存在的历史会话。按旧配置的全局来源加本会话来源筛选，保留原人格、来源和有效期，不复活已删除或过期内容。
- 源库去重后有 1,804 个有效记忆单元。各会话账本合计 24,897 个映射，包含旧全局记忆在不同会话中的副本，不代表新增了这么多独立旧记忆。
- 全部通过实际原生 SDK 的 `RoomState.legacy_ingest` 写入，逐会话核对账本、完成标记和 SQLite 完整性；待迁入队列已清空。14 个作用域的最终独立复核通过。
- `native_memory_enabled`、`memory_enabled` 均为 true；91 项原生工具已注册；插件配置未改变，AstrBot 和 QQ 容器未重启。

## 备份与执行记录

- 本次最终备份：`/vol1/1001/bot/backups/livingmemory-retirement-20261025T232622Z`，含源码、配置及 47 份一致性数据库快照。
- 最终完整旧库快照：`data/plugin_data/astrbot_plugin_ovenheart/amemorix/livingmemory_imports/retirement-livingmemory-retirement-20261025T232622Z.sqlite3`（相对服务器 Bot 根目录）。
- 备份目录中的 `migration.json` 保存逐会话结果，`audit.json` 保存切换后状态。
- 本机无凭据执行器保存在 `.codex/tools/ovenheart-retire-memory-{controller,worker}-20261026.py`。它们是本次一次性运维工具，不随插件发布。
- 第一次离线执行误用了少一层 `rooms` 的旁路目录，已停止，正式原生库未被覆盖。纠正后重新完成迁移；旁路数据移入最终备份的 `aborted-staging`，不再留在原生运行目录。

## 验证范围

本次没有发送 QQ 消息，也没有调用生成或嵌入模型。离线导入使用 SDK 的元数据、图谱与文本索引路径，原有向量文件保留，新增向量待正常运行中的真实嵌入流程补齐。没有把数据迁入成功当作真实聊天效果验收。

LivingMemory 停用后，工坊旧桥接状态中的 `memory.available`、`reply_source_binding` 为 false 属于旧插件不可用的状态；本次已另行确认原生记忆开关启用。迁移覆盖上述 14 个作用域，今后新增会话沿用原生会话、人格隔离策略。
