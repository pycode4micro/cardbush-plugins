# 组件、分工与发现

以下区分 2026-10-17 的流程实测版本与后续仓库整合版，使用时仍应读取当前环境的版本与 schema。无须为复现旧案例降级已有插件。当前生成默认已按用户要求统一为 Seedance 2.0 Fast；表内早期 2.5 记录仅说明当时实际执行的模型。

| 组件 | 实测版本或形态 | 负责什么 | 如何接入 |
|---|---|---|---|
| `baiying-replay` 插件 + 同名 skill | 0.2.2 | 手机登录、子账号/商品、已有切片与原回放、智能切片、下载校验、缺口和交接 | 发现 `baiying_replay` MCP；无需启动旧数据平台网页/数据库 |
| cardbush-plugins 的 `video-editer` 插件；界面/MCP/Python 包名 `video_editer` | 0.3.8 将生成默认改为 2.0 Fast；保留 0.3.7 的具体问题/反差与原片兑现、0.3.6 表演选项及 0.3.5 默认标准与音频修复 | 项目、统一异步队列、GPU 优先与资源控制、时间线、花字音轨、排队重试及音量/帧覆盖验收；保存账号钩子计划/生成状态/验收与插入 | 安装标识 `video-editer@cardbush-plugins`；随插件提供 `video-editer` 执行 skill 和 `baiying-dialogue-video` 编排 skill，工具本身不会选素材、写文案或调用模型 |
| cardbush-plugins 的 `volcengine-plugins` + `reference-video-production` skill（原 `seedream-mcp`） | 仓库 0.7.1 默认 Fast；早期实测使用 seedream-mcp 0.3.0+codex.20260912063727 | 参考素材编排；Seedance 能力、免费预览、实际视频生成与查询 | 新生成显式用 `doubao-seedance-2-0-fast-260128`、720p、有声；2.5 历史任务记录保持真实，不作新请求模板 |
| `anchor-asset-registry` 本地 skill | 1.0.0 | 主播别名、脸部与音色资产对应关系 | 本次读取本地登记表；这不是采集接口，也不是独立生成服务 |
| `video-face-stylizer` 本地插件/CLI + 同名 skill | 包名 `video-face-stylizer-local` 0.2.3 | 原参考片头部视觉预处理，保留商品、动作、背景 | 本次走本地 CLI 并显式接入编辑器重任务锁；换环境先发现能力与入口 |
| `tencent-cos-upload` 插件 | 0.2.1+codex.20260912094424 | 上传指定参考片，提供有限期私有访问链接 | 本次调用 `upload_video`；没有为这次任务使用单独 COS skill |
| `short-video-prompts` 插件 + 同名 skill | 1.1.0，可选 | 钩子脚本、叙事承诺、参考绑定与生成提示词合同 | 只做脚本规划；原有珂欣案例未通过它产出。实拍正文使用源区间剪辑表，不套用全生成任务合同或默认 15/25 秒时长 |

## 编辑器引用

维护源为 [cardbush-plugins / video-editer](https://github.com/pycode4micro/cardbush-plugins/tree/main/plugins/video-editer)。读取当前安装目录内的 `skills/video-editer/SKILL.md`，按阶段继续读取同目录的 `resources.md`、`generated-hooks.md`、`audio-and-delivery.md`；不写死个人插件缓存目录或版本路径。

0.3.4 保留原 74 项工具及参数，增加资源、统一队列和钩子工具至 87 项。`render_retry` 返回新的排队任务，须按新 ID 查询至 `succeeded.result`；不再把重试响应当成视频。音频验收默认会占用同一个重任务槽，`timeline_validate(check_audio=false)` 仅作结构检查。调用方仍需按发现的 namespace 使用工具，不能由显示名拼接前缀。

0.3.5 保持这些工具接口，新增随包维护的编排 skill、默认参数与内容规则。维护源位于 `plugins/video-editer/skills/baiying-dialogue-video/`；独立本地副本应与该目录同步。`references/editorial.md` 是内容规则的共同入口，`assets/workflow-plan.json` 是调用方计划，二者都不会自动写入时间线。

0.3.6 的钩子话术方向、韵律与音效记录见 `references/hook-options.md`。秋天强调质感版已完成技术制作，但用户否定了单纯夸质感的开场内容；0.3.7 据此增加关注理由和原片兑现记录，仍保留可选重音与音效。这些是调用方的编排指令，没有新增逐字语音控制工具；修订文案尚未再次生成验证。

## 技术底座

- FFmpeg/ffprobe：元数据、解码、音视频处理与编码，优先由插件排队调用。
- 本地 ASR：这次使用 faster-whisper；是可替换的转录能力，不是新增独立插件。中文 ASR 可能把“扣子”误写为“袖子”，不能直接生成销售文案。
- PIL 与 SVG：本次自编图形脚本输出透明 PNG 和可编辑 SVG，编辑器执行叠加及关键帧；字体来自系统。还没有专业花字模板库，也没有调用 Herk 或 Remotion 渲染器。
- `skill-creator`：整理和维护本 skill 时使用，不是每条视频的运行依赖。

## 阶段依赖

- 只规划：可利用现有记录；能查询时读取账号/素材/能力，不必启动所有服务。
- 只备料：百应插件及其本地媒体依赖。
- 交接或剪辑：再加编辑器、可用视听审阅与字体。
- 生成真人参考钩子：再加生成服务、可用的主播资产、符合要求的真实参考输入路线；预处理和 COS 是本次验证过的路线，已入库的合规原片资产可走另一条受支持路线。

工具未直接暴露时，先查当前安装位置和插件说明，可连接其独立 MCP 或受支持 CLI。不要从旧案例抄绝对路径，不擅自安装缺失插件。缺少生成依赖时仍可完成不依赖它的正文与流程计划，准确报告该阶段状态。

## 可迁移配置

账号资料、主播资产表、模型入口、存储凭据、FFmpeg/字体、资源目录、输出目录应由运行环境提供；SKILL.md 不保存手机号、验证码、Cookie、私有 Asset ID 或签名 URL。

“重任务并发 1”约束同一主机上共同使用资源锁的进程。不同机器可以各自工作；若希望多台电脑总共只跑 1 个，需要外部调度服务，现有编辑器的文件锁不能充当分布式队列。
