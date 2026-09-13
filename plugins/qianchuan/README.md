# 千川独立插件（开发版）

插件自带 runtime/app 的千川工具实现，不连接桌面 qianchuan_tool_service，不读取其 .env、数据库或虚拟环境。不需要启动本地 REST/MCP 开发服务。

## 从 GitHub 市场安装

```shell
codex plugin marketplace add pycode4micro/cardbush-plugins --ref main
codex plugin add qianchuan@cardbush-plugins
```

已添加市场时，先运行 `codex plugin marketplace upgrade cardbush-plugins` 刷新目录。安装后新建 Codex 任务；通过 `qianchuan_setup_status` 查看独立配置文件的位置和缺失项，再按下文完成配置。

## 启动

兼容 Host 读取根目录 plugin.json 和 mcp.json；旧客户端可使用 .codex-plugin/plugin.json 和 .mcp.json。Host 需支持插件根路径变量 PLUGIN_ROOT，并能运行 uv。uv 按 scripts/launch.py.lock 安装隔离依赖，首次使用需联网下载依赖；不复用开发环境。开发阶段尚未内置 uv/Python 可执行文件，不能称为零系统依赖的离线软件包。

其他通用 MCP Host 可直接运行：
uv run --frozen --script <此插件绝对路径>/scripts/launch.py

## 配置与数据隔离

默认持久目录为 Windows %LOCALAPPDATA%/qianchuan-plugin；其他系统为 ~/.local/share/qianchuan-plugin。可单独设置 QIANCHUAN_PLUGIN_DATA_DIR。该目录跨插件升级保持稳定。

- config.env：用户独立配置的网关及安全参数。
- data/qianchuan.db：独立策略、审计与任务数据库。
- data/oauth_selection.json：独立授权选择。
- materials/images、materials/videos：本插件上传暂存区。

没有配置时仍可启动 MCP，通过 qianchuan_setup_status 返回配置位置与缺失项。按 config.example.env 在该位置配置后重新连接。不会自动从原服务迁移任何配置、权限或账号。网关地址和HMAC由部署者提供；无需在对话里公开密钥。

## 内置能力

千川官方API调用、OAuth客户端、授权选择、素材上传及绑定、投放操作、安全校验、审计、限时任务及后台暂停全部由插件代码执行。正确执行不要求Agent遵守Skill协议。

配置有效时自动启动插件自己的独立到期保障进程。关闭Agent会话不会主动终止该进程，系统退出或电脑关机仍会中断；不自动安装开机自启。任务数据持久保存，插件下次启动继续恢复。动态ROI决策与外部通知仍不是本地自治算法。

仅OAuth中转网关和千川官方API是业务必需的外部连接。商品货号ERP解析、AI生图是可选上游能力，不是插件启动或投放的必要依赖；没有它们时使用明确的商品ID和已提供素材，不假装内置模型。

## 运行验证

开发项目可以移动、停服或删除，插件运行不再依赖它。不得把开发数据库复制到插件目录冒充隔离测试；新实例应先返回 setup_required，不具有旧服务的写权限。

参考：https://developers.openai.com/plugins/build/plugins
