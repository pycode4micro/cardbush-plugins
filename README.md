# Cardbush Plugins

通过 GitHub 分发的 Codex 插件市场，包含 Seedream / Seedance / MediaKit / 火山 v5.0 音乐、腾讯云 COS、千川、video_editer 剪辑、视频生成去真人化处理、MiniMax 音乐创作、飞书机器人，抖音视频下载、Agent Chatroom 聊天室、Logic Memory 经验学习与检索、分部位衣服设计，以及企业与个人资料库插件。

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

重新打开 Codex 的插件页面，在市场来源中选择 **Cardbush Plugins**，即可浏览和安装这些插件。若页面未刷新，重启客户端。

| 插件 | 包内基础版本 | 功能 | 安装说明 |
| --- | --- | --- | --- |
| `volcengine-plugins` | 0.7.0 | Seedream 图片、Seedance 视频、v5.0 歌曲/BGM、MediaKit 超分与去字幕；34 个 MCP 工具 | [生成服务配置](plugins/volcengine-plugins/README.md) |
| `tencent-cos-upload` | 0.2.0 | COS 上传、下载及经确认的单对象删除或重命名 | [COS 配置](plugins/tencent-cos-upload/README.md) |
| `qianchuan` | 1.0.0 | 自带运行代码的千川素材、报表及受控投放工具 | [千川配置](plugins/qianchuan/README.md) |
| `video-editer`（video_editer） | 0.2.0 | 74 项无模型剪辑工具：长视频证据浏览、Agent 索引与候选、时间线、花字、转场及渲染 | [剪辑插件配置](plugins/video-editer/README.md) |
| `video-face-stylizer` | 0.2.3 | 本地整头/脸部白模处理、帽子与侧背补漏、网格取坐标及覆盖统计 | [去真人化处理配置](plugins/video-face-stylizer/README.md) |
| `minimax-music` | 1.0.0 | 主题曲、纯音乐 BGM、歌词创作与改写、参考翻唱；11 项工具、动画配乐技能和 Music 3 自部署连接 | [音乐插件配置](plugins/minimax-music/README.md) |
| `feishu-bot` | 1.0.0 | 独立飞书机器人、电子表格、多维表格及账号分表；默认只读，环境变量和启动参数控制修改能力 | [飞书插件配置](plugins/feishu-bot/README.md) |
| `douyin-video-download` | 0.2.0 | 获取 Cookie 文件后下载单条视频；1 个 MCP 工具、3 个参数，内部完成候选选择及落盘校验 | [抖音下载使用说明](plugins/douyin-video-download/README.md) |
| `agentchatroom` | 0.1.1 | 本地或远程聊天室、聊天码加入、人类网页邀请、消息查询与指定参与者等待 | [聊天室配置](plugins/agentchatroom/README.md) |
| `logic-memory` | 0.1.0 | 独立可选的 learn / consult 经验工具、BM25 检索、幂等反馈与旧数据导入 | [经验插件配置](plugins/logic-memory/README.md) |
| `garment-designer` | 0.2.0 | 分部位矢量衣服设计、确认后整体生图及对照分析，支持 PDF、PPTX、HTML 设计方案导出；7 个 MCP 工具和交互面板 | [衣服设计使用说明](plugins/garment-designer/README.md) |
| `knowledge-library` | 0.1.0 | 企业与个人资料导入、按部门／场景检索、原文出处、版本及归档管理；7 个 MCP 工具和交互面板，当前为 demo | [资料库使用说明](plugins/knowledge-library/README.md) |

市场添加成功后，也可按需用 CLI 安装：

```shell
codex plugin add volcengine-plugins@cardbush-plugins
codex plugin add tencent-cos-upload@cardbush-plugins
codex plugin add qianchuan@cardbush-plugins
codex plugin add video-editer@cardbush-plugins
codex plugin add video-face-stylizer@cardbush-plugins
codex plugin add minimax-music@cardbush-plugins
codex plugin add feishu-bot@cardbush-plugins
codex plugin add douyin-video-download@cardbush-plugins
codex plugin add agentchatroom@cardbush-plugins
codex plugin add logic-memory@cardbush-plugins
codex plugin add garment-designer@cardbush-plugins
codex plugin add knowledge-library@cardbush-plugins
```

安装或更新后，新建 Codex 任务以载入新的技能和工具。若已有 `@personal` 下的同名插件，请在插件管理页面选用一个来源，避免重复启用同一插件。

## 新电脑运行准备

GitHub 托管的是市场索引和插件文件。这些插件在本机通过 stdio 提供 MCP 工具；抖音下载也保留无需安装依赖的独立 Skill/CLI 入口。添加市场本身不会配置业务凭据，也不会把本地服务转换为云端 HTTPS MCP；各插件的依赖安装方式如下。

### Volcengine Plugins 与腾讯云 COS

需要 Python 3.11 或更新版本，且 Codex 启动环境能通过 `python` 找到它。克隆仓库，在仓库根目录安装需要的包：

```shell
git clone https://github.com/pycode4micro/cardbush-plugins.git
cd cardbush-plugins
python -m pip install ./plugins/volcengine-plugins
python -m pip install ./plugins/tencent-cos-upload
```

使用与 MCP 配置相同的 Python 解释器。导入插件与安装 Python 包是两个步骤；更新源码后应重新执行相应的 `pip install` 命令。

凭据通过环境变量配置，Windows 桌面客户端可使用各插件支持的用户环境变量读取功能：

- Seedream / Seedance：`ARK_API_KEY`。
- MediaKit 视频增强和字幕擦除：`MEDIAKIT_API_KEY`，独立于 Ark 密钥。
- 火山 v5.0 歌曲和纯音乐：`VOLCENGINE_ACCESS_KEY_ID`、`VOLCENGINE_SECRET_ACCESS_KEY`，另开通 AI 音乐服务；可选 STS `VOLCENGINE_SESSION_TOKEN`。详见[音乐生成说明](plugins/volcengine-plugins/docs/music-generation.md)。
- 腾讯云 COS：`TENCENT_COS_SECRET_ID`、`TENCENT_COS_SECRET_KEY`、`TENCENT_COS_BUCKET`、`TENCENT_COS_REGION`；临时凭据还需 `TENCENT_COS_TOKEN`。

`volcengine-plugins` 原名 `seedream-mcp`。更新时安装新插件并停用旧连接，使用 `python -m volcengine_plugins`；现有 API Key 变量和工具名称继续有效。精细化去字幕默认使用 v5，最高输出 1080p，详见[去字幕说明](plugins/volcengine-plugins/docs/video-subtitle-erase.md)。

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

自动处理仍需抽查漏检时间段，白模效果不保证可靠身份匿名化。0.2.3 增加椭圆取坐标预览与无掩膜/回退覆盖计数。功能、参数及 83 项测试与实际处理验证记录见[插件说明](plugins/video-face-stylizer/README.md)和[验证记录](plugins/video-face-stylizer/VALIDATION.md)。

### MiniMax 音乐创作

需要 Node.js 22 或更新版本，Codex 启动环境需能找到 `node`。插件自带单文件 MCP 运行程序，运行时无需 `npm install`。包含 11 项工具和 2 个技能，覆盖带歌词歌曲、纯音乐 BGM、歌词创作/改写、参考歌曲分析与翻唱，以及动画主题曲和场景配乐工作流。

云端凭据通过 `MINIMAX_API_KEY` 或私有密钥文件配置。也可以连接已有的 MiniMax Music 3 / SGLang-Omni 自部署服务；该模式支持歌曲和纯音乐，不提供专有歌词/翻唱 API。插件不自动部署模型服务器。

截至 2026-09-15，MiniMax 官方公告说明音乐/歌词付费 API 自 2026-08-20 起不再对新用户开放，历史付费用户可继续使用；网页 Audio 账户不等于 API 权限。插件已完成 12 项离线行为测试和 MCP 安装验证，真实出歌需要可用凭据或自部署服务。详见[音乐插件说明](plugins/minimax-music/README.md)与[验证记录](plugins/minimax-music/VALIDATION.md)。

### 飞书机器人

需要 Python 3.11 或更新版本，以及能在 Codex 启动环境中找到的 `uv`。插件自带 Python MCP 服务、技能及 `uv.lock`；首次启动自动准备依赖，虚拟环境放在插件数据目录，不依赖业务平台或其他插件。

默认 `FEISHU_ALLOW_READ=true`、`FEISHU_ALLOW_WRITE=false`，提供 14 个只读/状态工具。配置 `FEISHU_ALLOW_WRITE=true` 或启动参数 `--allow-write` 后，可使用消息发送、表格修改和账号分表追加等共 28 个工具；启动参数优先于环境变量，关闭的能力在工具注册及 HTTP 层同时拦截。

把包内 `.env.example` 复制到用户目录 `~/.config/feishu-bot/.env`，按需配置自建应用 `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 及飞书应用/文档权限。也可用 `--env-file` 指定外部配置。自定义群 webhook 只支持发送，不能用于读取表格。

已通过 74 项自动化测试、实际 MCP 启动及跨目录迁移测试，并对真实目标表的 6 个页签抽查 120 行，与原客户端逐格一致。真实验证全程只读；写入通过模拟 HTTP 测试。详见[配置说明](plugins/feishu-bot/README.md)和[验证记录](plugins/feishu-bot/docs/verification.md)。

### 抖音视频下载

需要 Python 3.10 或更新版本、网络及本地目录写入权限。CLI 仅使用 Python 标准库；使用 MCP 工具时执行 `python -m pip install -r ./plugins/douyin-video-download/requirements.txt`，然后重新加载插件。无需 API Key、Playwright 或浏览器安装。

在已登录的抖音网页中，从开发者工具 Network 的请求头复制 Cookie，保存为私有 UTF-8 文件；也支持已有 Netscape/JSON 导出。向唯一工具 `douyin_download` 传分享链接、Cookie 文件绝对路径和保存目录即可。云端使用服务器上的 Cookie 文件路径。没有 Cookie 时先提示获取步骤，不尝试访客下载、扫码或扫描浏览器账号目录。

工具内部只取目标视频 ID 对应的播放地址，比较最多三个候选并验证 MP4、分辨率、时长和大小；不覆盖已有文件、不转码或改写水印参数。Cookie 不能代替网页 JavaScript 或额外验证，工具未取得地址时报告具体限制，不另建下载流程。

离线测试覆盖 Cookie 格式与域名限制、重定向、下载校验、不覆盖及错误脱敏；MCP stdio 测试检查唯一工具和必填 Cookie。真实抖音页面与账号需另行验收，详见[抖音下载使用说明](plugins/douyin-video-download/README.md)。

### Agent Chatroom 聊天室

需要 Node.js 22.12 或更新版本，客户端启动环境需能找到 `node`。插件包含可直接运行的 MCP 程序、HTTP 服务和人类参与的网页，运行时无需 `npm install`。源码、锁文件、构建脚本和测试均位于 `plugins/agentchatroom`，可独立开发，不依赖 CardBush 主仓库。

在 CardBush 中连接插件后，首次本地房间操作会自动启动服务，也可通过 `chatroom_service` 直接启动、查看、停止或重启。服务跟随创建它的插件连接关闭，历史保留，下次启动恢复，无需手动运行脚本。独立外部服务和远程 MCP 连接仍受支持。聊天码、房主管理、人类邀请及 `await` 行为见[聊天室说明](plugins/agentchatroom/README.md)。

### 衣服设计

需要 Node.js 22 或更新版本，客户端启动环境需能找到 `node`。插件包含源码、可直接运行的 MCP 程序和 WASM 矢量渲染器，安装后运行无需 `npm install`。先用自然语言设计衣身、领口、袖子、口袋等部位，查看和修改可编辑的矢量稿；可选参考图、部位锁定和历史恢复均保存在插件主机的数据目录。

设计阶段不调用图片生成。用户确认具体版本后，宿主复用已有 Seedream（`volcengine-plugins`）或其他生图能力生成整体效果，再由宿主视觉模型对照原设计分析偏差并继续改款。插件不提供生产纸样或合体仿真，也不内置模型凭据。

通过 `garment-presentation` Skill 可将已保存的设计版本整理为讲解方案，导出矢量 PDF、含 SVG 与可编辑文字的 PPTX、离线 HTML 和讲稿。已通过 17 项自动化测试、交互面板和导出回归，以及 CardBush ZIP 安装验证。真实付费生图的款式还原质量尚未验收，详见[插件说明](plugins/garment-designer/README.md)及[验证记录](plugins/garment-designer/TESTING.md)。

### 资料库

需要 Node.js 22.13 或更新版本，客户端启动环境需能找到 `node`。插件自带可直接运行的 MCP 服务、文档解析器和管理面板，无需现场 `npm install`。在本机或云 Agent 上按领域、部门和场景建库，导入 PDF、DOCX、Markdown、TXT、HTML、CSV，通过中文全文检索与领域同义词返回少量原文和稳定出处。

数据默认保存在插件运行账户的 `~/.cardbush-knowledge/`，独立于安装目录。支持增量版本、归档恢复、只读连接和可信连接的库范围配置；普通检索不会自动弹出面板。当前为单个可信租户的 demo，不含 OCR、向量检索或企业身份认证。初始化方式、云端数据目录及能力边界见[资料库说明](plugins/knowledge-library/README.md)。

## 更新市场

```shell
codex plugin marketplace upgrade cardbush-plugins
```

然后在插件页面更新或重新安装需要的插件。市场目录升级不会自动替换通过 `pip` 安装的 Python 服务代码。

## 仓库结构

```text
.agents/plugins/marketplace.json
plugins/
  volcengine-plugins/
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
  douyin-video-download/
    .codex-plugin/plugin.json
    .mcp.json
    server.py
    requirements.txt
    README.md
    scripts/build_release.py
    scripts/test_mcp.py
    skills/douyin-video-download/
      SKILL.md
      agents/openai.yaml
      scripts/download_video.py
      scripts/test_download_video.py
      scripts/test_cookies.py
  agentchatroom/
    .codex-plugin/plugin.json
    .mcp.json
    plugin.json
    mcp.json
    package.json
    package-lock.json
    build.mjs
    runtime/cli.mjs
    src/
    web/
    skills/agentchatroom/
    test/
  logic-memory/
    .codex-plugin/plugin.json
    .mcp.json
    runtime/cli.mjs
    skills/logic-memory/
    src/
    test/
  garment-designer/
    .codex-plugin/plugin.json
    .mcp.json
    package.json
    package-lock.json
    dist/
    src/
    ui/
    skills/garment-design/
    skills/garment-presentation/
    scripts/
    test/
    third-party/
  knowledge-library/
    .codex-plugin/plugin.json
    .mcp.json
    plugin.json
    mcp.json
    package.json
    package-lock.json
    runtime/
    src/
    ui/
    skills/knowledge-library/
    scripts/
    test/
    assets/
    THIRD_PARTY_NOTICES.txt
```

市场条目的 `source.path` 相对于仓库根目录，保持为 `./plugins/<插件名>`。每个插件保留原始包内版本、源码和现有资源；发布整理将兼容清单的默认提示统一为数组，并补充仓库链接。原始 ZIP 不需要作为市场入口上传。
