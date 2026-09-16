# Cardbush Plugins

通过 GitHub 分发的 Codex 插件市场，包含 Seedream / Seedance / MediaKit、腾讯云 COS、千川、video_editer 剪辑、视频生成去真人化处理、MiniMax 音乐创作，以及飞书机器人插件。

目录和市场索引参照 [OpenAI 插件仓库](https://github.com/openai/plugins) 与 [OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins)。这是独立维护的插件市场。

## 添加云端市场

在已安装 Codex CLI 的电脑上运行：

```shell
codex plugin marketplace add pycode4micro/cardbush-plugins --ref main
```

也可以使用完整仓库地址：

```shell
codex plugin marketplace add https://github.com/pycode4micro/cardbush-plugins.git --ref main
```

重新打开 Codex 的插件页面，在市场来源中选择 **Cardbush Plugins**，即可浏览和安装这七个插件。若页面未刷新，重启客户端。

| 插件 | 包内基础版本 | 功能 | 安装说明 |
| --- | --- | --- | --- |
| `seedream-mcp` | 0.4.0 | Seedream 图片、Seedance 视频、MediaKit 超分与参考视频 Skill | [生成服务配置](plugins/seedream-mcp/README.md) |
| `tencent-cos-upload` | 0.2.0 | COS 上传、下载及经确认的单对象删除或重命名 | [COS 配置](plugins/tencent-cos-upload/README.md) |
| `qianchuan` | 1.0.0 | 自带运行代码的千川素材、报表及受控投放工具 | [千川配置](plugins/qianchuan/README.md) |
| `video-editer`（video_editer） | 0.2.0 | 74 项无模型剪辑工具：长视频证据浏览、Agent 索引与候选、时间线、花字、转场及渲染 | [剪辑插件配置](plugins/video-editer/README.md) |
| `video-face-stylizer` | 0.2.2 | 本地整头/脸部白模处理、小脸补漏与 CPU/GPU 渲染，Windows 后台启动不弹终端 | [去真人化处理配置](plugins/video-face-stylizer/README.md) |
| `minimax-music` | 1.0.0 | 主题曲、纯音乐 BGM、歌词创作与改写、参考翻唱；11 项工具、动画配乐技能和 Music 3 自部署连接 | [音乐插件配置](plugins/minimax-music/README.md) |
| `feishu-bot` | 1.0.0 | 独立飞书机器人、电子表格、多维表格及账号分表；默认只读，环境变量和启动参数控制修改能力 | [飞书插件配置](plugins/feishu-bot/README.md) |

市场添加成功后，也可按需用 CLI 安装：

```shell
codex plugin add seedream-mcp@cardbush-plugins
codex plugin add tencent-cos-upload@cardbush-plugins
codex plugin add qianchuan@cardbush-plugins
codex plugin add video-editer@cardbush-plugins
codex plugin add video-face-stylizer@cardbush-plugins
codex plugin add minimax-music@cardbush-plugins
codex plugin add feishu-bot@cardbush-plugins
```

安装或更新后，新建 Codex 任务以载入新的技能和工具。若已有 `@personal` 下的同名插件，请在插件管理页面选用一个来源，避免重复启用同一 MCP 服务。

## 新电脑运行准备

GitHub 托管的是市场索引和插件文件。七个插件均在本机通过 stdio 提供 MCP 工具。添加市场本身不会配置业务凭据，也不会把本地服务转换为云端 HTTPS MCP；各插件的依赖安装方式如下。

### Seedream 与腾讯云 COS

需要 Python 3.11 或更新版本，且 Codex 启动环境能通过 `python` 找到它。克隆仓库，在仓库根目录安装需要的包：

```shell
git clone https://github.com/pycode4micro/cardbush-plugins.git
cd cardbush-plugins
python -m pip install ./plugins/seedream-mcp
python -m pip install ./plugins/tencent-cos-upload
```

使用与 MCP 配置相同的 Python 解释器。导入插件与安装 Python 包是两个步骤；更新源码后应重新执行相应的 `pip install` 命令。

凭据通过环境变量配置，Windows 桌面客户端可使用各插件支持的用户环境变量读取功能：

- Seedream / Seedance：`ARK_API_KEY`。
- MediaKit 视频增强：`MEDIAKIT_API_KEY`，独立于 Ark 密钥。
- 腾讯云 COS：`TENCENT_COS_SECRET_ID`、`TENCENT_COS_SECRET_KEY`、`TENCENT_COS_BUCKET`、`TENCENT_COS_REGION`；临时凭据还需 `TENCENT_COS_TOKEN`。

完整可选变量见各插件 README。密钥值由使用者自行配置，仓库不包含密钥。视频工作流按需另行准备 FFmpeg / ffprobe 和参考素材预处理工具。

### 千川

千川插件包含 `runtime/app` 源码，通过 `uv run --frozen --script` 启动。客户端需能找到 `uv`，启动脚本要求 Python 3.12 或更新版本。首次启动时，`uv` 按插件内的锁文件联网准备依赖。

插件使用独立的配置与数据目录，不依赖桌面的 `qianchuan_tool_service`。缺少业务配置时仍可启动 MCP，通过 `qianchuan_setup_status` 查看配置位置和缺失项。按插件内的 `config.example.env` 配置网关及安全参数，重新连接后加载业务工具；原服务的凭据和投放权限不会自动迁移。

详细配置、后台限时任务行为及运行限制见[千川 README](plugins/qianchuan/README.md)。

### video_editer 剪辑插件

需要 Python 3.11 或更新版本，并使用 MCP 配置中同一个 `python` 安装运行依赖。在克隆后的仓库根目录运行：

```shell
python -m pip install ./plugins/video-editer
```

插件自带独立 Python/FFmpeg 执行代码，不依赖其他剪辑平台，不读取其他项目的 `.env`，也不调用模型 API。理解视频、撰写花字、选择片段和转场由宿主 Agent 完成；插件提供本地视听证据、版本化索引、候选管理以及明确参数下的剪辑执行。

FFmpeg 需支持 libx264/libass；已声明的 `imageio-ffmpeg` 提供二进制回退。中文花字需要本机安装微软雅黑或 Noto Sans CJK 等中文字体，字体不随仓库分发。可通过 `VIDEO_EDITER_DATA_DIR` 指定独立数据目录，默认在用户目录下的 `.video_editer`；原片、数据库、预览及成片不会写入市场仓库。

已包含 85 项离线回归测试，覆盖合法/非法输入、实际音视频执行、源时间锚定、后台队列和长素材证据链；另有可选的 2 小时、200MB 以上合成素材基准。合成基准不代表真实商品视频的语义召回率。外部生成特效接入、跨项目创作库和结构模板尚未实现，详见[插件说明](plugins/video-editer/README.md)及[长素材使用指引](plugins/video-editer/skills/video-editer/long-media.md)。

### 视频生成去真人化处理

`video-face-stylizer` 适用于 Windows x64，通过包内 PowerShell 脚本启动，无需 API 密钥。首次连接自动准备 Python 3.12 和插件独立运行环境，按 `uv.lock` 安装完整依赖，并检查模型、CPU 渲染与 FFmpeg；首次准备需要联网，后续连接复用已安装环境。

源码包含四个模型/网格资产及来源、许可和 SHA256，依赖包含 FFmpeg 编码器。CPU 模式无需独显；GPU 模式需要支持 OpenGL 3.3 的驱动。0.2.2 修复 Python、FFmpeg 和依赖检查弹出终端窗口的问题，并保留原始 MCP 字节流。安装后也可在插件目录运行 `setup.cmd` 预先准备环境。

自动处理仍需抽查漏检时间段，白模效果不保证可靠身份匿名化。功能、参数及 65 项测试与实际处理验证记录见[插件说明](plugins/video-face-stylizer/README.md)和[验证记录](plugins/video-face-stylizer/VALIDATION.md)。

### MiniMax 音乐创作

需要 Node.js 22 或更新版本，Codex 启动环境需能找到 `node`。插件自带单文件 MCP 运行程序，运行时无需 `npm install`。包含 11 项工具和 2 个技能，覆盖带歌词歌曲、纯音乐 BGM、歌词创作/改写、参考歌曲分析与翻唱，以及动画主题曲和场景配乐工作流。

云端凭据通过 `MINIMAX_API_KEY` 或私有密钥文件配置。也可以连接已有的 MiniMax Music 3 / SGLang-Omni 自部署服务；该模式支持歌曲和纯音乐，不提供专有歌词/翻唱 API。插件不自动部署模型服务器。

截至 2026-09-15，MiniMax 官方公告说明音乐/歌词付费 API 自 2026-08-20 起不再对新用户开放，历史付费用户可继续使用；网页 Audio 账户不等于 API 权限。插件已完成 12 项离线行为测试和 MCP 安装验证，真实出歌需要可用凭据或自部署服务。详见[音乐插件说明](plugins/minimax-music/README.md)与[验证记录](plugins/minimax-music/VALIDATION.md)。

### 飞书机器人

需要 Python 3.11 或更新版本，以及能在 Codex 启动环境中找到的 `uv`。插件自带 Python MCP 服务、技能及 `uv.lock`；首次启动自动准备依赖，虚拟环境放在插件数据目录，不依赖业务平台或其他插件。

默认 `FEISHU_ALLOW_READ=true`、`FEISHU_ALLOW_WRITE=false`，提供 14 个只读/状态工具。配置 `FEISHU_ALLOW_WRITE=true` 或启动参数 `--allow-write` 后，可使用消息发送、表格修改和账号分表追加等共 28 个工具；启动参数优先于环境变量，关闭的能力在工具注册及 HTTP 层同时拦截。

把包内 `.env.example` 复制到用户目录 `~/.config/feishu-bot/.env`，按需配置自建应用 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 及飞书应用/文档权限。也可用 `--env-file` 指定外部配置。自定义群 webhook 只支持发送，不能用于读取表格。

已通过 74 项自动化测试、实际 MCP 启动及跨目录迁移测试，并对真实目标表的 6 个页签抽查 120 行，与原客户端逐格一致。真实验证全程只读；写入通过模拟 HTTP 测试。详见[配置说明](plugins/feishu-bot/README.md)和[验证记录](plugins/feishu-bot/docs/verification.md)。

## 更新市场

```shell
codex plugin marketplace upgrade cardbush-plugins
```

然后在插件页面更新或重新安装需要的插件。市场目录升级不会自动替换通过 `pip` 安装的 Python 服务代码。

## 仓库结构

```text
.agents/plugins/marketplace.json
plugins/
  seedream-mcp/
    .codex-plugin/plugin.json
    .mcp.json
    pyproject.toml
    src/
    skills/
  tencent-cos-upload/
    .codex-plugin/plugin.json
    .mcp.json
    pyproject.toml
    src/
  qianchuan/
    .codex-plugin/plugin.json
    .mcp.json
    plugin.json
    mcp.json
    config.example.env
    runtime/app/
    scripts/launch.py
    scripts/launch.py.lock
    skills/
  video-editer/
    .codex-plugin/plugin.json
    .mcp.json
    pyproject.toml
    server.py
    video_editer/
    skills/video-editer/
    tests/
    scripts/
  video-face-stylizer/
    .codex-plugin/plugin.json
    .mcp.json
    plugin.json
    mcp.json
    pyproject.toml
    uv.lock
    requirements.lock
    setup.cmd
    scripts/
    src/video_face_stylizer/
    skills/video-face-stylizer/
    tests/
  minimax-music/
    .codex-plugin/plugin.json
    .mcp.json
    plugin.json
    mcp.json
    package.json
    package-lock.json
    scripts/minimax-music.mjs
    src/
    skills/music-studio/
    skills/animation-soundtrack/
    assets/
    tests/
  feishu-bot/
    .codex-plugin/plugin.json
    .mcp.json
    plugin.json
    mcp.json
    pyproject.toml
    uv.lock
    .env.example
    src/feishu_bot/
    skills/feishu-bot/
    docs/
    tests/
    scripts/
```

市场条目的 `source.path` 相对于仓库根目录，保持为 `./plugins/<插件名>`。每个插件保留原始包内版本、源码和现有资源；发布整理将兼容清单的默认提示统一为数组，并补充仓库链接。原始 ZIP 不需要作为市场入口上传。
