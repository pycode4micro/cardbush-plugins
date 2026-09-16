# 协议与 API 对照

核对日期：2026-09-16。以下是本插件的实现依据，不是复制的官方文档。接口仍受飞书控制台的应用权限、数据权限、群成员关系及限流规则约束。

## OpenAI 与 MCP

- [OpenAI：Package your plugin](https://developers.openai.com/plugins/build/plugins)：新版根目录 `plugin.json`、固定发现的 `mcp.json` / `skills/`；OpenAI 展示字段位于 `extensions.com.openai`；`.codex-plugin/plugin.json` 是兼容入口。
- [Agent Plugins：MCP servers](https://agent-plugins.org/plugin-authors/mcp-servers)：stdio server、`type`、`command`、`args`、`env`、`cwd`，以及宿主展开的 `${PLUGIN_ROOT}` / `${PLUGIN_DATA}`。
- [plugin.json schema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json)、[mcp.json schema](https://agent-plugins.org/schemas/1.0.0/mcp.schema.json)：交付前使用 JSON Schema 校验。
- [MCP 服务端开发](https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server)：采用官方 Python `mcp` 2.2.0 的 `MCPServer`，通过 stdio JSON-RPC 与客户端通信，日志使用 stderr。

两份插件清单共享名称、版本、展示信息；两份 MCP 配置均启动同一个 Python 包。便携清单只包含该 schema 支持的字段；兼容清单额外提供环境变量透传和超时设置。开关没有写死在清单中，默认来自 Settings。

## 飞书 API

| 操作 | HTTP 接口 | 依据 |
| --- | --- | --- |
| 自建应用租户 token | POST `/auth/v3/tenant_access_token/internal` | [官方鉴权文档](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal) |
| 发送消息 | POST `/im/v1/messages` | [发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create) |
| 群历史消息 | GET `/im/v1/messages` | [历史消息](https://open.feishu.cn/document/server-docs/im-v1/message/list) |
| 机器人 / 群 / 单条消息 / 回复 / 撤回 | `/bot/v3/info`、`/im/v1/chats`、`/im/v1/messages/{id}`、`.../reply` | [飞书官方 Python SDK](https://github.com/larksuite/oapi-sdk-python/tree/v2_main/lark_oapi/api/im/v1/model)；机器人信息已做真实只读验证 |
| 列电子表格页签 | GET `/sheets/v3/spreadsheets/{token}/sheets/query` | [获取工作表](https://open.feishu.cn/document/server-docs/docs/sheets-v3/spreadsheet-sheet/query) |
| 读取单元格 | GET `/sheets/v2/spreadsheets/{token}/values/{range}` | [读取单个范围](https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/reading-a-single-range) |
| 覆盖单元格 | PUT `/sheets/v2/spreadsheets/{token}/values` | [写入单个范围](https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/write-data-to-a-single-range) |
| 追加单元格 | POST `/sheets/v2/spreadsheets/{token}/values_append` | [追加数据](https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/append-data) |
| 增加空白行列 | POST `/sheets/v2/spreadsheets/{token}/dimension_range` | [增加行列](https://open.feishu.cn/document/server-docs/docs/sheets-v3/sheet-rowcol/add-rows-or-columns) |
| 新建 / 改名 / 删除页签 | POST `/sheets/v2/spreadsheets/{token}/sheets_batch_update`，分别使用 addSheet/updateSheet/deleteSheet | 原参考客户端使用的 v2 协议，按请求与回包结构模拟验证；未做真实远程修改 |
| 多维表格筛选 | POST `/bitable/v1/apps/{app}/tables/{table}/records/search` | [查询记录](https://open.feishu.cn/document/docs/bitable-v1/app-table-record/search) |
| 多维表格表、字段、记录和批量修改 | `/bitable/v1/apps/{app}/tables/...` | [飞书官方 SDK 请求模型](https://github.com/larksuite/oapi-sdk-python/tree/v2_main/lark_oapi/api/bitable/v1/model) |
| 自定义机器人 webhook | POST `/bot/v2/hook/{id}` | [自定义机器人指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot) |

上表接口均以 `/open-apis` 为前缀。主机固定为飞书或 Lark 官方 HTTPS 域名，禁止跟随重定向携带凭据访问其他主机。

关键差异：

1. 多维表格搜索使用 POST，但不修改数据，按只读能力处理；鉴权请求只在已授权能力调用内发生。
2. 应用机器人发消息的 `content` 是 JSON 字符串；工具接收对象，由客户端序列化。自定义机器人 webhook 的 `content` / `card` 保持对象。
3. webhook 签名使用 `timestamp + "\n" + secret` 作为 HMAC-SHA256 的 key，消息为空，再做 Base64；不是直接对请求体签名。
4. 表格追加明确使用 `INSERT_ROWS`；匹配页签按完整账号 ID，拒绝歧义，不引入业务平台执行器。
5. 接口权限与插件能力开关是两层限制。开关只决定插件可发出的操作，不改变飞书应用自身授权。

## 从参考工作流提取的范围

保留：应用身份鉴权、显式列顺序、账号分组、匹配/新建页签、容量检查和追加。通用输入参数替代工作流变量、数据源和模板。

未引入：数据库、工作流编排器、调度器、业务 SQL、千川账号配置、原工作流密钥或全局插件注册代码。电子表格、群消息、多维表格操作也可单独调用。
