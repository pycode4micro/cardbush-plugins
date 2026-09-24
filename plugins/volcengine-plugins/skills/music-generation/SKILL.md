---
name: music-generation
description: 使用火山引擎 v5.0 生成带歌词人声歌曲或纯音乐 BGM，查询异步任务并下载音频、歌词和字幕元数据。适用于主题曲、原创歌曲和视频配乐；不用于语音朗读、翻唱或声音克隆。
---

# 火山音乐 v5.0

先用 `music_capabilities` 读取当前契约与凭据状态。该服务使用音乐 AK/SK，Ark 与 MediaKit 的 API Key 不能替代；不要读取其他项目的密钥或把密钥传进工具参数。

- 带歌词歌曲使用 `music_preview_song` → `music_create_song`，传入原生 `Lyrics`、`Prompt`、`ModelVersion="v5.0"`。用户提供的歌词原样保留；需要创作歌词时由助手按当前请求撰写，可用 `[verse]`、`[chorus]`、`[bridge]` 等结构标签。用户已要求生成时无需重复询问同一授权；只有缺失信息会改变成品目标时再澄清。
- `v5.0` 不使用旧版 `Genre/Mood/Gender/Timbre/Instrument` 等独立字段。将所需风格写入 `Prompt`，不要暗中切回旧模型。歌曲允许同时传歌词和描述，时长 30～240 秒，语言为普通话、英文或粤语。
- 纯音乐使用 `music_preview_bgm` → `music_create_bgm`，用中文 `Text` 描述曲风、心情、场景、乐器。请求体版本字段为 `Version="v5.0"`，时长 30～120 秒。明确分段需要时使用 `Segments`；总时长优先于描述里的时长，再优先于外层 `Duration`。避免三处相互矛盾。`EnableInputRewrite` 默认关闭。
- 预览是本地免费操作。`billing_mode` 默认 `postpaid`；用户明确使用资源包时选 `prepaid`。资源包与后付费不互相抵扣，权限/余额错误时不要改计费方式重试。价格预览只引用公开 standard/BGM 价目，不代表已确认的 v5.0 报价。

提交后保存 `task.id`，优先用 `generation_wait_tasks`（`kind=music`）在插件内等待同一 ID；宿主支持后台只读工具时交给它等待并继续独立工作，续等只匹配 `structuredContent.status=timeout`，设有限预算。`ready` 后逐项核对结果，已结束 ID 不再加入后续等待；旧版才用 `music_get_task`。接口调用成功不等于歌曲完成；只有 `status="succeeded"` 才下载。尊重返回的建议查询间隔，缺少进度不猜测完成时间。超时或响应丢失可能已创建任务，不自动重提或降级；拿不到 ID 时报告不确定结果和请求编号，先检查控制台。

用 `music_download_task` 保存到新的绝对路径；可同时指定新的 `metadata_dest` JSON 路径保存歌词、原生 Captions 和 StyleInfo。直接传任务 ID，由工具读取完整签名地址。不要抄写、重排或截断签名 URL。

检查返回的真实文件格式。官方链路可能把请求 WAV 的结果转成 MP4；下载工具保留原字节并提示扩展名不匹配。若交付要求特定格式，再单独进行音频转换，保留下载原件。听检歌词漏唱/重复、发音、段落收尾和音频杂音；未听检时说明验证范围。只把服务实际返回的时间戳用于后续字幕，不猜测 Captions 格式或编造 LRC/SRT。

完整参数、原生 Action、配置与示例见 [音乐接口说明](../../docs/music-generation.md)。独立 `GenLyrics` 官方目前仅列出 v4.0，本技能不调用该旧模型。
