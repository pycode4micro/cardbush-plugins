# 飞书机器人独立插件

版本 **1.0.0**。通过官方 MCP Python SDK 提供 stdio 工具，自带运行代码、依赖锁文件和使用技能。默认 **读取开启、修改关闭**，启动参数优先于环境变量。目录可以整体移动、复制或分享。

插件不导入数据平台、千川工作流、数据库或其他插件，不读取它们的配置。`qianchuan.goods_card_paid_daily` 仅用于理解“按账号匹配页签并追加”的需求；所有业务数据由工具参数传入。发行包不含真实密钥、群地址、业务表格 token 或生产数据。

## 开始使用

需要 Python 3.11+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。首次运行会按 `uv.lock` 下载依赖；安装完成后仍需联网访问飞书。这里的“独立”指独立运行与配置，不是离线访问飞书。

1. 将整个 `feishu-bot` 文件夹放到固定位置。
2. 把 `.env.example` 复制到用户目录的 `.config/feishu-bot/.env`，在本机填写自建应用的 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`。也可用 `--env-file` 指定任意外部配置文件。
3. 运行脱敏配置检查。下面的 PowerShell 命令从插件目录执行：

```powershell
$feishuConfigDir = Join-Path $env:USERPROFILE '.config/feishu-bot'
New-Item -ItemType Directory -Path $feishuConfigDir -Force | Out-Null
# 仅在该文件不存在时复制，保留已有凭据：
if (-not (Test-Path (Join-Path $feishuConfigDir '.env'))) {
    Copy-Item -LiteralPath '.env.example' -Destination (Join-Path $feishuConfigDir '.env')
}
# 在本机编辑外部 .env 后检查配置；该命令不联网、不显示密钥。
uv run --frozen --no-dev feishu-bot --print-config
```

macOS/Linux 的默认配置位置同样是 `~/.config/feishu-bot/.env`。

### 在 Codex 中加载

标准入口是根目录 `plugin.json`、`mcp.json`；同时带有 `.codex-plugin/plugin.json`、`.mcp.json` 兼容清单。通过 Cardbush Plugins 市场安装完整插件（工具与技能）：

```shell
codex plugin marketplace add pycode4micro/cardbush-plugins --ref main
codex plugin add feishu-bot@cardbush-plugins
```

已经添加该市场时，先运行 `codex plugin marketplace upgrade cardbush-plugins` 更新市场索引，再安装插件。安装后新建任务，调用 `feishu_status` 检查权限和凭据配置。运行时只需要本插件与声明的 Python 依赖。不要同时注册下面的独立 MCP 连接，以免重复出现工具。

如果只需要工具服务，可以直接在支持 stdio 的 MCP 客户端注册。Codex CLI 示例（把路径改为本机插件目录）：

```powershell
codex mcp add feishu-bot -- uv run --project "D:/plugins/feishu-bot" --frozen --no-dev feishu-bot
```

这个方式注册 MCP 工具，不会安装插件技能。插件清单中的 `${PLUGIN_ROOT}`、`${PLUGIN_DATA}` 由插件宿主展开，不能直接作为普通终端路径使用。按标准清单启动时，虚拟环境保存在插件数据目录，源码目录无须可写。

## 权限配置

优先级：**启动参数 > 进程环境变量 > 外部 .env > 内置默认值**。进程启动时确定能力，修改配置后须重启 MCP 服务。没有任何工具调用参数能临时开启修改。

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `FEISHU_ALLOW_READ` | `true` | 表格、消息、群与机器人信息读取 |
| `FEISHU_ALLOW_WRITE` | `false` | 发送、追加、覆盖、新建、重命名、删除 |
| `FEISHU_ENV_FILE` | 未设置 | 外部配置文件；默认 `~/.config/feishu-bot/.env` |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 空 | 飞书自建应用凭据，自动获取并缓存租户 token |
| `FEISHU_TENANT_ACCESS_TOKEN` | 空 | 可选预发放 token，优先于应用凭据，不自动续期 |
| `FEISHU_WEBHOOK_URL` / `FEISHU_WEBHOOK_SECRET` | 空 | 可选自定义群机器人地址和签名密钥 |
| `FEISHU_API_BASE_URL` | `https://open.feishu.cn` | 也支持 `https://open.larksuite.com` |
| `FEISHU_TIMEOUT_SECONDS` | `30` | 单次 HTTP 超时，范围 1–120 秒 |

布尔值接受 true/false、1/0、yes/no、on/off；非法值直接停止启动。指定的配置文件不存在也会停止。插件不会自动读取当前工作目录、项目目录或上级目录的 `.env`。

```powershell
# 默认只读；启动后等待 MCP 客户端通过 stdin/stdout 通信，不是聊天窗口。
uv run --frozen --no-dev feishu-bot

# 启动参数开启修改，即使环境变量为 false 也生效。
uv run --frozen --no-dev feishu-bot --allow-write

# 参数强制关闭修改，即使环境变量为 true 也关闭。
uv run --frozen --no-dev feishu-bot --no-allow-write

# 仅写：独立发送、写入仍可用；需要先读取的自动路由工具不注册。
uv run --frozen --no-dev feishu-bot --no-allow-read --allow-write

# 全部关闭：仅保留不联网的 feishu_status。
uv run --frozen --no-dev feishu-bot --no-allow-read --no-allow-write

# 指定配置文件并检查最终配置。
uv run --frozen --no-dev feishu-bot --env-file "D:/private/feishu.env" --allow-write --print-config
```

要让插件宿主使用启动参数，在对应 MCP 配置的 `args` 数组末尾追加 `"--allow-write"`；完整示例见 [写入配置示例](docs/mcp-write-example.json)。新客户端读取 `mcp.json`，兼容客户端读取 `.mcp.json`。包中的默认配置都没有开启写入。

部分 MCP 宿主会过滤继承的环境变量，建议凭据与长期能力配置使用默认外部 `.env`；非默认配置位置可通过 `args` 中的 `--env-file` 明确传入。不要把密钥写进可分享的清单。飞书控制台的权限仍必须单独配置，插件开关不能越过飞书自身授权。

## 飞书应用配置

应用机器人和自定义群机器人是两类身份：**应用凭据用于消息与文档 API；群 webhook 只能向配置的群发消息**。使用 webhook 不能读取表格或群历史。

在飞书开放平台创建、发布企业自建应用，按需启用机器人能力，申请相应 API 权限，并把目标文档授权给应用；发群消息还需机器人加入该群。应用获得 API 权限不等于拥有某一份文档的访问权。

常用权限入口：电子表格只读 `sheets:spreadsheet:read` / `sheets:spreadsheet:readonly`，修改 `sheets:spreadsheet`；多维表格按具体表与记录 API 配置读取/新增/更新/删除权限；机器人发送通常使用 `im:message:send_as_bot`，读取群信息及消息需对应读取权限。**具体接口页显示的适用权限和控制台授权为准**，不同接口不保证共享同一条权限。参见 [官方 API 对照](docs/protocol-and-api.md)。

## 工具清单

默认提供 14 个工具：13 个远程只读工具和 1 个本地状态工具；同时开启读写时共 28 个。

| 能力 | 工具（均加 `feishu_` 前缀） |
| --- | --- |
| 本地状态 | `status` |
| 机器人和群 | `bot_info`、`chats_list`、`chat_get` |
| 消息读取 | `messages_list`、`message_get` |
| 电子表格读取 | `spreadsheet_get`、`sheets_list`、`sheet_read` |
| 多维表格读取 | `bitable_tables_list`、`bitable_fields_list`、`bitable_records_search`、`bitable_record_get` |
| 路由预览 | `sheet_route_plan` |
| 消息修改 | `message_send`、`message_reply`、`message_recall`、`webhook_send` |
| 电子表格修改 | `sheet_create`、`sheet_rename`、`sheet_delete`、`sheet_write`、`sheet_append`、`sheet_add_dimension` |
| 多维表格修改 | `bitable_records_create`、`bitable_records_update`、`bitable_records_delete` |
| 路由执行 | `sheet_route_append`，必须同时开启读写 |

支持文本、富文本、卡片等消息内容；图片、文件等消息使用已经存在的飞书资源 key。本版不提供文件上传、知识库节点解析、文档正文编辑、事件回调或消息长连接监听。它是客户端按需调用的 MCP 服务，不是常驻群聊应答进程。

分页接口完整保留 `has_more`、`page_token`。多维表格的 `records/search` 虽然使用 HTTP POST，仍按只读操作处理。开关同时作用于工具注册和 HTTP 操作层，不能靠猜测工具名绕过。

## 按账号分表追加

普通工具使用飞书标准参数；下面是从参考工作流中提取的通用功能示例。传入 `feishu_sheet_route_plan` 或授权后的 `feishu_sheet_route_append`：

```json
{
  "spreadsheet_token": "YOUR_SPREADSHEET_TOKEN",
  "records": [
    {"__account_id": "000123", "__account_name": "示例店", "日期": "2026-09-16", "消耗": 35.6},
    {"__account_id": "000456", "__account_name": "第二家店", "日期": "2026-09-16", "消耗": 18.2}
  ],
  "column_order": ["日期", "消耗"],
  "start_column": "A",
  "start_row": 1
}
```

账号 ID 必须为字符串；字段名可以通过 `account_id_field` / `account_name_field` 更换。插件按完整 ID 匹配已有页签，`123` 不会误命中 `1234`；同一 ID 命中多个页签会在写入前停止。缺失时新建 `账号名(账号ID)`，根据实际容量扩容，按指定列顺序追加，不写表头、不自动做业务去重。

首个写入字段不能为空。飞书 append 从范围首列的第一个空白位置开始追加；固定使用 `INSERT_ROWS`，避免因空白行不够覆盖后续数据。不是“寻找任意列的最后一条业务记录”。如果已有表头，通常把 start_row 设为 2。

## 失败处理

读取遇到网络错误、HTTP 429/5xx 可有限重试；租户 token 只在内存缓存。**写入不自动重试**。消息发送的 `request_uuid` 可以由调用者提供；同一业务重试保留 UUID，飞书消息去重窗口是一小时。

HTTP 超时或无法确认结果时返回 `outcome=unknown`，应先读取核实。按账号写入跨多个请求，没有远程事务：返回 `completed`、`failed`（含阶段和已发生副作用）、`pending_account_ids`。不要把已完成部分再次发送。一个进程内的路由调用串行处理；多个进程或其他用户同时写同一页签时，应由调用方协调。

错误包含飞书 code、HTTP 状态和请求 ID，已知凭据会被脱敏；不记录原始请求数据和密钥。业务返回的消息/单元格仍可能含敏感内容，分享结果前按实际需要选择范围。

## 开发与验证

```powershell
uv sync --frozen
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts
uv run --frozen python scripts/build_bundle.py
```

测试使用模拟 HTTP 检查消息和表格写入，不会发送真实消息或修改远程数据。另有真实 stdio 握手、参数优先级、缺省只读、跨目录复制后用标准清单启动的测试。验证记录见 [verification.md](docs/verification.md)。打包器只打包明确允许的源码与文档，排除密钥文件、虚拟环境和缓存。

本插件按 [OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins) 和 [Agent Plugins MCP 规范](https://agent-plugins.org/plugin-authors/mcp-servers) 制作；飞书接口来源及与参考工作流的差异见 [协议与 API 对照](docs/protocol-and-api.md)。这是本地自建插件，不代表 OpenAI 或飞书官方发布或审核。
