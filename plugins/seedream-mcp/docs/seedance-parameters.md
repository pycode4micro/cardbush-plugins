# Seedance 原生参数与能力边界

核对日期：2026-09-11。依据：[创建任务 API](https://www.volcengine.com/docs/82379/1520757)、[2.5 教程及四模型差异](https://www.volcengine.com/docs/82379/2607688)、[查询任务 API](https://www.volcengine.com/docs/82379/1521309)。模型开通、配额、素材权限、安全政策仍由服务端决定。

## 传输与默认行为

- 共用 `ARK_API_KEY`，POST `/api/v3/contents/generations/tasks`，GET 同路径加 `/{id}`。
- 标准 MCP stdio；可选仅回环的 streamable HTTP。没有浏览器自动化或本地视频工厂依赖。
- `request` 对应原生 API JSON。`local` 只控制本地适配行为，绝不传给 Ark。
- 除显式便捷模型别名和经用户启用的本地图片/音频编码外，不改写参数。None/未提供字段不传，false/0保留。
- 不自动变速、拼接、剪辑、遮罩、去字幕、裁切、静音、补音、音色转换或降级模型。
- create 只提交一次；网络/服务异常可能已创建任务，提示检查 Ark 历史，不自动重试收费请求。
- 成功查询返回原生任务字段和临时 URL，不自动下载。本版本不提供任务列表、取消/删除、资产上传或 TOS 管理工具。

## 全部可选参数

| 原生字段 | 类型/取值 | 限制 |
| --- | --- | --- |
| `model` | 字符串 | 四个已核对 ID/别名；ep-* 与未来 ID 必须声明 local.capability_profile |
| `content` | 有序数组 | text、image_url、video_url、audio_url；媒体顺序不变 |
| `resolution` | 480p/720p/1080p/4k | 2.0标准到4k；fast/mini到720p；2.5到1080p |
| `ratio` | 16:9/4:3/1:1/3:4/9:16/21:9/adaptive | 2.5首尾帧、edit、extend只能adaptive或不填 |
| `duration` | 整数 | -1或4..15（2.0系列）/4..30（2.5）；2.5 edit必须-1或不填 |
| `generate_audio` | 布尔 | 四款都支持；需要声音时明确true；与输入参考音频是否静音是两回事 |
| `watermark` | 布尔 | 四款都支持 |
| `return_last_frame` | 布尔 | 四款都支持，查询返回last_frame_url |
| `omni_reference_task_type` | auto/reference/edit/extend | 仅2.5全模态参考模式 |
| `output_format` | mp4/mov | 仅2.5；2.0系列连mp4也应省略此字段 |
| `callback_url` | HTTP(S) URL | 原生状态回调；本插件不是回调接收服务 |
| `execution_expires_after` | 整数3600..259200 | 秒；任务排队/执行过期时间，不是视频秒数或HTTP超时 |
| `priority` | 整数0..9 | 同接入点队列优先级 |
| `safety_identifier` | ASCII字符串≤64 | 终端用户标识，不填个人敏感数据 |
| `tools` | `[{"type":"web_search"}]` | 原生联网搜索 |

`extra_body` 是本地扩展容器，不作为同名字段传给 Ark：仅显式 `local.allow_unverified_parameters=true` 时允许透传未来新增字段。不能覆盖上述字段、鉴权和接口地址，不能绕过已知不支持的 `seed/frames/camera_fixed/draft/draft_task/service_tier`。这四个模型不支持旧模型的 draft 和 flex 离线接口能力；不能拿全系列 SDK 参数并集充当各款支持列表。

## 素材模式与限额

| 条件 | 2.0/fast/mini | 2.5 |
| --- | --- | --- |
| 参考图最多 | 9 | 30 |
| 参考视频最多 | 3 | 10 |
| 参考音频最多 | 3 | 10 |
| 所有参考素材合计最多 | 15 | 50 |
| 参考视频各条与合计上限 | 15秒 | 30秒 |
| 参考音频各条与合计上限 | 15秒 | 30秒 |
| 仅音频参考（没有图/视频） | 不支持 | 支持 |
| 显式edit/extend字段 | 不支持；在提示词表达 | 支持；仍结合提示词判定 |

首帧、首尾帧、全模态参考是互斥场景。单首帧用1张图，role为first_frame或不填；首尾帧用2张图，两个role必须明确为first_frame/last_frame；全模态图必须role=reference_image，视频role=reference_video，音频role=reference_audio。不能混用首帧图与参考音频/视频；如需这种组合，使用reference_image并在提示词说明首帧意图，不能假装等同严格首帧约束。

素材规格：

- 图：每边300..6000像素，宽高比0.4..2.5，单图小于30MB。原生支持jpeg/png/webp/bmp/tiff/gif/heic/heif；本地校验支持前6种。
- 视频：mp4/mov，24..60fps，每边300..6000，宽高比0.4..2.5，总像素407696..8295044，≤200MB。每条至少2秒；2.5 edit每条至少4秒。
- 音频：mp3/wav，≤15MB，每条至少2秒。内联/本地文件读取元信息，校验单条和已知总时长；URL/asset不下载，服务端校验剩余元信息与总时长。
- API请求体≤64MB；本地编码按UTF-8 JSON字节数验证。不要把大视频编码进请求。
- `seedance_preview_request` 的“通过”不代表服务端素材校验通过，也不验证是否有权使用人物/音色资源。

## 原生调用示例

文生视频：

```json
{"request":{"model":"2.0-fast","content":[{"type":"text","text":"雨后街道的咖啡店，镜头缓慢前移，有环境声。"}],"resolution":"720p","duration":6,"generate_audio":true}}
```

2.5 视频编辑（输入视频须4..30秒）：

```json
{"request":{"model":"2.5","content":[{"type":"text","text":"编辑视频1，仅把外套颜色改成灰蓝色，其余动作、场景和节奏不变。"},{"type":"video_url","video_url":{"url":"asset://YOUR_VIDEO_ASSET_ID"},"role":"reference_video"}],"omni_reference_task_type":"edit","ratio":"adaptive","duration":-1,"output_format":"mp4","generate_audio":true}}
```

2.5 延长：将上述任务类型改为extend，提示词说明向前/向后延长什么，duration使用允许值，ratio保持adaptive。不要用这个示例推断其他模型支持同名参数。

本地音色文件：

```json
{"request":{"model":"2.5","content":[{"type":"text","text":"主角声音只使用音频1的音色，以自然语气说：你好，欢迎来到我的店。"},{"type":"audio_url","audio_url":{"url":"C:/absolute/path/voice.wav"},"role":"reference_audio"}],"duration":4,"generate_audio":true},"local":{"allow_local_files":true}}
```

查询已提交任务：

```json
{"task_id":"cgt-实际任务ID"}
```

工具返回的task.status为succeeded时查看task.content.video_url。出现failed/expired不要当成完成成片；保留任务ID和脱敏错误码用于诊断。2.5仍会根据提示词异步判断类型，即使显式指定也可能返回TaskTypeMismatch；不擅自重新生成来修复。
