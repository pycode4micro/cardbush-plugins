# 音乐与歌曲生成：v5.0

截至 2026-09-18 官方文档，歌曲最新可选模型为 `v5.0`，纯音乐也已全面升级至 `v5.0`。插件显式发送该版本，不接受旧版本或 `latest` 之类不明确的别名。原生 API 的查询参数 `Version=2024-08-12` 是接口版本，与生成模型版本不同。

## 配置

在 [AI 音乐控制台](https://console.volcengine.com/ai-music/product) 开通所需的按时长后付费或资源包预付费服务，再配置以下环境变量。Windows 也支持读取当前用户环境变量；显式设置的进程变量（包括空值）优先。`ARK_READ_USER_ENV=0` 关闭整个插件的用户注册表回退。

| 变量 | 用途 |
| --- | --- |
| `VOLCENGINE_ACCESS_KEY_ID` | 火山 IAM AccessKeyID |
| `VOLCENGINE_SECRET_ACCESS_KEY` | 对应的 SecretAccessKey |
| `VOLCENGINE_SESSION_TOKEN` | 可选，仅临时 STS 凭据使用 |
| `VOLCENGINE_MUSIC_TIMEOUT_SECONDS` | 请求超时，默认 60 秒，允许 1～300 秒 |

使用具备音乐服务权限的账户或子账户；仅有密钥并不代表已开通服务。旧接入指南写“仅企业”，较新的计费页写明个人用户可付费购买试用，实际开通以账户控制台为准。无需把密钥写进 `.mcp.json`、项目文件或工具参数；诊断只显示是否配置及来源。

鉴权使用 HMAC-SHA256，固定域名 `open.volcengineapi.com`、区域 `cn-beijing`、服务 `imagination`。所有请求使用 HTTPS，AK/SK 不会发给音频 CDN。该能力与 `ARK_API_KEY`、`MEDIAKIT_API_KEY` 独立。

## 七个 MCP 工具

| 工具 | 功能 |
| --- | --- |
| `music_capabilities` | 本地能力、请求 schema、凭据状态和官方来源 |
| `music_preview_song` | 免费校验人声歌曲并预览请求 |
| `music_preview_bgm` | 免费校验纯音乐及分段时长 |
| `music_create_song` | 提交一次人声歌曲任务，消耗所选服务额度 |
| `music_create_bgm` | 提交一次纯音乐任务，消耗所选服务额度 |
| `music_get_task` | 查询同一个任务，获取状态、音频、歌词、Captions、StyleInfo |
| `music_download_task` | 下载成功任务原件，可同时保存歌词/字幕 JSON |

预览与创建工具均接收 `request` 和可选 `billing_mode`，后者为 `postpaid`（默认）或 `prepaid`。预览保留歌词与创作描述便于核对，回调、存储和水印元数据脱敏。没有凭据也可启动 MCP 和预览。

## 用歌词生成歌曲

先传给 `music_preview_song`，在用户已授权生成时用相同参数调用 `music_create_song`：

```json
{
  "billing_mode": "postpaid",
  "request": {
    "ModelVersion": "v5.0",
    "Lyrics": "[verse]\n晚风吹过安静的街\n路灯点亮远方的夜\n[chorus]\n让星光陪你慢慢回家\n把今天的心愿轻轻放下",
    "Prompt": "温暖治愈的中文民谣，木吉他伴奏，柔和女声，自然收尾。",
    "Duration": 60,
    "Lang": "Chinese",
    "VodFormat": "wav"
  }
}
```

`Lyrics` 和 `Prompt` 至少提供一个，v5.0 可同时传入。自带歌词保留原文；只给 `Prompt` 时由服务创作歌曲。中文歌词限 5～700 字符，英文限 5～2000 字符；`Lang` 支持 `Chinese`、`English`、`Cantonese`。`Duration` 为 30～240 秒，可省略由服务决定；此时不输出固定时长的费用参考。

`VodFormat` 为 `wav`（默认）或 `mp3`。可选字段还有 `CallbackURL`、`TosBucket`、`ImplicitWaterMark`、`AigcWatermark` 和 `SkipCopyCheck`；版权检查默认保留（`SkipCopyCheck=false`）。不自动更改水印选项。

v5.0 官方标注旧的 `Genre`、`Mood`、`Gender`、`Timbre`、`GenreExtra`、`Key`、`Kmode`、`Tempo`、`Instrument`、`Scene` 字段不生效，插件会在提交前拒绝这些字段。创作意图写入 `Prompt`，不冒充对旧参数提供精确控制。独立歌词生成 `GenLyrics` 文档目前只列出 v4.0 且需要资源包赠送权限，本次不封装该旧模型。

## 纯音乐与分段

先调用 `music_preview_bgm`，获授权后用同一参数调用 `music_create_bgm`：

```json
{
  "request": {
    "Version": "v5.0",
    "Text": "温暖的商品展示背景音乐，钢琴与木吉他，节奏轻快，适合服装短片，结尾自然收束。",
    "Duration": 60,
    "EnableInputRewrite": false,
    "Segments": [
      {"Name": "intro", "Duration": 10},
      {"Name": "verse", "Duration": 20},
      {"Name": "chorus", "Duration": 20},
      {"Name": "outro", "Duration": 10}
    ]
  }
}
```

`Text` 为中文描述。`Duration` 默认 60 秒，v5.0 允许 30～120 秒；较旧计费页仍写 60 秒以内，此处遵循新 API 参数文档。`Segments` 可省略；名称为 `intro/verse/chorus/inst/bridge/outro`，每段 5～120 秒、合计 30～120 秒，单段也必须达到 30 秒。时长优先级为分段合计 > `Text` 中指定的时长 > 外层 `Duration`。插件不猜测描述里的时长，实际计费仍以生成结果为准。

BGM 不支持歌曲的 `Lyrics`、`VodFormat`、`ModelVersion` 等字段，也不使用旧版的 `Genre/Mood/Instrument/Theme`；用 `Text` 描述风格。`EnableInputRewrite` 默认关闭，只有显式开启才让服务改写输入。

## 查询与保存

创建返回 `task.id`，表示任务已提交。使用 `music_get_task({"task_id":"实际任务ID"})` 查询；官方 `Status` 依次为 0 等待、1 处理中、2 成功、3 失败，插件对应 `queued/running/succeeded/failed`。查询响应 `Code=0` 不代表生成成功，未知状态不会被当作成功。

成功后调用：

```json
{
  "task_id": "实际任务ID",
  "dest": "C:/music/song.wav",
  "metadata_dest": "C:/music/song.json"
}
```

两个路径均须为不存在的新文件，`metadata_dest` 可省略。返回文件大小、SHA256、实际容器和可读取的音频头信息。JSON 保存歌词、服务原生 Captions/StyleInfo 和时长，不保存签名 URL。官方未定义固定 Captions 内部格式，本工具原样保留，不猜测时间戳或转换为 LRC/SRT。

官方提示视频云链路可能返回 MP4 等容器；工具原样保存并检查扩展名是否匹配，不转码。音频头检查不能替代完整解码和听检。需要 MP3/WAV 交付时，可在核实真实格式后另存转换版。

输出 URL 按官方文档用于转存而非直接在应用中引用，文档标注有效期一年，建议及时下载。API 凭据和签名请求头不传给 CDN；下载失败只允许有限 GET 重试，绝不重新生成歌曲。元数据保存失败时返回已保存音频路径，后续查询同一个任务即可取回歌词。

## 计费与失败恢复

| 类型 | 后付费 Action | 预付费 Action |
| --- | --- | --- |
| 歌曲 | `GenSongForTime` | `GenSongV4` |
| 纯音乐 | `GenBGMForTime` | `GenBGM` |

`GenSongV4` 是官方预付费 Action 名称，请求体仍发送 `ModelVersion=v5.0`，不代表调用 v4。两种计费模式独立，不会相互抵扣；插件不按余额或权限自动切换。

公开计费页列出 standard 歌曲和 BGM 后付费均为 0.002 元/秒，但没有分别列出模型版本价格。预览金额只作为这一公开价目的参考，`confirmed_v5_price=false`，不保证账号实际 v5.0 账单。预付费不计算按秒金额；开通时核对控制台产品与价格。

本地参数或凭据错误明确标记未发付费请求；网络请求发出后，超时、异常响应或缺少 TaskID 都可能导致结果不确定。保留 RequestId、已有 TaskID，查询同一个任务或控制台记录，不自动重试创建、不降级、不更改计费方式。成功提交或失败响应均不推断实际扣费/退款。

## 验证范围与官方来源

离线测试使用模拟 HTTP 和合成 WAV，覆盖签名、参数限制、计费 Action、状态映射、凭据缺失、失败不重提、签名 URL 原样下载和歌词元数据保存。签名黄金向量独立取自官方 Python 签名示例，使用假 AK/SK 与固定日期；未使用真实付费生成作为自动化测试。实际账号授权、生成音质及歌词演唱准确度仍须出歌后验证。

0.7.0 完整插件测试：377 项通过，包含实际 MCP stdio 握手、32 项工具清单与音乐预览；插件清单和 music-generation Skill 格式验证通过。

- [人声歌曲](https://www.volcengine.com/docs/84992/2091679)
- [纯音乐](https://www.volcengine.com/docs/84992/2100970)
- [任务查询与返回歌词](https://www.volcengine.com/docs/84992/2100960)
- [公共字段与鉴权](https://www.volcengine.com/docs/84992/1967910)、[官方签名示例](https://www.volcengine.com/docs/6369/67269)
- [产品计费](https://www.volcengine.com/docs/84992/1404661)、[独立歌词接口](https://www.volcengine.com/docs/84992/2100963)
