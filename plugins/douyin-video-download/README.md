# 抖音视频下载

**获取 Cookie → 提供文件路径 → 下载视频。** 一个 MCP 工具，三个参数，不再配置扫码登录、访客模式或候选数量。

## 获取 Cookie

1. 在自己的 Chrome / Edge 中登录 [抖音网页版](https://www.douyin.com/)，确认目标视频能播放。
2. 按 F12，打开 **Network / 网络**，刷新视频页。
3. 选一个发往 **www.douyin.com** 的请求，展开 **Headers → Request Headers**，复制 `Cookie` 的完整值。请勿复制 `Set-Cookie` 或整段 cURL。
4. 用记事本等编辑器保存为 UTF-8 文本文件，例如用户私有目录下的 `cookies.txt`。内容是一行 `name=value; name2=value2`，也接受前面的 `Cookie:` 标签。

请求头的位置见 [Chrome DevTools 官方说明](https://developer.chrome.com/docs/devtools/network/reference#headers)。无需安装浏览器扩展。已有的 Netscape `cookies.txt`、JSON 数组或含 `cookies` 数组的导出文件继续支持。

Cookie 文件留在私有目录；工具只接收路径，结果不回显 Cookie。云 Agent 使用服务器上的文件路径，可通过现有上传或 SSH 通道传入文件。不要把本机 `C:/...` 路径直接交给 Linux 服务。

## 调用工具

`douyin_download` 只需：

| 参数 | 含义 |
| --- | --- |
| `share_text` | 一条抖音链接或包含它的完整分享文案 |
| `cookies_file` | 用户提供的 Cookie 文件绝对路径，必填 |
| `output_dir` | 保存目录的绝对路径 |

```json
{
  "share_text": "https://www.douyin.com/video/视频ID",
  "cookies_file": "/home/me/private/cookies.txt",
  "output_dir": "/home/me/downloads"
}
```

工具内部解析目标视频，最多比较三个候选（每个最大 1 GiB，单次网络请求超时 30 秒），按实测分辨率、码率选择最佳已检查源。返回文件路径、大小、SHA-256、分辨率、时长及音轨信息。已有同名文件不覆盖，未完成临时文件会清理，不转码、不修改签名或水印参数。

缺 Cookie 时直接提示准备文件，不发起访客请求。过期或拒绝访问时更新 Cookie；限流时停止。Cookie 无法代替网页 JavaScript、额外验证或访问权限，`metadata_unavailable` 仍可能发生，此时没有下载成功。只在 `status=downloaded` 时交付文件，不承诺所有视频可用或全站原画。

原始 Cookie 请求头仅发送给 HTTPS 的 `www.douyin.com`；JSON/Netscape 文件保留其域名、路径和有效期。Cookie 不发送给第三方 CDN，也不自动读取浏览器配置或旧扫码状态。

## 安装与 CLI

从插件市场安装，或导入完整目录。Python 3.10+；MCP 依赖用配置中的同一 Python 安装：

```shell
python -m pip install -r requirements.txt
```

重新加载插件即可。`.mcp.json` 的 `python` 可按环境改为 Python 的绝对路径或 `python3`。不再需要 Playwright、Chromium 或扫码依赖。

没有 MCP 时可用仅依赖 Python 标准库的 CLI：

```powershell
python -X utf8 ./skills/douyin-video-download/scripts/download_video.py '分享文案或链接' --cookies-file 'Cookie文件绝对路径' --output-dir '保存目录绝对路径'
```

多行或含复杂引号的分享文案先保存到 UTF-8 文件，用 `--input-file` 代替位置参数。输出目录默认当前目录下的 `downloads`。退出码 0 表示已下载，2 表示错误。

升级后只暴露 `douyin_download`；扫码三个工具、单独解析及候选下载工具已移除。`guest` 和清晰度/候选数量等公开选项已移除。旧扫码文件不会被删除；如要继续使用，可显式将其绝对路径传为 `cookies_file`。

## 验证

```shell
python -I -B skills/douyin-video-download/scripts/test_download_video.py
python -I -B skills/douyin-video-download/scripts/test_cookies.py
python -X utf8 scripts/test_mcp.py
```

离线测试使用虚构 Cookie 和合成 MP4，检查文件传入、域名隔离、重定向、下载校验与错误脱敏；MCP 测试运行真实 stdio 服务，确认仅一个工具且无 Cookie 不联网。离线通过不代表真实账号和抖音线上页面已验证。
