# 开发指南

## 目录

```text
main.py              AstrBot 插件入口、生命周期与事件钩子
core/                配置、SQLite 存储、学习、检索、模型用量和 Panel API
core/chat/           群聊上下文、持续规划、目标绑定、回复节奏与参与调度
core/memory/         人物、通用、图片与中期记忆，以及记忆控制台
core/integrations/   AstrBot 提供商之外的可选插件与服务联动
views/workshop/      原生 Panel 页面、脚本和样式
core/vendor/         保留来源清单与许可证的参考实现
.astrbot-plugin/     AstrBot 界面国际化
_conf_schema.json    AstrBot 插件配置表单
metadata.yaml        插件元数据
requirements.txt     AstrBot 安装依赖
pyproject.toml       Python 项目与开发依赖
uv.lock              可复现的开发依赖锁
tests/              原生宿主、存储、聊天与前端检查
docs/               当前使用文档
```

`main.py` 是唯一宿主入口；其他模块使用所属职责包的路径。运行数据放在 AstrBot 的插件数据目录，配置以宿主配置为准。SQLite 初始化直接创建当前结构，运行记录与原生配置分别维护。

## 环境

准备 Python 3.12、uv、Node.js 和相邻的 AstrBot 源码。宿主路径可以调整为自己的工作区：

```bash
uv sync --python 3.12
uv pip install -e ../AstrBot
```

`requirements.txt` 与 `pyproject.toml` 的运行依赖保持一致。密钥只通过本机环境或标准输入提供，不写入代码、测试报告或命令行参数。

## 检查

按改动范围选择检查，结构调整或发布前核验安装包：

```bash
uv run --no-sync ruff check .
uv run --no-sync pytest tests/test_release_package.py -q
node --test tests/test_frontend.mjs tests/test_panel_loading.mjs
```

完整 Python 检查入口是 `uv run --no-sync pytest`。测试中的本地包直接来自当前工作区，不安装第二份插件代码。

持续 Planner 的动作参数在 `PlannerTool.call` 按实际工具 schema 校验。校验失败返回包含合法字段与重试说明的结构化结果，本次不执行动作，也不把异常堆栈交给模型。`reply_style` 控制篇幅，`expression_intent` 描述回应动作与语气，`attach_emoji` 使用真实候选的整数序号；`expression_style` 和 `attachment` 不作为别名接收。

## 发布

发布源码归档时，使用 Git 的归档功能；`.gitattributes` 排除了测试、开发文档和本地工作资料。运行数据和密钥不得加入发布包。

参考实现的原许可证与来源清单必须保留。修改自身适配层优先于修改第三方仓库。完成变更后使用 conventional commit，描述实际行为与必要验证。
