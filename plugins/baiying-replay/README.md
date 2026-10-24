# 巨量百应商品切片 0.3.0

按手机号、子账号和商品查询链接，或为后续剪辑准备经过验证的素材包。独立运行，不需要数据平台服务或 PostgreSQL。

## 使用示例

- “为兆君官方旗舰店的 ZSU1226 准备 5 条素材，至少 90 秒，下载后给剪辑用。”
- “不足时可以用同商品原回放，先不要继续百应智能切片。”
- “刚才的任务允许继续百应智能切片，最多两轮。”
- “继续刚才的素材任务，只重试失败下载。”
- “只给我这个商品的视频链接。”

prepare_materials 启动后台任务并立即返回 job_id；material_status 查询、resume_materials 恢复或调整策略、cancel_materials 停止、list_material_jobs 找回记录。工具完整名称均带 baiying_ 前缀，原有 10 个基础工具保留。

新任务默认 source_policy=original_only，仅下载同商品原始回放，已有相关切片也保留原片，不自动智能剪辑。original_first 允许原片不足补成品；smart_clip_first 显式使用旧成品优先路线；旧任务恢复保持原策略。原片新任务默认只取 1 段；常见商品原回放约 10 分钟，先在这一段完成 ASR 与选材，只有明确要求补料才扩大素材池。成品兼容路线保留原默认 5 条。可设置目标时长、最少原片段数和声音要求。默认最多下载 30 个候选、单个 2 GiB、总量 4 GiB，每项失败最多尝试 2 次。允许智能切片时默认最多两轮、每轮一个片段、每轮等待最多 300 秒。均可配置。

媒体通过完整视频/音轨解码才计入素材。完全重复文件不计数，近似画面默认等待审阅后才重复计入目标；同原片段时长只计最长一条。ready 只表示配置的技术指标满足，尚需语义选片。素材不足时报告缺口与选项，不换商品、账号或无限重剪。

同时读取百应的 auto_cut_videos 和 author_cut_videos：手动触发智能切片的新视频可能出现在后者。合并后按视频 ID 去重，并保留 provider_video_collection 说明来源；两路中的处理中记录都阻止误判为未剪片段。

每任务输出 materials.json（素材与缺口）和 editor_handoff.json（已验证文件与导入信息）。视频统一建议用 media_register 进入后续剪辑插件的异步队列，保存导入映射，完成后核对哈希；失败通过 media_job_retry 新建排队尝试。部分素材也可交接但保留缺口；本插件不自动建立时间线或发布视频。

## 依赖和存储

0.2.3 增加结构化故障诊断。查询失败的 `structuredContent.error`、后台任务的 `last_error` 和逐文件的 `error_detail` 区分网络、限流、登录、权限、服务端响应、本地存储和内部错误，并给出诊断编号及恢复建议。不会将异常伪装成空素材，也不会自动重提状态未知的智能切片。脱敏诊断写在数据目录的 `diagnostics/`，不包含原始异常文本、请求参数、响应体或凭据。详细约定见 [错误诊断与恢复](skills/baiying-replay/references/errors.md)。

需要 Python 3.10+。在 MCP 使用的 Python 中执行 `python -m pip install -r requirements.txt`，安装 requests 与自带 FFmpeg 的 imageio-ffmpeg。也可配置 FFMPEG_BIN，FFPROBE_BIN 可选。

Cookie、当前手机号及任务记录保存在包外。Windows 默认 `%LOCALAPPDATA%\baiying-replay`；其他系统 `~/.local/share/baiying-replay`。可复用桌面数据平台已有登录缓存。

BAIYING_COOKIE_CACHE_PATH、BAIYING_REPLAY_STATE_PATH、BAIYING_DATA_DIR 可调整存储。默认素材目录 `~/Downloads/baiying/账号ID/商品ID/任务ID/`，支持 NAS 绝对路径。后台进程沿用当前 Python 和环境，Windows 隐藏启动。中断后继续原任务，复核文件并刷新来源，不覆盖损坏的已有文件。

扫描有页数上限，不保证拿到历史上所有直播；未扫描完整不会报告来源耗尽。子账号归属不等于实际主播身份识别。

## 开发

核心源码 workflow_scripts/baiying_login/；`python scripts/build_baiying_plugin.py` 同步六个独立模块，`--check` 验证副本一致，`--archive 路径.zip` 只打包源码、配置和技能，不含账号缓存、任务数据库或视频。

`python server.py --call baiying_material_status` 从 stdin JSON 读取参数；不带参数使用 stdio MCP。后台记录只存手机号哈希，工作进程通过 stdin 获取手机号，不放入命令行或素材清单。
