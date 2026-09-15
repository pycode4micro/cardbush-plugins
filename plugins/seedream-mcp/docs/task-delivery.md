# Seedance 任务、预检与下载（0.4.0）

`seedance_create_task` 在 POST 前强制运行与免费 preview 相同的本地校验，
成功返回 `preflight.valid`、脱敏请求和摘要。每个 content 项都必须显式写
`type`，比如 `{"type":"text","text":"海边日落"}`；不会猜测或补写请求。

本地预检拒绝返回 MCP 错误和结构化 `stage=preflight`、
`paid_request_sent=false`、`billing.charged=false`。POST 已发出或响应不明确
时 `charged/refunded=null`，表示没有账单证据，不表示免费或已经退款。
创建不自动重试；应先查看服务端任务历史。

| 工具 | 主要参数与返回 |
| --- | --- |
| `seedance_list_tasks` | page_num/page_size、可选 status/task_ids/model；服务端 items/total 和分页信息 |
| `seedance_get_tasks` | 1..100 个 task_ids，去重、最多 4 个并发 GET；按项返回 ok，保留部分成功 |
| `seedance_download_task` | task_id、绝对 dest，可选 output=video/last_frame；返回路径、bytes、sha256 |

列表的 model 是服务端模型/推理接入点筛选，不改任务模型。进度和 ETA 仅原生
提供才有值，缺失返回 null。elapsed_seconds 是创建到当前的时间，不是完成预测。
批量查询只执行一轮，不在工具内持续等待或重新生成。

下载先查询同一个成功任务，原样使用 video_url/last_frame_url，包括 `%2F`、
`+`、参数顺序及重复 query 参数，不手工转录到脚本。到 CDN 的请求不带 Ark
Authorization。支持 HTTPS 重定向、临时文件、SHA256、文件大小上限和有限 GET
重试（默认 2 次）；不覆盖现有目标、不修改视频、不执行返回内容。
401/403/404/410 会明确报告链接可能失效，不触发新任务。查询可能仍返回失效
链接，不能保证过期资产可恢复。下载字节完整不等于媒体解码和声音质量合格。

接口依据[官方任务列表 SDK](https://github.com/volcengine/volcengine-go-sdk/blob/master/service/arkruntime/content_generation.go)
及[官方任务字段](https://github.com/volcengine/volcengine-go-sdk/blob/master/service/arkruntime/model/content_generation.go)。

离线验证：在插件目录执行 `python -m pytest -q`，224 项通过（2026-09-15）。
包含真实 MCP stdio 握手、缺 type 的预检错误、签名 query 原样传输、无密钥
泄露到 CDN、下载重试/过期/过大/重定向、不覆盖、分页/批量部分失败以及付费
状态未知的表达。HTTP 均为模拟响应；没有使用真实密钥、创建付费任务或验证
当前账户的模型权限。服务端 ETA/退款和生成质量无法由离线测试保证。
