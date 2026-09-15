# MiniMax Music · 动画音乐工作室

为 Codex 封装 MiniMax 歌曲、纯音乐、歌词和翻唱能力。包含 **11 个 MCP 工具、2 个技能、后台任务、单文件运行程序和原创 logo**。这是个人集成插件，非 MiniMax 或 OpenAI 官方发布。

![Logo](assets/logo.png)

## 功能

| 能力 | 工具 |
|---|---|
| 歌词配曲与演唱、描述直接生成歌曲 | `generate_song` |
| 无人声 BGM / 配乐 | `generate_instrumental` |
| 歌词创作、固定歌名 | `write_lyrics` |
| 歌词改写、续写 | `edit_lyrics` |
| 参考歌曲的歌词和段落分析 | `preprocess_cover` |
| 风格翻唱、改词翻唱、两步翻唱 | `generate_cover` |
| 配置与能力检查 | `music_capabilities` |
| 参数预览，不调用 API | `preview_music_request` |
| 查询任务、找回历史、重试下载 | `get_music_job`、`list_music_jobs`、`download_result` |

云端支持 `music-3.0`、`music-2.6`、`music-cover`；支持 WAV / MP3 / PCM、采样率与码率、URL / Hex、SSE 流式接收、非流式音频末尾水印。本地参考音频直接转换 Base64 传给 MiniMax。输出保存到指定的绝对目录，每个任务使用独立子目录。

技能 `music-studio` 负责音乐操作；`animation-soundtrack` 负责动画 OP/ED、场景 BGM 和配乐清单。没有实现或宣称原生分轨、歌手音色克隆、精确卡点、任意续曲、歌词逐字时间轴或保证无缝循环。

## 运行要求

- Node.js **22 或更新版本**，`node` 可从 PATH 找到。
- 运行只使用已打包的 `scripts/minimax-music.mjs`，**无需 npm install**。
- 云端需要具有音乐权限的 MiniMax API Key；自部署模式需要已经运行的 Music 3 / SGLang-Omni 服务。

**账号限制（2026-09-15 核对）：** 自 2026-08-20 起，官方音乐/歌词付费 API 不再向新用户开放；历史付费用户可继续使用，免费音乐 API 已停止。网页 Audio 账号不等同于音乐 API 权限。本插件无法解除上游限制。[官方公告](https://platform.minimax.cn/docs/api-reference/music-generation)

## 安装到 Codex

从 Cardbush Plugins 市场安装：

```shell
codex plugin marketplace add pycode4micro/cardbush-plugins --ref main
codex plugin add minimax-music@cardbush-plugins
```

已添加该市场时，先运行 `codex plugin marketplace upgrade cardbush-plugins` 刷新目录，再安装插件。安装后新建 Codex 任务。若已启用个人市场的同名插件，请在插件管理中选择一个来源，避免重复启用。

此方式只要求 Node.js 22 或更新版本和 Codex CLI；业务凭据仍需按下方说明单独配置。

### 本地开发安装（可选）

使用随包的 `install.ps1` 注册到默认个人 marketplace 并安装。安装脚本调用 Codex 自带的 `plugin-creator` 系统技能和 Python 3（优先自动使用 Codex 内置 Python）；这些仅用于注册安装，插件运行只需要 Node.js。它把插件复制到用户的 `plugins/minimax-music`，保留其他插件与配置。首次安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./install.ps1
```

安装后新建一个 Codex 任务以加载技能和工具。插件通过 GitHub 市场分发，在用户本机运行 stdio MCP 服务。

## 配置云端

不要把密钥放入聊天、插件源码、请求 JSON 或 outputs。可以通过启动 Codex 前设置的 `MINIMAX_API_KEY` 环境变量提供密钥，也可把密钥单独保存在个人私有文件中，并引用该文件：

```powershell
node ./scripts/minimax-music.mjs setup --backend cloud --region cn --key-file 'C:/private/minimax-api.key'
node ./scripts/minimax-music.mjs doctor
```

上面的 key 文件需要自行建立，内容仅为密钥，路径替换为自己的实际私有路径。`setup` 不接收明文 key 参数，配置只保存文件路径。默认配置位置为 `~/.config/minimax-music/config.json`。密钥文件不随插件打包或复制。国际账号使用 `--region global`。

环境变量优先于文件配置：

| 变量 | 用途 |
|---|---|
| `MINIMAX_API_KEY` / `MINIMAX_API_KEY_FILE` | 云端凭据（二者都有时 API_KEY 优先） |
| `MINIMAX_REGION` | `cn` 或 `global` |
| `MINIMAX_MUSIC_CONFIG` | 自定义配置文件绝对路径 |
| `MINIMAX_MUSIC_BACKEND` | `cloud` 或 `local` |
| `MINIMAX_LOCAL_BASE_URL` | 自部署服务基础地址 |
| `MINIMAX_LOCAL_API_KEY` | 自部署服务自己的可选密钥，与云端密钥隔离 |
| `MINIMAX_MUSIC_DATA_DIR` | 任务状态目录；默认用 PLUGIN_DATA 或用户数据目录 |
| `MINIMAX_TIMEOUT_SECONDS` | 单次请求超时，默认 900，范围 1–3600 |

`doctor` / `music_capabilities` 不收费，不会联网验证余额或权限，也不会打印密钥。不要把“已配置”理解成“已验证能出歌”。

## 连接自部署 Music 3

```powershell
node ./scripts/minimax-music.mjs setup --backend local --local-url http://127.0.0.1:8000
```

适配官方模型卡展示的 SGLang-Omni `/v1/audio/speech`，支持歌曲与纯音乐，固定非流式 32 kHz 立体声 WAV。支持 `seed`、`duration_seconds`（最大长度，1–300 秒）。该模式没有专有歌词/翻唱接口，不自动下载模型、安装 CUDA 或部署服务器。[部署文档](https://huggingface.co/MiniMaxAI/MiniMax-Music3)

## 使用示例

在 Codex 中说：

- “用 MiniMax 为这部热血动画做一首中文主题曲，按我提供的歌词生成。”
- “生成一段适合悬疑对白的纯音乐 BGM，低动态，不要人声。”
- “把这首原创歌曲分析后改成轻柔的片尾曲，保留副歌歌词。”

也可通过 CLI 调用。把下列 JSON 保存为工作区 `work/song.json`，将输出路径改为自己的绝对路径：

```json
{
  "output_dir": "C:/animation/outputs",
  "title": "越过长夜",
  "request_id": "episode01-opening-v1",
  "prompt": "中文动画片头曲，流行摇滚，坚定而温暖的青年男声，主歌克制，副歌加入电吉他与弦乐，表达少年与同伴共同面对未知。",
  "lyrics": "[Verse]\n风掠过沉睡的城\n你点亮远方的灯\n[Chorus]\n越过长夜 向黎明奔跑\n把每个约定 写进破晓"
}
```

```powershell
node ./scripts/minimax-music.mjs call generate_song ./work/song.json
```

工具立即返回本地 job_id。用 `get_music_job` 查询，或 `list_music_jobs` 找回历史；每个工具的 JSON 参数模式可通过 MCP `tools/list` 查看。离线预览使用 `preview_music_request`，参数是 `{ "operation": "song", "arguments": { ... } }`。

## 保存与失败恢复

- 成功文件夹包含音频 / 歌词、`request.json`（已去除参考音频字节）、`result.json`，可追踪生成参数。
- `request_id` 防止不确定的工具重试重复提交。同一 ID 的参数改变会被拒绝；新的候选歌曲要用新 ID。
- `download_failed` 表示生成成功但下载失败，使用 `download_result` 取回，不重新生成。音频链接和翻唱特征约 24 小时有效。
- `unknown` 表示网络或进程中断，生成/扣费结果不确定。插件不自动重发付费 POST。
- SSE 是接收与保存流式音频，并非 Codex 内实时播放器。纯音乐、歌词和段落等生成控制仍需验收，未保证每项指令精准实现。

## 开发与验证

```powershell
npm ci
npm run build
npm test
```

源码在 `src/`，运行包是 `scripts/minimax-music.mjs`。测试用本地模拟服务验证云端协议、任务恢复与真正的 MCP stdio 交互，不发送付费生成请求。实际音质与账号权限需要配置可用凭据后单独验证。

## 规范与来源

- [OpenAI 插件打包规范](https://developers.openai.com/plugins/build/plugins)：根目录 Agent Plugins 1.0 `plugin.json`、`mcp.json`、`skills/`、OpenAI 展示扩展；保留 `.codex-plugin/plugin.json` 与 `.mcp.json` 兼容清单。
- [音乐接口](https://platform.minimax.cn/docs/api-reference/music-generation)、[歌词接口](https://platform.minimax.cn/docs/api-reference/lyrics-generation)、[翻唱前处理](https://platform.minimax.cn/docs/api-reference/music-cover-preprocess)。
- 原创插件图标位于 `assets/`，不是 MiniMax 官方商标；浅色、深色和小图标均已在清单中声明。

代码与原创图标为 MIT；依赖许可见 `THIRD_PARTY_LICENSES.txt`。第三方模型、API 和输出的使用条件由对应提供商规定。
