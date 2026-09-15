# 验证记录

日期：2026-09-15。环境：Windows，Node.js 24.20.0；运行最低要求为 Node.js 22。

## 已通过

- Agent Plugins 1.0 官方 JSON Schema：根 `plugin.json` 与 `mcp.json`。
- Codex `plugin-creator/scripts/validate_plugin.py`：兼容清单、MCP 配置、技能、资源路径。
- `skill-creator/scripts/quick_validate.py`：`music-studio`、`animation-soundtrack`。
- PowerShell 安装脚本语法解析。
- 两种 PNG logo 的视觉检查；64px 小图标与 SVG 原稿随包提供。
- `npm run build`：生成独立运行文件，无需运行时 node_modules。
- `npm test`：12 项行为测试全部通过。

## 行为测试覆盖

1. 官方音乐参数与模型映射。
2. 无效、互斥及不支持的参数在请求前拒绝。
3. 歌词创作、标题保留、编辑/续写模式。
4. 翻唱来源互斥、本地音频编码、特征 ID 必须配歌词。
5. 自部署帧预算、种子和云端参数隔离。
6. API 鉴权头、业务错误和不自动重试。
7. 网络中断与未完成响应标记为未知。
8. SSE 分块、CRLF、最终完整音频或增量、截断流检测。
9. 下载不携带 API Key，并拒绝非音频错误页面。
10. 歌词文件、翻唱特征与结构时间戳保存。
11. 下载失败后恢复，禁止重复执行同一个生成 worker。
12. 使用官方 MCP SDK 完成真实 stdio 握手、11 工具发现、本地后台音频任务、断开后重连、音频资源返回、请求去重与冲突检测。

## 测试边界

所有生成测试使用合成的静音 WAV 和本地/内存模拟服务。没有向 MiniMax 发起付费生成，没有上传用户参考歌曲。本机 `doctor` 显示未配置云端 API Key。

因此，这些检查证明插件打包、参数封装和任务机制正常；不证明账号权限、云端服务当前可用性、真实 Music 3 推理、歌词准确度或音乐听感。真实验收需配置有权限的 MiniMax Key，或连接已经部署的 Music 3 服务，然后生成并试听。
