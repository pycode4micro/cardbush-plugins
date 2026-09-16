# 抖音视频下载 / Douyin Video Download

独立的 **Skill 型插件**：给出抖音分享文案或链接，将单条公开视频保存到本地。插件本身不启动 MCP 服务，也不需要另一个项目。

## 安装和管理

通过 Cardbush 云端市场安装（已经添加该市场时，先刷新市场再安装）：

```shell
codex plugin marketplace add pycode4micro/cardbush-plugins --ref main
codex plugin add douyin-video-download@cardbush-plugins
```

- 将完整插件目录导入支持 `.codex-plugin/plugin.json` 和 Skill 的插件客户端，或从已配置的插件市场安装 `douyin-video-download`。
- Codex 本机已添加个人市场条目时，可在插件页找到“抖音视频下载”并安装。安装后新建任务使用。
- 在客户端的插件管理页启用、停用或卸载。不要再单独复制一份内置 Skill 到全局 Skills 目录，以免停用插件后独立副本仍然生效。
- 其他兼容客户端的导入入口取决于该客户端；本包不是 MCP Server，不能作为 MCP 服务器地址添加。

## 环境要求

Python **3.10 或更新版本**、可访问抖音网页及其媒体 CDN 的网络、本地目录写入权限。

不需要 `pip install`、Node.js、FFmpeg、浏览器、Cookie、API Key、`.env` 或对象存储。未配置任何第三方解析服务。离线时不能解析网络视频。

## 使用

安装后直接向助手说：

> 下载这条抖音分享链接中的视频，保存到我指定的目录：〈粘贴链接或完整分享文案〉

也可以显式选择内置 Skill `douyin-video-download`。助手会定位插件自己的下载脚本，返回文件的绝对路径。未指定目录时使用当前工作目录下的 `downloads`。

也可在插件根目录直接运行（Windows PowerShell）：

```powershell
python -X utf8 ./skills/douyin-video-download/scripts/download_video.py '〈抖音分享链接〉' --output-dir './downloads'
```

Linux / macOS 可将 `python` 换成 `python3`。上例中的尖括号内容需要替换，不是实际链接。

| 参数 | 用途 |
| --- | --- |
| 位置参数 | 一条链接或完整分享文案 |
| `--input-file PATH` | 从 UTF-8 文本文件读取文案；与位置参数二选一 |
| `--output-dir PATH` | 保存目录，默认 `./downloads` |
| `--resolve-only` | 只解析，不下载；返回的媒体 URL 可能很快过期 |
| `--timeout 30` | 每次网络操作的超时秒数，不是任务总时限 |
| `--max-mb 1024` | 单个下载文件的大小上限，默认 1 GiB |

输出为 JSON：`downloaded` 才表示下载完成，`resolved` 仅表示解析成功。成功退出码为 `0`，失败为 `2`。下载文件命名为 `<视频ID>.mp4`，不覆盖同名文件；按服务端返回字节保存，不转码、不去音、不变速。

## 限制与隐私

- 仅下载有权获取的公开单条视频，不支持图文、直播或合集批量下载。
- 不保证所有分享链接都能下载；登录、验证、地区限制、已删除内容或网页结构变化都可能导致失败。遇到访问限制会停止，不获取浏览器登录态，也不破解校验或签名。
- 不改写水印参数，不承诺无水印或原画质量。MP4 结构和轨道检查不等于完整解码，也不等于人工检查音画。
- 不需要账号配置；会向抖音官方网页和网页提供的媒体地址发送请求，不向第三方解析站上传数据。
- `--resolve-only` 输出可能带临时媒体签名，不适合原样公开分享。下载文件和运行结果不会收进插件分发包。

离线测试（无需安装测试库）：

```powershell
python -I -B ./skills/douyin-video-download/scripts/test_download_video.py
```

当前版本通过 57 项离线测试及解压到独立目录运行测试。历史分享链接只返回网页、没有目标视频播放地址，因此真实成功下载仍待有效分享链接验证；离线测试不代表所有线上链接可用。

---

## English

A standalone **skill-based plugin** that downloads one public Douyin video from a share link or full share message. It contains a Python script, not an MCP server, and does not depend on another project.

**Install:** Import the complete plugin directory in a client that supports Codex plugin manifests and Skills, or install `douyin-video-download` from your configured marketplace. In Codex, start a new task after installation. Enable, disable or uninstall it in the client's plugin manager. Do not also install a separate global copy of the bundled Skill.

For the Cardbush marketplace, use the two CLI commands in the installation section above. If the marketplace is already configured, refresh it before installing the new plugin.

**Requirements:** Python 3.10+, network access to Douyin and its media CDN, and a writable local output directory. No pip packages, browser, FFmpeg, Node.js, API key, cookies, `.env`, or object storage are required. This plugin cannot be registered as an MCP server.

**Use:** Ask the assistant to download the supplied Douyin share link and name an output directory. It will locate the bundled script relative to the installed Skill. Alternatively, run the command above from the plugin root, replacing the placeholder with a real share link. On macOS/Linux, use `python3` if needed.

The positional argument accepts a link or share message. `--input-file` reads UTF-8 input instead. `--output-dir` defaults to `./downloads`; `--resolve-only` does not download; `--timeout` defaults to 30 seconds per network operation; `--max-mb` defaults to 1024 MiB.

JSON status `downloaded` means the file was saved; `resolved` means metadata only. Exit codes are 0 for success and 2 for failure. Files are named `<video_id>.mp4`, never overwrite an existing file, and preserve the bytes returned by the server.

Only retrieve content you are authorized to access. Login challenges, rate limits, unavailable videos, regional restrictions and website changes may prevent downloading. The plugin does not collect browser sessions, bypass access controls, rewrite watermark parameters, or promise original quality. Container validation checks MP4 structure and tracks, not full decoding or audiovisual content. Resolved media URLs can contain expiring signatures; do not publish them. Offline standard-library tests are included.

Validation status: 57 offline tests and a relocated-package test pass. The historical share link returned no playable target metadata; a successful real-world download still needs validation with an accessible link.
