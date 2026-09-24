---
name: douyin-video-download
description: 使用用户提供的 Cookie 文件，从抖音分享文案或链接下载单条视频；缺少 Cookie 时指导从已登录浏览器获取。适用于抖音视频下载和 Cookie 使用，不负责账号登录或批量抓取。
metadata:
  version: "0.2.0"
---

# 抖音视频下载

只需 **分享链接、Cookie 文件、保存目录**。已提供可用文件时直接调用 `douyin_download`；未提供时先讲下面的获取步骤，不试访客下载或扫描浏览器账号数据库。

## 获取 Cookie

1. 用户在自己的 Chrome / Edge 打开 `https://www.douyin.com/`，完成登录并确认目标视频能正常播放。
2. 按 F12 打开开发者工具，进入 **Network / 网络**，刷新视频页。
3. 选一个发送到 **www.douyin.com** 的请求，在 **Headers → Request Headers** 中复制 `Cookie` 的完整值。不是 `Set-Cookie`，也不是整条 cURL 命令。
4. 将值保存为本机 UTF-8 文本文件，例如 `cookies.txt`，只需一行 `name=value; name2=value2`。然后提供该文件的绝对路径，不需要把 Cookie 原文发进聊天。

也兼容已有 Netscape 或浏览器导出的 JSON Cookie 文件。文件留在用户自己的私有目录，不提交仓库。云端插件需要将文件通过现有上传或 SSH 通道放到服务器，再传服务器路径；本机路径不能直接给云端工具。

## 下载

调用唯一工具 `douyin_download`：

```json
{
  "share_text": "用户给出的分享链接或完整文案",
  "cookies_file": "/absolute/path/cookies.txt",
  "output_dir": "/absolute/path/downloads"
}
```

只传文件路径，不读取或回显 Cookie 内容。保存目录用用户指定的位置；未指定时用当前工作目录下的 `downloads`。工具自行解析目标、比较最多三个候选并校验实际 MP4、分辨率、时长和大小，不覆盖已有同名文件。不要让用户配置候选数、访客模式或浏览器依赖。

仅当返回 `status=downloaded` 才交付 `file_path` 的实际文件链接。按返回值报告分辨率和时长；不把“已检查候选中最佳”说成全站原画或保证无水印。

- `cookies_required` / `cookie_file_error` / `invalid_cookies`：按获取步骤准备正确文件和路径。
- `cookies_expired` / `access_required`：请用户在浏览器确认能播放并更新 Cookie；不要循环重试。
- `metadata_unavailable`：Cookie 已提供，但工具没拿到视频地址；如实报告页面渲染、额外验证或改版的限制，不把它说成缺 Cookie，不另写取源脚本或接第三方解析站。
- `rate_limited`：停止本次下载；`output_exists`：保留原文件，使用其他保存目录。

Cookie 是必要输入，不是下载成功保证。只处理用户有权获取的单条视频，不绕过验证码、付费或访问限制。插件不转码、不改签名和水印参数。

MCP 未挂载时，同一下载实现可直接运行；路径相对本 Skill 目录解析。复杂分享文本先存 UTF-8 文件，用 `--input-file` 替代位置参数，避免 shell 解释：

```powershell
python -X utf8 '<Skill目录>/scripts/download_video.py' '分享链接' --cookies-file '<Cookie文件绝对路径>' --output-dir '<保存目录绝对路径>'
```
