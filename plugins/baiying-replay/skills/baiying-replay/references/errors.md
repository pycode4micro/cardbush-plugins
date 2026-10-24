# 错误诊断与恢复（0.2.3+）

`isError=true` 仍表示调用失败；成功接口、素材任务 ID 和原有状态不变。工具错误通过 `structuredContent.error` 返回，`content[0].text` 是同样的 JSON。素材任务失败读 `last_error`；逐文件下载与智能切片提交异常读该项的 `error_detail`。

- `category`：具体原因类别；`message` 是可展示的说明。
- `stage`：工具/后台任务阶段；`request_stage` 在能确定时提供具体百应接口阶段。可能为空，不要猜测。
- `http_status`：真实 HTTP 状态；`provider_code`：JSON 业务错误码，两者分别记录，不能把业务码当 HTTP 状态。
- `diagnostic_id`：本次本地诊断编号；`provider_request_id` 仅在响应携带时提供。
- `transient`：原因是否可能短暂；`retryable`：是否适合重试当前只读调用。`transient=true` 不代表可以重复提交写操作。
- `next_action`：下一步建议。`diagnostic_saved=true` 时可读 `diagnostic_file`；false 表示写日志失败，原错误仍有效。

| 类别 | 处理 |
| --- | --- |
| `timeout` / `connection_error` / `provider_unavailable` | 保留原账号、商品、任务；检查网络并稍后做有限次数的同一只读查询。回放扫描内部已有最多 3 次尝试，仍失败应报告诊断编号，不无限重试。 |
| `rate_limited` | 延后查询，减少频率；不要通过更换手机号或任务绕开限流。 |
| `authentication_required` | 先查登录状态，确实失效再按原短信流程恢复，不自动反复发送验证码。 |
| `permission_denied` | 核对所选子账号归属及权限；不要当作查询结果为空。 |
| `invalid_response` / `provider_rejected` / `internal_error` | 保留诊断编号、阶段、业务码；检查插件/接口，不盲目重新登录或改搜索词。未知业务码不会自动判为权限问题。 |
| `storage_busy` / `storage_error` | 检查本地数据库占用、路径、磁盘或 NAS。不要删除登录缓存或现有任务来“修复”。 |
| `invalid_arguments` / `tls_error` / `media_error` | 按具体提示修正参数、环境或素材，避免原样重试。 |

智能切片提交超时可能已经被百应受理：继续 `baiying_check_clips` 查询原 `plan_id`，不能换计划重发。素材准备失败继续原 `job_id`，经修正后用 `baiying_resume_materials`；失败下载可设 `retry_failed=true`，成功素材保留。

账号范围错误时先核对当前任务的手机号。省略 `mobile` 会读取插件共享的“上次使用的手机号”，不保证与当前聊天相同；知道目标手机号就显式传入，任务恢复也使用同一号码，不修改其他任务的默认配置来绕过范围校验。

诊断文件在 `BAIYING_DATA_DIR/diagnostics/`（未配置时 Windows 为 `%LOCALAPPDATA%/baiying-replay/diagnostics/`）。只包含分类、状态码、阶段、时间、诊断编号及无源码/局部变量的堆栈位置，不记录请求参数、原始响应、Cookie、短信验证码、SSO ticket 或签名媒体地址。不要要求用户粘贴原始 Cookie 或整份登录响应。
