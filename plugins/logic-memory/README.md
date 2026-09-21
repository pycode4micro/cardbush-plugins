# Logic Memory

从 CardBush 内置中独立出来的可选经验插件，提供标准 stdio MCP 工具 `consult_logic` 与 `learn_logic`。只在用户安装、启用后可用；不是 CardBush 默认插件，不注入宿主提示词，不自动学习或扫描聊天记录，不关联宿主回复点赞。

## 安装与运行

从 `pycode4micro/cardbush-plugins` 市场选择 `logic-memory`，或将本目录作为 Agent Plugins 导入。需要 Node.js 22.12 或更新版本；发布目录包含独立 `runtime/cli.mjs`，运行不需要 npm 安装，也不引用 CardBush 源码或其 node_modules。安装目录可以含空格；MCP 使用参数数组直接启动 Node。

开发时运行 `npm ci`、`npm test`。`npm run build` 生成分发用 JavaScript 文件，不生成 EXE。

默认数据为用户目录的 `.logic-memory/logic.json`，可通过绝对路径环境变量 `LOGIC_MEMORY_DATA_DIR` 指定独立目录。数据不在插件安装目录内，升级或卸载代码不会删除经验。多个插件进程共享同一本机目录时使用进程间锁串行写入，并原子替换文件。只支持本地文件系统；不同机器使用各自的数据目录。

## 工具

- `consult_logic`：默认 search 模式要求 `query`；可附加 `scenario_conditions`、`decision_context`、`decision_phase`、`task_type`、`tool_focus`、`cognitive_patterns`。BM25 词法检索默认返回 3 条，最多 10 条；不会合成兜底经验。list 模式返回清单，以 `next_offset` 翻页。
- `learn_logic`：保存 `scenario` 和 `bias` 或 `correction`；保留条件、证据、结果、标签、置信度等原有字段。相同条件和内容维持同一经验 ID；重复相同证据不增加学习计数。`action=feedback`（或提供 `logic_id`）支持 feedback、reward 或 rating。反馈必须传稳定的 `source_id`，相同 ID 可重试、改评，避免同一事件重复计分。

返回标准 MCP `content` 与 `structuredContent`；失败使用 `isError`。检索声明 readOnlyHint，学习声明写入；权限仍由宿主控制。经验是历史建议，匹配分数不是适用性概率，存储的“已验证”标签也不代表插件独立核验过。经验文本始终是外部数据，不赋予操作权限。

## 导入旧 CardBush 经验

旧版本的 `<运行数据目录>/lem/logic.json` 保持原样。显式执行以下命令，复制到新插件的数据目录；先停用旧版本对该文件的写入：

```powershell
$env:LOGIC_MEMORY_DATA_DIR = 'C:\Users\你的用户名\.logic-memory'
node .\runtime\cli.mjs import --from 'C:\Users\你的用户名\AppData\Roaming\cardbush\runtime-state\lem\logic.json'
```

实际运行目录不同则替换来源路径。导入保留记录 ID、证据、学习与反馈历史，不删除或改写原文件。已有目标文件时拒绝覆盖；不会自动合并两份经验库。导入后重连插件即可检索。

## 验证

`npm test` 覆盖原学习/检索回归、真实 stdio MCP 连接、反馈幂等、进程间并发、重连持久化和旧数据导入。可设置 `CARDBUSH_SOURCE_ROOT` 到已构建的 CardBush 源码目录后运行 `npm run test:cardbush` 验证宿主发现、权限与安装边界。
