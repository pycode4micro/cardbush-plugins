# 火山 MediaKit 视频超分

仅提供 **standard（标准版）** 和 **generative（大模型版）**。不提供极速版、专业版，不允许用扩展参数绕过版本限制。它是原生异步接口封装，不调用 Seedance 重绘视频。

## 配置

独立配置 `MEDIAKIT_API_KEY`，不要直接复用 `ARK_API_KEY`。可以使用开通 MediaKit 权限的 IAM 通用 API Key 或 MediaKit 专用 API Key。插件不从聊天、项目 `.env`、代码读取密钥，不把临时测试密钥保存到插件。

- `MEDIAKIT_BASE_URL`：默认 `https://mediakit.cn-beijing.volces.com`，仅管理员环境配置，不是工具参数。
- `MEDIAKIT_TIMEOUT_SECONDS`：单次 HTTP 超时，默认120秒，可选1–300秒，不是任务完成时限。
- 用户变量继承规则和现有 Ark 配置相同：进程（包括显式空值）优先，其次 Windows 当前用户变量。`ARK_READ_USER_ENV=0` 同时关闭所有插件用户变量兜底。

## 调用顺序

1. `video_enhance_capabilities`：免费离线 schema、限制、配置状态。
2. 本地文件先调用 `video_enhance_upload(file_path="C:/absolute/video.mp4")`。这是明确授权的外部上传，不是增强生成。返回 `video_url="mediakit://..."`，原文件不裁切、不缩放、不静音。包装层接受列出的常见视频后缀、非空、最大10 GiB，媒体实际格式/尺寸由服务端验证。不要上传凭证或无关文件。
3. `video_enhance_preview_request`：免费离线验证和脱敏预览，不读取/上传媒体。
4. 经用户授权后 `video_enhance_create_task`：付费提交一次，返回 `task.task_id`。
5. `video_enhance_get_task(task_id=...)`：查询同一任务，状态为 `running` / `completed` / `failed`。完成后返回 `task.result.video_url`，不自动下载或转码。

标准版示例（preview 和 create 使用相同参数）：

```json
{"request":{"variant":"standard","video_url":"mediakit://YOUR_FILE_ID","resolution":"1080p","scene":"aigc","enhance_style":"natural","bitrate_level":"high","client_token":"unique-request-token"}}
```

大模型版示例：

```json
{"request":{"variant":"generative","video_url":"mediakit://YOUR_FILE_ID","resolution":"1080p","bitrate_level":"high","client_token":"another-unique-request-token"}}
```

`variant` 只是包装层路由选择；标准版固定向 `/api/v1/tools/enhance-video` 发送 `tool_version=standard`，大模型版使用 `/api/v1/tools/enhance-video-generative`，不传 `tool_version`。其余字段按官方名称透传，无静默改写。

| 项目 | 标准版 | 大模型版 |
|---|---|---|
| 输出 `resolution` | 240p / 360p / 480p / 540p / 720p / 1080p / 2k / 4k / 8k | 720p / 1080p / 2k（默认720p） |
| `resolution_limit` | 128–4320；与 resolution 互斥 | 不支持 |
| `scene` | common / ugc / short_series / aigc / old_film | 不支持 |
| `enhance_style` | hd / natural | 不支持 |
| 输入短边 / 长边 | 360–1440 / 360–2560 | 360–1080 / 360–1920，且仅SDR |

共同字段：`video_url`（HTTP(S)/mediakit/vod/tos）、`bitrate_level`（low/medium/high）、`bitrate`（10–150000 kbps，覆盖 bitrate_level）、`fps`（15–120，不填保留原帧率）、`media_output_destination`（vod/tos，需事先授权）、`client_token`（1–64个可打印ASCII字符）、`callback_args`（最多512 UTF-8字节）、`callback_url`、`queue_id`。无可改时长/台词/字幕/静音/音色的参数。输出像素取整、增强效果和音轨最终以服务端文件为准，插件不声称逐像素保真。

`client_token` 是原生幂等凭证，建议每次逻辑提交明确设置。超时后不自动重试、换模型或新建任务；保存原 token 和已返回的任务 ID，先确认服务端状态。不能通过重复 create 轮询。任务可查询最近30天，结果临时URL通常24小时有效。

工具均在现有 `seedream` MCP 服务内独立命名，客户端可以分别停用；不会删除或替换已有7个生图/生视频工具。新增总计5个工具。

## 验证与依据

封装测试使用 mock HTTP 和真实 MCP stdio 握手，不产生超分费用。此前真实样片已验证两种官方增强服务可用，但不将模拟测试表述为新版封装的真实付费端到端测试。

官方文档（核对2026-09-11）：[标准版接口](https://www.volcengine.com/docs/6448/2279230)、[大模型版接口](https://www.volcengine.com/docs/6448/2464595)、[本地上传](https://www.volcengine.com/docs/6448/2536891)、[任务查询](https://www.volcengine.com/docs/6448/2278532)、[鉴权和幂等](https://www.volcengine.com/docs/6448/2300661)。
