# volcengine-plugins

长任务优先使用后台生图入口与统一批量等待；插件自行控制轮询频率，复用宿主现有后台只读工具，CardBush 无需修改。[后台任务、重复提交保护与重启边界](docs/background-generation.md)。参考视频 Skill 已明确要求在实际生成提示词中绑定音频编号、角色与音色，不能只附带音频。

## 0.7.0：v5.0 歌曲与纯音乐

新增 7 个音乐工具，加上后台生图和统一等待，现共 34 个 MCP 工具。支持自带歌词/创作描述生成人声歌曲、分段纯音乐 BGM、免费预览、异步查询、原样下载及歌词/字幕 JSON 保存。歌曲和 BGM 均显式使用官方 `v5.0`，不自动降级或重提付费任务。默认按时长后付费，也可明确选择预付费资源包。

音乐使用独立的 `VOLCENGINE_ACCESS_KEY_ID` / `VOLCENGINE_SECRET_ACCESS_KEY`，已有 Ark 或 MediaKit Key 不能替代。配置、调用示例和官方版本差异见[音乐生成说明](docs/music-generation.md)，内置 [music-generation Skill](skills/music-generation/SKILL.md)。更新后重新安装 Python 包并重连 MCP。

## 0.6.0：取源核对、局部擦除计划和画质 QC

新增 `video_media_preflight`、`video_subtitle_erase_plan`、`video_subtitle_erase_qc`、`video_export_publish`，共 25 个 MCP 工具。内置 [subtitle-erasure Skill](skills/subtitle-erasure/SKILL.md)；校验清晰度、按实际字幕位置与时段限定处理、输出纹理/音轨对照证据、另存发布版。NumPy、Pillow 和带 FFmpeg 的 imageio-ffmpeg 随 Python 依赖安装，不需要 imageio。QC 指标只提示可疑位置，不能自动证明无残留或无痕。

## 0.5.0：独立精细化去字幕与插件改名

插件由 `seedream-mcp` 改名为 `volcengine-plugins`，新增 6 个独立字幕擦除工具，共 21 个 MCP 工具。
默认使用火山 MediaKit 精细化 v5、仅字幕模式和画质优先，支持本地上传、范围/时间段控制、查询和原样下载。
复用 `MEDIAKIT_API_KEY`，不翻译、不配音、不请求重新生成整段视频。输入最高 2K，输出最高 1080p。
完整流程及限制见 [精细化字幕擦除](docs/video-subtitle-erase.md)。

更新迁移：重新安装此包 `python -m pip install .`，加载 `volcengine-plugins` 并停用旧 `seedream-mcp` 插件连接。
新启动命令为 `python -m volcengine_plugins`，MCP 服务键为 `volcengine`；既有工具名称和密钥变量保持不变。

## 0.4.1：参考文字检查

参考视频 Skill 增加上传前的小字、字幕、水印和署名检查，按任务要求保留或清理，并提供按当前切片坐标执行的 delogo 模板及复核要求。本次不改变生成接口。

## 0.4.0：任务管理与可靠下载

新增 `seedance_list_tasks`、`seedance_get_tasks` 和 `seedance_download_task(task_id, dest)`，支持服务端分页、批量状态与完整签名 URL 直接落盘。创建任务自动执行免费预检；本地拒绝明确未发出付费请求，已发出请求但没有账单证据时不猜扣费/退款。进度与 ETA 缺失时返回空值。

[参数、失败语义与离线验证](docs/task-delivery.md)。同步更新的参考视频 Skill 覆盖下载、有效运动帧率、逐刀音频、重复取段和末尾视频覆盖检查。更新需重新安装 Python 包并重连 MCP。

<img src="assets/logo.png" width="96" height="96" alt="Volcengine Plugins logo">

中文使用说明 · [English](README.en.md)

用于 Seedream 图片、Seedance 视频、火山 v5.0 歌曲/纯音乐，以及 MediaKit 视频超分和精细化字幕擦除。支持 Codex / Cardbush 插件格式，也可连接标准 MCP 客户端。

## 内置参考视频 Skill

插件已包含 [`reference-video-production`](skills/reference-video-production/SKILL.md)，负责编排：素材理解 → 真人图片遮罩 / 视频去真人化 → 按目标模型限制用 FFmpeg 切片 → COS 上传获取 URL → Seedance 视频参考生成 → 成片交付。

使用 Blender 配合视频生成时，优先生成首尾帧图片或外观参考图（已有合适图片可复用），人物默认适度风格化、避免完全真人化。Blender 只负责空间、整体走位和运镜，人物不建模肢体动作；图片、预演和文字分别确定外观、镜头及自然表演。参考组合按当前模型能力选择，不混用不兼容的首尾帧与视频参考模式。

在支持 Skill 的客户端直接描述需求，或显式调用 `$reference-video-production`。例如：“按参考视频替换商品与指定人物，保留节奏，中文有声；先检查当前模型限制，再处理素材、切片、上传并生成完整视频。”同时提供本地素材路径、人物/音色引用及字幕要求。Claude Code 通过插件根目录的 `skills/` 自动发现技能，调用入口以客户端显示为准；参见 [Claude 插件规范](https://code.claude.com/docs/en/plugins-reference#skills)。单独添加 MCP 连接不会自动加载 Skill，需要支持插件/Skill 的客户端导入此目录。

需要按任务另外准备：

- 可读取素材的客户端，以及可执行的 **FFmpeg / ffprobe**；由智能体决定切分，COS 工具不切视频。
- 合适的去真人化处理工具/插件，优先专用插件，**不绑定具体名称**；可更换实现，但必须支持所需的单人/多人及脸部/头部/全身范围。缺失时流程停在预处理，不把未处理真人素材直接上传。
- Tencent COS Upload 或等效 COS 上传工具，并按其说明配置环境/用户变量；此包不嵌入上传服务或去真人化模型。当前 `upload_video` 仅上传视频，图片/音频使用生成接口支持的合法引用或本地输入，不伪装为视频上传。
- 已开通目标模型的 `ARK_API_KEY`。Skill 根据实际能力读取每段/总参考时长、数量、输出时长、分辨率等，不固定切成 4 秒、15 秒或 30 秒，也不自动降档。

URL 优先采用临时预签名访问，不要求把桶改为公共读。原文件不覆盖，默认不删除/更名云端对象。处理图片/视频不保证匿名化或平台审核通过。Skill 是编排说明，不是新增的一键 MCP 工具；外部依赖就绪后才能实际运行，付费生成遵循用户授权。

更新后重新加载插件并开启新会话。此包保留原 MCP 工具及 Logo，不包含个人素材、私有资产 ID、运行任务记录或去真人化模型权重。

## 1. 安装与连接

需要 Python 3.11 或更新版本。解压插件包，在包含 `pyproject.toml` 的目录打开终端，执行：

```shell
python -m pip install .
```

然后在客户端安装/导入插件目录。使用标准 MCP 连接时，添加：

```json
{
  "mcpServers": {
    "volcengine": {
      "command": "python",
      "args": ["-m", "volcengine_plugins"]
    }
  }
}
```

- Codex / Cardbush 使用包内的 `.codex-plugin/plugin.json` 和 `.mcp.json`。先运行 `codex plugin marketplace add pycode4micro/cardbush-plugins --ref main` 添加市场，再执行 `codex plugin add volcengine-plugins@cardbush-plugins`。
- Claude 或其他标准 MCP 客户端可以使用上述配置；包内也提供 `.claude-plugin/plugin.json`。
- `command` 必须指向执行安装时的 Python；存在多个解释器时，请填写其绝对路径。
- 导入插件不会自动安装 Python 依赖，上面的安装命令仍需执行。
- 不要同时启用插件连接和同一服务的手动 MCP 连接，避免出现重复工具。

### 插件图标

Codex 使用 `interface.composerIcon`、`logo` 和 `logoDark`；深浅色界面共用自带深色底的图标。MCP 握手的 `serverInfo.icons` 与工具列表的 `icons` 同时提供内嵌 PNG，不需要联网下载图标。Claude Code 的插件清单目前没有专用 logo 字段；Claude 和其他客户端能否展示 MCP 图标取决于其界面实现，不保证所有版本都显示。更新后重新执行 `python -m pip install .`，重新安装/加载插件并开启新会话。

协议参考：[Claude 插件清单](https://code.claude.com/docs/en/plugins-reference#plugin-manifest-schema)、[MCP 图标](https://modelcontextprotocol.io/specification/2025-11-25/basic#icons)。本图标为独立插件标识，不代表厂商官方插件。

## 2. 配置密钥（推荐环境变量 / Windows 用户变量）

**推荐使用环境变量；Windows 桌面客户端优先配置“用户变量”。不必使用系统级变量，也无需管理员权限。插件不自动读取 `.env` 文件，不建议把密钥写进插件目录或配置文件。**

只需配置所使用的服务：

| 变量 | 用途 | 获取位置 |
| --- | --- | --- |
| `ARK_API_KEY` | Seedream 生图和 Seedance 生视频共用 | [火山方舟控制台](https://console.volcengine.com/ark/)的 API Key 管理；使用 API Key，不是 AK/SK 密钥对 |
| `MEDIAKIT_API_KEY` | 视频超分和字幕擦除使用，不自动复用 Ark 密钥 | [MediaKit 基础配置](https://console.volcengine.com/imp/ai-mediakit/settings)；使用有效的 MediaKit API Key，或具备相应权限的 IAM 通用 API Key |
| `VOLCENGINE_ACCESS_KEY_ID` / `VOLCENGINE_SECRET_ACCESS_KEY` | v5.0 人声歌曲和 BGM 的 AK/SK 签名鉴权 | 火山 IAM 密钥；另在 [AI 音乐控制台](https://console.volcengine.com/ai-music/product) 开通所选计费服务 |
| `VOLCENGINE_SESSION_TOKEN` | 可选，音乐临时 STS 凭据 | 使用临时 AK/SK 时填写配套 Token |

账号还需开通相应服务/模型，并具备权限和可用额度。密钥存在不等于服务已开通。

### Windows：用户变量（推荐）

1. 在 Windows 搜索中打开“编辑账户的环境变量”，或打开“环境变量”窗口。
2. 在“用户变量”区域点击“新建”。
3. 变量名填写 `ARK_API_KEY` 或 `MEDIAKIT_API_KEY`，变量值填写对应密钥，保存。
4. 重启客户端或重新连接 MCP，再按下一节检查配置。

插件会按需读取当前 Windows 用户保存的同名变量，即使桌面客户端启动较早、没有继承新变量也能读取；不会读取其他用户的变量。

### 临时测试：当前进程环境

PowerShell 示例通过隐藏输入读取密钥，不把真实值直接写进命令历史。下面设置超分密钥；生图/视频请将变量名改为 `ARK_API_KEY`。

```powershell
$enteredKey = Read-Host "MEDIAKIT_API_KEY" -AsSecureString
$env:MEDIAKIT_API_KEY = [System.Net.NetworkCredential]::new('', $enteredKey).Password
```

然后从**同一个终端**启动客户端或 MCP 服务。它不会更新已运行的桌面应用，也不会在终端退出后持久保存。

macOS / Linux 可在 Bash 中设置进程环境，只执行所需服务的部分：

```bash
read -r -s -p "ARK_API_KEY: " ARK_API_KEY
printf '\n'
export ARK_API_KEY
read -r -s -p "MEDIAKIT_API_KEY: " MEDIAKIT_API_KEY
printf '\n'
export MEDIAKIT_API_KEY
```

从该终端启动客户端。桌面图标启动的应用不一定继承终端变量，应使用客户端支持的环境注入方式。macOS/Linux 不使用 Windows 用户变量兜底。

### 读取优先级与覆盖问题

**客户端进程变量（包括空值） → Windows 当前用户变量 → 默认值。**

- 进程中已有旧密钥：旧值优先。清除客户端覆盖，或重启客户端刷新继承。
- 客户端配置了 `"ARK_API_KEY": ""` 或 `"MEDIAKIT_API_KEY": ""`：空值会阻止读取用户变量。应删除空的覆盖项，不要留空占位。
- Windows 用户变量按需读取、不缓存；仍受进程变量优先级影响。
- 在客户端进程中设置 `ARK_READ_USER_ENV=0` 可关闭整个插件的 Windows 用户变量兜底，包括 MediaKit。
- 密钥不要放进提示词、工具参数、聊天、截图、代码或分享包。

## 3. 检查配置

先调用以下任意能力工具，它们均免费、离线，不生成媒体：

- `seedream_capabilities`
- `seedance_capabilities`
- `video_enhance_capabilities`
- `video_subtitle_erase_capabilities`

查看 `configured` 以及 `configuration.variables.ARK_API_KEY` / `MEDIAKIT_API_KEY`：

| 返回值 | 含义 |
| --- | --- |
| `source=windows_user` | 来自当前 Windows 用户变量 |
| `source=process` | 来自客户端进程 |
| `configured=false` | 没有有效的非空配置 |
| `configured=true` | 已读取到值，不代表服务端鉴权或付费调用已验证 |

也可以执行 `python scripts/diagnose_config.py` 查看脱敏诊断，不显示密钥。

## 4. 工具与调用顺序

| 用途 | 免费能力 / 预览 | 付费执行 | 查询 |
| --- | --- | --- | --- |
| 图片 | `seedream_capabilities` / `seedream_preview_request` | `seedream_create_task` | `generation_wait_tasks`；旧同步 `seedream_generate` 保留 |
| 视频 | `seedance_capabilities` / `seedance_preview_request` | `seedance_create_task` | `seedance_get_task` |
| 超分 | `video_enhance_capabilities` / `video_enhance_preview_request` | `video_enhance_create_task` | `video_enhance_get_task` |
| 去字幕 | `video_subtitle_erase_capabilities` / `video_subtitle_erase_preview_request` | `video_subtitle_erase_create_task` | `video_subtitle_erase_get_task` |
| 人声歌曲 | `music_capabilities` / `music_preview_song` | `music_create_song` | `music_get_task` |
| 纯音乐 BGM | `music_capabilities` / `music_preview_bgm` | `music_create_bgm` | `music_get_task` |

音乐完成后用 `music_download_task` 保存原音频，可选保存包含歌词和原生字幕数据的 JSON。人声歌曲 30～240 秒、BGM v5.0 为 30～120 秒；不要把歌曲字段 `ModelVersion` 与 BGM 字段 `Version` 混用。详细示例见[音乐生成说明](docs/music-generation.md)。

本地超分素材另有 `video_enhance_upload`：上传文件并返回 `mediakit://` 地址，不创建增强任务；可能的存储/传输费用以服务商规则为准。

本地去字幕素材使用 `video_subtitle_erase_upload`；任务完成后可用 `video_subtitle_erase_download_task` 原样下载至新的绝对路径。完整参数见[去字幕说明](docs/video-subtitle-erase.md)。

先查能力、预览参数，确认后提交一次。异步任务提交成功不等于完成，之后用返回的同一个任务 ID 查询。**不要重复 create 来轮询。** 超时不自动重试，不自动更换模型。

### 图片生成

以下参数先传给 `seedream_preview_request`，确认后连同唯一 `request_id` 传给 `seedream_create_task`。按回执 ID 等待结果；旧同步调用可继续用 `seedream_generate`：

```json
{
  "request": {
    "prompt": "参考商品图，保持颜色、领口、袖口和织法，生成白底商品摄影。",
    "watermark": false,
    "output_format": "png"
  },
  "local": {
    "reference_images": ["C:/images/product.png"],
    "aspect_ratio": "3:4",
    "resolution": "2K",
    "save_images": true
  }
}
```

替换为现有文件的绝对路径；纯文生图省略 `reference_images`。本地参考图和 `request.image` 不要同时使用。直接设置 `request.size` 时，不再设置 `local.aspect_ratio` / `local.resolution`。

开启保存后，base64 图片通常保存至 `SEEDREAM_OUTPUT_DIR` 并返回绝对路径。显式指定 `response_format="url"` 时只返回链接，不自动下载。详细参数见 [图片参数说明](docs/official-parameters.md)。

### 视频生成

以下参数先传给 `seedance_preview_request`，确认后传给 `seedance_create_task`：

```json
{
  "request": {
    "model": "2.5",
    "content": [
      {"type": "text", "text": "参考视频1的运镜和节奏，使用图片1的商品，生成有自然环境声的完整短片。"},
      {"type": "video_url", "video_url": {"url": "https://example.com/reference.mp4"}, "role": "reference_video"},
      {"type": "image_url", "image_url": {"url": "asset://YOUR_PRODUCT_ASSET_ID"}, "role": "reference_image"}
    ],
    "resolution": "1080p",
    "ratio": "adaptive",
    "duration": 10,
    "generate_audio": true,
    "watermark": false
  }
}
```

URL / asset ID 须换成账号可访问的真实素材。成功后取 `task.id`，传入 `seedance_get_task` 的 `task_id`。

| model 别名 | 输出分辨率 | 时长 |
| --- | --- | --- |
| `2.0` | 480p / 720p / 1080p / 4k | 4–15秒或 -1 |
| `2.0-fast` / `2.0-mini` | 480p / 720p | 4–15秒或 -1 |
| `2.5` / `2.5-pro` | 480p / 720p / 1080p | 4–30秒或 -1 |

`-1` 交给模型决定时长。需要声音请设置 `generate_audio=true`。参考视频使用 HTTP(S) / `asset://`，不能直接传本地 MP4 或视频 base64。图片/音频可用支持的 URL / asset / data URI；本地图片/音频需绝对路径并设置 `local.allow_local_files=true`。

**MediaKit 上传返回的 `mediakit://` 不可直接用于 Seedance。** 使用 Seedance 支持的资产/上传流程。自定义 `ep-*` 接入点还需正确声明 `local.capability_profile`。更多声音参考、模型限制和编辑参数见 [视频参数说明](docs/seedance-parameters.md)。

### 超分：标准版 / 大模型版

1. 本地素材调用 `video_enhance_upload`，参数为 `{"file_path":"C:/videos/input.mp4"}`，保存返回的 `video_url`。
2. 将下面参数传给 `video_enhance_preview_request`，确认后传给 `video_enhance_create_task`。
3. 将返回的 `task.task_id` 传给 `video_enhance_get_task`。
4. `status=completed` 时，从 `task.result.video_url` 获取视频；不自动下载。

标准版：

```json
{"request":{"variant":"standard","video_url":"mediakit://YOUR_FILE_ID","resolution":"1080p","scene":"aigc","enhance_style":"natural","bitrate_level":"high","client_token":"unique-request-001"}}
```

大模型版：

```json
{"request":{"variant":"generative","video_url":"mediakit://YOUR_FILE_ID","resolution":"1080p","bitrate_level":"high","client_token":"unique-request-002"}}
```

- 每次新逻辑请求使用新的 `client_token`；同一不确定提交保留原 token，先核实状态，不要随意换 token 重试。
- 标准版支持场景和 `hd` / `natural` 风格。大模型版不支持 `scene`、`enhance_style`、`resolution_limit`。
- 大模型版只支持 SDR 输入，输出支持720p / 1080p / 2k。
- 不填 `fps` 保留输入帧率；插件不主动剪辑、加速或静音。
- 输入支持 HTTP(S) / `mediakit://` / `vod://` / `tos://`。详细范围见 [超分参数说明](docs/video-enhance.md)。

## 5. 可选环境变量

通常只需配置密钥，下面各项可保持默认：

| 变量 | 默认值 / 用途 |
| --- | --- |
| `ARK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` |
| `SEEDREAM_MODEL` | `doubao-seedream-5-0-pro-260628` |
| `SEEDREAM_OUTPUT_DIR` | 当前用户的 `Pictures/Seedream` |
| `SEEDREAM_TIMEOUT_SECONDS` | 300秒 |
| `SEEDANCE_MODEL` | `doubao-seedance-2-5-260628` |
| `SEEDANCE_TIMEOUT_SECONDS` | 单次 HTTP 请求60秒，可设置1–300秒 |
| `MEDIAKIT_BASE_URL` | `https://mediakit.cn-beijing.volces.com` |
| `MEDIAKIT_TIMEOUT_SECONDS` | 单次 HTTP 请求120秒，可设置1–300秒 |
| `VOLCENGINE_MUSIC_TIMEOUT_SECONDS` | 音乐单次 HTTP 请求60秒，可设置1–300秒；异步提交后按任务 ID 查询 |
| `ARK_READ_USER_ENV` | `1`；设为 `0` 关闭 Windows 用户变量兜底 |

客户端工具超时建议至少360秒。API 基址仅由管理员通过环境变量设置，不接受提示词/工具参数覆盖。

## 6. 常见问题与更新

| 问题 | 处理方式 |
| --- | --- |
| `No module named volcengine_plugins` | 使用客户端对应的 Python 执行 `python -m pip install .` |
| 密钥设置后未读取 | 查能力工具的 `configuration`；检查变量名、空值覆盖、旧进程变量、禁用开关 |
| 401 / 403 | 检查对应服务的密钥、有效期、开通状态和权限；超分不自动使用 Ark 密钥 |
| 参数被拒绝 | 查对应能力工具，不要混用不同模型/版本的参数 |
| 已有任务 ID 但没视频 | 用 get_task 查原任务，不要再提交生成 |
| 结果链接打不开 | 可能已过期，应及时下载。MediaKit 可在支持的保留期内查询刷新结果链接，不保证所有服务都能恢复 |
| 更新后没有新工具 | 重新安装 Python 包和加载插件；Codex 新开任务，其他客户端重连 MCP |

更新时解压新版，执行 `python -m pip install .`，再重新加载插件。原环境/用户变量可继续使用。不要把输出文件和密钥放进要分享的插件包。
