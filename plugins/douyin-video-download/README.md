# 抖音视频下载 / Douyin Video Download

0.2.0 同时提供 **Skill、独立 CLI 和 3 个 MCP 工具**，下载单条抖音视频并比较候选源清晰度。

## 安装

从已配置的 Cardbush 插件市场安装 `douyin-video-download`，或导入完整插件目录。CLI 使用 Python 3.10+ 标准库，无需安装包；启用 MCP 时，在插件目录运行：

```shell
python -m pip install -r requirements.txt
```

重新加载插件。`.mcp.json` 使用 `${PLUGIN_ROOT}/server.py`，可从任意工作目录运行。插件不需要 API Key、其他项目或对象存储。不要另外复制 Skill 到全局目录。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `douyin_resolve` | 从公开分享页解析目标视频 ID 对应的候选源。 |
| `douyin_download` | 实际下载比较候选，校验 MP4/音视频轨道、体积、分辨率、时长和 SHA-256，保存最佳已核实源。 |
| `douyin_download_candidates` | 接受目标页面实际观察到的候选 URL，解决浏览器取源后缺少统一下载和质量校验的问题。 |

`min_short_side=1080` 会拒绝 576p/720p 替代源；默认 0 不强制门槛。`max_candidates` 默认 3，允许 1..8。比较需要实际下载这些候选，各候选分别受 `max_mb`（默认 1024）限制，可能消耗多个文件的流量。

候选源的页面元数据只用于排序，最终用落盘文件的实际分辨率优先、码率次优选择。不把网页当前播放的 `video.currentSrc` 当作原画证明，也不把大文件自动等同于高画质。

## JS 渲染页面

MCP 本身不执行浏览器 JavaScript。公开分享页返回 `metadata_unavailable` 时，可通过本次已授权的可用浏览器观察同一目标页面的真实候选 URL，再调用候选下载工具。每项为：

```json
{"url":"实际观察到的媒体URL","width":1080,"height":1920,"source":"target-page"}
```

`width`、`height`、`bitrate`、`bytes`、`source` 均可选；URL 必填。`page_url` 必须含目标视频 ID。调用方负责确认候选属于同一视频，容器检查无法证明内容归属。不推测或修改 file_id、签名和水印参数，不读取浏览器 Cookie。媒体访问仍被拒绝时停止；没有浏览器也仍可使用公开页解析或已有本地文件。

## 独立 CLI

```powershell
python -X utf8 ./skills/douyin-video-download/scripts/download_video.py '〈分享文案或链接〉' --output-dir '〈输出目录〉' --min-short-side 1080
```

| 参数 | 用途 |
| --- | --- |
| 位置参数 / `--input-file` | 一条链接或完整分享文案；二者不同时使用。 |
| `--candidates-file` | 浏览器候选 JSON 文件，与文案、input-file 三选一。 |
| `--output-dir` | 默认 `./downloads`。 |
| `--resolve-only` | 只解析，不下载；结果可能带临时签名。 |
| `--timeout` | 单次网络超时，默认 30 秒。 |
| `--max-mb` | 每个候选文件上限，默认 1024 MiB。 |
| `--min-short-side` | 最小短边，默认 0；高清源可用 1080。 |
| `--max-candidates` | 最多实际比较数，默认 3，范围 1..8。 |

候选文件结构为 `{"page_url":"目标完整视频页","candidates":[{"url":"实际观察到的地址"}]}`。复杂签名 URL 推荐走 JSON 文件或 MCP 结构化参数，不拼接 shell 命令。

成功退出码 0，错误退出码 2。只有 `status=downloaded` 表示已落盘；`resolved` 仅是解析结果。`quality.checked_candidates` 报告实际测量，未达到最小分辨率时返回 `quality_too_low` 并且不留下低清成片。`all_candidates_checked=false` 表示仍有线路未核实，不承诺全站最高画质。临时文件清理，已有文件不覆盖。

按服务端字节保存，不转码、不静音、不变速。支持单条授权可获取的视频，不支持图文、直播或批量合集。容器和轨道检查不等于完整解码或人工内容验证；高质量后续处理应再核对实际画面。

## 验证

```powershell
python -I -B ./skills/douyin-video-download/scripts/test_download_video.py
python -X utf8 ./scripts/test_mcp.py
```

64 项离线下载测试覆盖低清优先陷阱、伪高分辨率元数据、最小分辨率拒绝、下载不完整及不覆盖；MCP 测试实际启动服务并发现 3 个工具。没有在测试中访问登录态、取 Cookie 或对真实抖音链接执行下载，不能据此保证所有线上链接可用。

## English

Version 0.2.0 includes a portable standard-library CLI/Skill and an optional three-tool MCP server. Install `requirements.txt` using the Python interpreter configured in `.mcp.json`, then reload the plugin. CLI-only usage needs no pip packages.

Resolve a public share page, or supply actual candidate URLs observed in an authorized browser on that same video page. The server does not render JavaScript or access browser cookies. It never invents file IDs or changes watermark/signature parameters.

Candidate metadata ranks attempts; saved MP4 dimensions and bitrate determine the best verified download. Set `min_short_side=1080` to reject low-resolution substitutes. The default comparison limit is three downloads, with an explicit maximum of eight and a per-file size limit. This may use more bandwidth than downloading only the default player stream. Resolution/bitrate are not proof of original quality or video identity.

Outputs report file bytes, hash, resolution, duration, tracks and candidate verification. Existing files are never overwritten, incomplete downloads are removed, and low-quality-only results fail when a minimum is specified. Container checks are not full decoding or visual validation. Signed URLs must not be published.
