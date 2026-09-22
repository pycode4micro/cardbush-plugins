# 抖音视频下载 / Douyin Video Download

提供 **Skill、独立 CLI 和 6 个 MCP 工具**，支持 Linux 服务器无桌面扫码登录、自动保存并复用登录会话，以及单条视频下载和清晰度校验。也支持用户指定的本地 Cookie 文件。

## 安装

从已配置的 Cardbush 插件市场安装 `douyin-video-download`，或导入完整插件目录。CLI 使用 Python 3.10+ 标准库，无需安装包；启用 MCP 时，在插件目录运行：

```shell
python -m pip install -r requirements.txt
```

重新加载插件。`.mcp.json` 使用 `${PLUGIN_ROOT}/server.py`，可从任意工作目录运行。插件不需要 API Key、其他项目或对象存储。不要另外复制 Skill 到全局目录。

扫码登录额外使用 Playwright 和无头 Chromium。Linux 上可在插件目录安装到独立虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install --with-deps chromium
```

第三步可能需要管理员安装系统依赖；运行插件使用支持 Chromium 沙箱的普通用户。把 MCP 配置的 `command` 指向该虚拟环境 Python 的绝对路径。默认配置写的是 `python`，只有 `python3` 的服务器需要调整。扫码不需要图形桌面、DISPLAY、VNC 或对外开放登录端口。手动 Cookie/访客下载仍只需标准库，不强制安装浏览器。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `douyin_login_start` | 启动独立无头浏览器，返回官方登录面板图片和 `session_id`；用户用手机抖音扫码确认。 |
| `douyin_login_status` | 查询进度和当前二维码；`refresh=true` 刷新，最多三次且不延长总等待时间。 |
| `douyin_login_cancel` | 取消等待、关闭浏览器；保留原有已保存会话。 |
| `douyin_resolve` | 从公开分享页解析目标视频 ID 对应的候选源。 |
| `douyin_download` | 实际下载比较候选，校验 MP4/音视频轨道、体积、分辨率、时长和 SHA-256，保存最佳已核实源。 |
| `douyin_download_candidates` | 接受目标页面实际观察到的候选 URL，解决浏览器取源后缺少统一下载和质量校验的问题。 |

三个下载/解析工具接受可选的 `cookies_file`，值为用户选定的本地 Cookie 文件绝对路径。不提供时自动复用本插件扫码保存的会话；`guest=true` 跳过已保存会话。不把 Cookie 原文放进参数、提示词或日志，不扫描其他浏览器账号配置。

## Linux 服务器扫码登录

1. 调用 `douyin_login_start`。服务器打开官方登录页面，工具把登录面板 PNG 作为 MCP 图片返回；在客户端查看即可，不必访问服务器图片路径。
2. 使用手机抖音扫码，并在手机上确认。图片来自官方网页，插件不构造二维码登录接口或签名。
3. 调用 `douyin_login_status` 查看进度。`starting`、`waiting_scan`、`waiting_confirmation` 不是登录完成；`qr_expired` 可用 `refresh=true` 刷新。
4. 只有 `session_saved` 表示本次登录 Cookie 已出现、登录面板已消失且会话已保存。后续下载自动读取 `~/.douyin-video-download/auth/cookies.json`，无需手动导出 Cookie。Linux 目录权限为 `0700`，文件权限为 `0600`。

默认等待 180 秒，可设为 30 到 600 秒。重复发起会复用当前等待中的会话。取消、超时或失败不覆盖旧会话。保存的 Cookie 由相同系统用户下的插件进程复用，失效后重新扫码；保存会话不等于验证了每个视频的访问权限。

额外验证码会返回 `verification_required` 并停止。本版没有远程交互接管功能，此时可用交互式浏览器登录后导入 Cookie 的备用方式；不自动解验证码。页面改版导致无法识别二维码时返回 `login_ui_unavailable`，不谎报成功。

没有 MCP 客户端时可运行 CLI，并保持进程等待扫码：

```bash
.venv/bin/python skills/douyin-video-download/scripts/qr_login.py --timeout 180
```

CLI 输出登录面板 PNG 的服务器路径；通过已有 SSH 文件通道查看该图片并扫码，过期时重新运行命令。图片在命令结束后清理。MCP 方式直接返回图片，不需要传图片文件。不要公开二维码或登录状态文件。

`min_short_side=1080` 会拒绝 576p/720p 替代源；默认 0 不强制门槛。`max_candidates` 默认 3，允许 1..8。比较需要实际下载这些候选，各候选分别受 `max_mb`（默认 1024）限制，可能消耗多个文件的流量。

候选源的页面元数据只用于排序，最终用落盘文件的实际分辨率优先、码率次优选择。不把网页当前播放的 `video.currentSrc` 当作原画证明，也不把大文件自动等同于高画质。

## JS 渲染页面

下载解析器本身不执行网页 JavaScript；新增浏览器模块用于扫码登录，不是视频取源浏览器。分享页返回 `metadata_unavailable` 时，可通过本次已授权的可用浏览器观察同一目标页面的真实候选 URL，再调用候选下载工具。每项为：

```json
{"url":"实际观察到的媒体URL","width":1080,"height":1920,"source":"target-page"}
```

`width`、`height`、`bitrate`、`bytes`、`source` 均可选；URL 必填。`page_url` 必须含目标视频 ID。调用方负责确认候选属于同一视频，容器检查无法证明内容归属。不推测或修改 file_id、签名和水印参数，不扫描浏览器账号目录。媒体访问仍被拒绝时停止；没有浏览器也仍可使用页面解析或已有本地文件。`metadata_unavailable` 不能证明必须登录；浏览器连接失败也不能用增加 Cookie 解决。

## Cookie 获取与使用

- 优先使用本插件扫码保存的会话；没有时使用访客模式。自动接收官方站点的 `Set-Cookie`，并在分享页跳转、页面解析及匹配的媒体请求中复用；访客 Cookie 不持久化。
- 可选导入用户指定的文件：支持 Netscape `cookies.txt`（含 HttpOnly），以及浏览器导出的 JSON 数组或包含 `cookies` 数组的 JSON。不把文件提交到代码库或打进分发包。
- 导入时只保留 `douyin.com` 和 `iesdouyin.com` 及其子域的有效 Cookie；请求仍按原始域名、host-only、路径、有效期匹配，只通过 HTTPS 发送。抖音 Cookie 不会被转发给其他视频 CDN 域名。
- 不支持无域名的原始 `Cookie:` 字符串，不自动读取其他浏览器配置，不回显 Cookie 值。扫码仅保存本次独立浏览器会话中的官方抖音域名 Cookie。
- `cookies_expired` 表示文件中匹配的 Cookie 全部过期；`cookies_unavailable` 表示没有可用的抖音 Cookie；`invalid_cookies` 表示格式错误。文件中存在未过期 Cookie 不代表服务端仍认可登录状态。401/403 或限流仍停止，不循环刷新或重试。

已在本机导出 Cookie 文件后，可通过路径使用：

```powershell
python -X utf8 ./skills/douyin-video-download/scripts/download_video.py '〈分享链接〉' --output-dir '〈输出目录〉' --cookies-file '〈本地 Cookie 文件路径〉'
```

Cookie 只能补足会话信息，无法执行页面 JavaScript。页面只有 JS 外壳时仍需浏览器提供真实候选地址；不能保证加 Cookie 后即可下载。

## 独立 CLI

```powershell
python -X utf8 ./skills/douyin-video-download/scripts/download_video.py '〈分享文案或链接〉' --output-dir '〈输出目录〉' --min-short-side 1080
```

| 参数 | 用途 |
| --- | --- |
| 位置参数 / `--input-file` | 一条链接或完整分享文案；二者不同时使用。 |
| `--candidates-file` | 浏览器候选 JSON 文件，与文案、input-file 三选一。 |
| `--cookies-file` | 可选；用户指定的 Netscape/JSON Cookie 文件，不从浏览器自动读取。 |
| `--guest` | 忽略本插件保存的登录态；与 `--cookies-file` 互斥。 |
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
python -I -B ./skills/douyin-video-download/scripts/test_cookies.py
python -I -B ./skills/douyin-video-download/scripts/test_qr_login.py
python -X utf8 ./scripts/test_mcp.py
python -X utf8 ./scripts/test_qr_browser.py
```

下载和 Cookie 测试覆盖质量、文件完整性、不覆盖、域名/路径/过期限制及跨域重定向。扫码测试覆盖状态、取消、刷新、超时、私有权限及会话复用。浏览器冒烟测试使用本地响应的 HTML 和虚构 Cookie，覆盖真实无头浏览器截图和面板识别，不访问真实账号。MCP 测试启动服务并检查六个工具。真实抖音页面和手机扫码需部署后单独验收，不能用离线测试宣称已完成真实登录。

## English

Includes a standard-library downloader and an optional six-tool MCP server. Install `requirements.txt` with the Python configured in `.mcp.json`. QR login also needs `requirements-browser.txt` and Playwright Chromium/system dependencies. Use a non-root user with browser sandbox support. CLI-only guest/cookie-file downloads need no pip packages.

QR login uses an isolated headless browser on the official site and returns its login-panel image through MCP. The user scans and confirms on their phone. Only `session_saved` means the login cookie appeared, the panel closed and state was saved. Downloads reuse this file automatically; `guest=true`/`--guest` opts out. Other browser profiles are never scanned. The QR browser does not implement JavaScript video-source extraction. Extra verification stops the flow; actual live login remains separate from offline fixture tests.

An optional `cookies_file`/`--cookies-file` imports a selected local Netscape/JSON export. Cookies retain domain, host-only, path and expiry limits and are sent only to matching official Douyin hosts over HTTPS. The plugin never invents file IDs or changes watermark/signature parameters. Saved credentials cannot guarantee access to every video.

Candidate metadata ranks attempts; saved MP4 dimensions and bitrate determine the best verified download. Set `min_short_side=1080` to reject low-resolution substitutes. The default comparison limit is three downloads, with an explicit maximum of eight and a per-file size limit. This may use more bandwidth than downloading only the default player stream. Resolution/bitrate are not proof of original quality or video identity.

Outputs report file bytes, hash, resolution, duration, tracks and candidate verification. Existing files are never overwritten, incomplete downloads are removed, and low-quality-only results fail when a minimum is specified. Container checks are not full decoding or visual validation. Signed URLs must not be published.
