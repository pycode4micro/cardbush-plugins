# Cardbush Plugins

通过 GitHub 分发的 Codex 插件市场，包含 Seedream / Seedance / MediaKit，以及腾讯云 COS 插件。

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

重新打开 Codex 的插件页面，在市场来源中选择 **Cardbush Plugins**，即可浏览和安装这两个插件。若页面未刷新，重启客户端。

| 插件 | 包内基础版本 | 功能 | 安装说明 |
| --- | --- | --- | --- |
| `seedream-mcp` | 0.3.0 | Seedream 图片、Seedance 视频、MediaKit 超分与参考视频 Skill | [生成服务配置](plugins/seedream-mcp/README.md) |
| `tencent-cos-upload` | 0.2.0 | COS 上传、下载及经确认的单对象删除或重命名 | [COS 配置](plugins/tencent-cos-upload/README.md) |

市场添加成功后，也可按需用 CLI 安装：

```shell
codex plugin add seedream-mcp@cardbush-plugins
codex plugin add tencent-cos-upload@cardbush-plugins
```

安装或更新后，新建 Codex 任务以载入新的技能和工具。若已有 `@personal` 下的同名插件，请在插件管理页面选用一个来源，避免重复启用同一 MCP 服务。

## 新电脑运行准备

GitHub 托管的是市场索引和插件文件。两个插件的 MCP 均通过本机 Python 的 stdio 启动；添加市场本身不会部署服务或安装 Python 依赖，也不会把本地服务转换为云端 HTTPS MCP。

### Seedream 与腾讯云 COS

需要 Python 3.11 或更新版本，且 Codex 启动环境能通过 `python` 找到它。克隆仓库，在仓库根目录安装需要的包：

```shell
git clone https://github.com/pycode4micro/cardbush-plugins.git
cd cardbush-plugins
python -m pip install ./plugins/seedream-mcp
python -m pip install ./plugins/tencent-cos-upload
```

使用与 MCP 配置相同的 Python 解释器。导入插件与安装 Python 包是两个步骤；更新源码后应重新执行相应的 `pip install` 命令。

凭据通过环境变量配置，Windows 桌面客户端可使用各插件支持的用户环境变量读取功能：

- Seedream / Seedance：`ARK_API_KEY`。
- MediaKit 视频增强：`MEDIAKIT_API_KEY`，独立于 Ark 密钥。
- 腾讯云 COS：`TENCENT_COS_SECRET_ID`、`TENCENT_COS_SECRET_KEY`、`TENCENT_COS_BUCKET`、`TENCENT_COS_REGION`；临时凭据还需 `TENCENT_COS_TOKEN`。

完整可选变量见各插件 README。密钥值由使用者自行配置，仓库不包含密钥。视频工作流按需另行准备 FFmpeg / ffprobe 和参考素材预处理工具。

## 更新市场

```shell
codex plugin marketplace upgrade cardbush-plugins
```

然后在插件页面更新或重新安装需要的插件。市场目录升级不会自动替换通过 `pip` 安装的 Python 服务代码。

## 仓库结构

```text
.agents/plugins/marketplace.json
plugins/
  seedream-mcp/
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
```

市场条目的 `source.path` 相对于仓库根目录，保持为 `./plugins/<插件名>`。每个插件保留原始包内版本、源码和现有资源；发布整理将兼容清单的默认提示统一为数组，并补充仓库链接。原始 ZIP 不需要作为市场入口上传。
