# 火山 MediaKit 精细化字幕擦除

`volcengine-plugins` 0.5.0 新增独立去硬字幕工具，调用火山官方
`POST /api/v1/tools/erase-video-subtitle-pro`。不请求翻译、配音、重新生成视频、增强、裁切或静音。

默认显式提交 `model_version=v5`、`mode=Subtitle`、`output_encode_mode=Quality`。
官方接口在省略版本时默认 v4；插件主动选择 v5，并在预览中展示，用户也可显式选择 v4。
官方说明 v5 优化 AIGC 视频修复闪烁、带阴影字幕、误擦和速度；具体素材的修复效果仍需检查，不能保证完全无痕或恢复从未露出的真实细节。

## 配置和迁移

复用 `MEDIAKIT_API_KEY`、`MEDIAKIT_BASE_URL`、`MEDIAKIT_TIMEOUT_SECONDS`；不会回退到 `ARK_API_KEY`。
Windows 当前用户环境变量的读取与原插件一致，进程变量（包括空值）优先。

插件原名 `seedream-mcp`，现在目录、清单和包名为 `volcengine-plugins`，MCP 服务键为 `volcengine`，
启动方式为 `python -m volcengine_plugins`。旧的生图、视频和增强工具名称保持不变。
更新后执行 `python -m pip install .` 并重新加载新插件，停用旧插件连接，避免两套工具同时启用。
不需要重新配置既有环境变量。

## 完整调用流程

1. `video_subtitle_erase_capabilities`：免费离线读取 schema、限制和脱敏配置状态。
2. 如输入为本地视频，调用 `video_subtitle_erase_upload`，例如 `{"file_path":"C:/videos/input.mp4"}`。
   只传输该文件的原始字节，返回 `mediakit://` 地址，不创建付费擦除任务；存储/传输可能另行计费。
3. `video_subtitle_erase_preview_request`：免费校验参数并显示脱敏后的原生请求。
4. 用户已授权去字幕后，调用一次 `video_subtitle_erase_create_task`，保存 `task.task_id`。
5. 用 `video_subtitle_erase_get_task` 查询同一 ID，直到 `task.status` 为 `completed` 或 `failed`。
   `task.success=true` 仅表示查询成功，不代表视频已处理完成。不要重复 create 来轮询。
6. 完成后，`task.result.video_url` 是成片地址；使用 `video_subtitle_erase_download_task` 保存原始结果字节：
   `{"task_id":"amk-tool-erase-video-subtitle-pro-EXAMPLE","dest":"C:/videos/clean.mp4"}`。
   目标必须为不存在的绝对路径，不覆盖、不转码。仅下载 GET 允许有限重试，不重新创建擦除任务。

预览与提交使用同一请求结构：

```json
{
  "request": {
    "video_url": "mediakit://YOUR_FILE_ID",
    "model_version": "v5",
    "mode": "Subtitle",
    "output_encode_mode": "Quality",
    "client_token": "subtitle-erasure-request-001"
  }
}
```

每个新的逻辑任务使用不同的 `client_token`。提交超时或返回缺少 task_id 时，保留原 token、先核实服务端状态；插件不会自动重试、换模型或降为标准版。

## 擦除位置和字幕保护

`Subtitle` 模式仅识别**画面下方 50%** 的字幕，还会使用文字高度和居中程度过滤。
自定义框与画面下半部取交集，框选画面上半部不会突破这个限制。

如确实要去除位于画面上部或偏侧的字幕，可在用户明确授权后使用 `mode=Text` 并限定字幕框。
`Text` 也会识别人名、地名等其他渲染文字，不能自动把仅去字幕的任务扩大成全屏擦字。
完整包含文字描边和阴影，勿截断文字。

```json
{
  "request": {
    "video_url": "https://example.com/source.mp4",
    "mode": "Text",
    "erase_ratio_location": [{
      "top_left_x": 0.1,
      "top_left_y": 0.05,
      "bottom_right_x": 0.9,
      "bottom_right_y": 0.2
    }]
  }
}
```

坐标归一化为 0–1，左上角为原点，矩形宽高必须为正。最多 20 个全局框。
`subtitle_filter` 仅用于 `Subtitle` 模式：

| 参数 | 官方默认值 |
| --- | --- |
| `min_text_height_ratio` | 0.01 |
| `max_text_height_ratio` | v5 为 0.1，v4 为 0.2 |
| `center_offset_ratio` | 0.08 |

## 只擦指定时间段

`time_segment_filter.mode=selected` 只处理指定时间段，`skip` 跳过指定时间段。
时间单位为源视频秒数，起点不小于 0，终点大于起点。它控制擦除范围，**不截短输出视频**。

```json
{
  "request": {
    "video_url": "mediakit://YOUR_FILE_ID",
    "time_segment_filter": {
      "mode": "selected",
      "segments": [
        {"start_time": 5, "end_time": 20},
        {"start_time": 25, "end_time": 40}
      ]
    }
  }
}
```

`selected` 还支持每段的 `erase_ratio_location`，每段最多 20 个框；`[]` 或省略表示该段按默认范围处理。
**全局框和分段框互斥**。若所有时间段使用同一范围，可用全局框加 selected 时间段列表。
`skip` 不支持分段框。不排序、合并或改写用户提供的时间段，实际视频时长由服务端校验。

## 限制与产物

- 输入支持 HTTP(S)、`mediakit://`、`vod://`、`tos://`。本地文件应先调用上传工具，不接受视频 base64。
- 官方输入最高 **2K**，输出最高 **1080p**。高于 1080p 的素材不能通过此服务保持原始输出分辨率。
- `Quality` 优先画质，输出文件可能增大；可显式选择 `Size` 优先文件大小。没有自定义 fps、音频、时长或翻译参数。
- 时间和空间范围控制仅影响擦除，插件不会主动裁剪、缩放、静音或改动音轨。最终编码、音轨和画面一致性以服务端产物为准。
- 未设置 `media_output_destination` 时，返回通常 24 小时有效的 HTTPS 结果链接，任务可查询最近 30 天。
- 设置 `media_output_destination=vod://空间` 或 `tos://桶` 需要预先授权服务写入。返回对应存储 URI，下载工具不把它冒充 HTTPS；应使用已授权的存储服务获取。
- `callback_url` 允许 HTTP(S)，`callback_args` 最多 512 UTF-8 字节，`client_token` 为 1–64 个可打印 ASCII 字符，`queue_id` 按原生字段传递。
- 预览隐藏来源、回调地址、回调内容、输出位置、队列和幂等 token；不把密钥放进工具参数。

## 验证与官方依据

离线测试覆盖请求映射、默认值、区域/时间段规则、敏感字段脱敏、付费请求不重试、独立鉴权、异步状态、原样下载及 MCP stdio。
模拟 HTTP 测试不代表真实付费接口或特定素材修复质量已经验证。

核对日期：2026-09-18。

- [精细化字幕擦除 API](https://www.volcengine.com/docs/6448/2372084)
- [字幕擦除使用指南](https://www.volcengine.com/docs/6448/2371372)
- [上传接口](https://www.volcengine.com/docs/6448/2536891)
- [多源输入](https://www.volcengine.com/docs/6448/2536893)
- [异步任务查询](https://www.volcengine.com/docs/6448/2278532)
- [鉴权与幂等](https://www.volcengine.com/docs/6448/2300661)
