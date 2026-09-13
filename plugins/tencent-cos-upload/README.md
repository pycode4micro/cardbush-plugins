# Tencent COS Upload

[English](README.en.md)

四个独立 MCP 工具，保留原有上传接口：

| 能力 | 工具 | 作用 |
|---|---|---|
| 上传 | `upload_video` | 本地视频原样上传，返回 URL 和对象键 |
| 下载 | `download_object` | 指定对象键下载到本地新文件，不覆盖已有文件 |
| 删除 | `delete_object` | 预览并确认后删除单个指定对象 |
| 更名/移动 | `rename_object` | 同桶内复制、校验、删除旧对象；非原子操作，有安全限制 |

已切好的每个视频分别调用；完整视频也可直接上传。不会切片、转码、白模化、生成视频、递归/批量删除或修改桶权限。下载、删除和更名按对象键操作，不限制为视频扩展名。

自有“对象桶＋上传箭头”Logo，不是腾讯官方插件。Codex 插件清单包含亮/暗色展示图及小图标；MCP 服务与工具通过内嵌 PNG 提供标准 `icons` 元数据，支持该字段的 Claude/Cardbush 等客户端可显示，无需联网加载图标。

## 安装

需要 Python 3.11+。解压插件包，在插件目录运行：

```powershell
python -m pip install .
```

然后通过 Codex/Cardbush 的插件管理界面安装此目录或插件包。插件遵循 `.codex-plugin/plugin.json` 格式；客户端是否支持导入压缩包取决于客户端。

通用 MCP 客户端（包括 Claude Desktop）使用 `examples/generic-mcp.json` 中的配置：

```json
{
  "mcpServers": {
    "tencent-cos-upload": {
      "command": "python",
      "args": ["-m", "cos_upload_mcp"]
    }
  }
}
```

`python` 必须是刚才安装包的同一解释器。如果客户端找不到它，把 `command` 改成该解释器的绝对路径。这段 JSON 是通用 MCP 配置，不是 Claude 插件市场清单。

## 配置：推荐系统/用户环境变量

在 Windows 的“环境变量 → 用户变量”中添加以下四项，不要把密钥放进聊天、插件文件或仓库：

| 变量 | 内容 |
|---|---|
| `TENCENT_COS_SECRET_ID` | 腾讯云访问密钥 ID，不是火山 ARK Key |
| `TENCENT_COS_SECRET_KEY` | 配对的腾讯云访问密钥 |
| `TENCENT_COS_BUCKET` | 完整桶名，格式 `桶名-APPID` |
| `TENCENT_COS_REGION` | 桶所在地域，例如 `ap-guangzhou` |

桶须提前创建。使用受限子账号/临时凭证，按需授权，不建议使用全权限主账号密钥：

- 上传及链接读取：目标前缀的 `cos:PutObject`、`cos:GetObject`。
- 下载：目标对象的 `cos:HeadObject`、`cos:GetObject`。
- 删除：目标对象的 `cos:HeadObject`、`cos:DeleteObject`。
- 更名：源/目标对象的读、写、HEAD、删除权限；另需 `cos:GetObjectACL` 与该桶的 `cos:GetBucketVersioning`，用于安全检查。

不需要列出整个桶、修改桶配置或删除历史版本的权限。若旧凭证只有上传权限，下载/删除/更名前需补充相应授权。

可选项：

| 变量 | 默认值 / 用途 |
|---|---|
| `TENCENT_COS_TOKEN` | 使用临时凭证时的安全令牌；必须与 ID/Key 配套 |
| `TENCENT_COS_PREFIX` | `reference-videos`；对象目录 |
| `TENCENT_COS_USE_PRESIGNED_URL` | `true`；私有桶推荐签名 GET 链接 |
| `TENCENT_COS_PRESIGNED_EXPIRES_SECONDS` | `86400`；允许 60–604800 秒，临时凭证失效可使 URL 提前失效 |
| `TENCENT_COS_PUBLIC_BASE_URL` | 已配置的 HTTPS 公网/CDN 根地址，仅用于非签名模式 |
| `COS_UPLOAD_ALLOWED_ROOT` | 可选，限制上传源文件及下载目标到该绝对目录；远端对象范围应由 COS 凭证权限限制 |
| `COS_UPLOAD_TIMEOUT_SECONDS` | `120`；SDK 网络超时，范围 5–1800 秒，并非整个任务的硬性截止时间 |
| `COS_UPLOAD_READ_USER_ENV` | 进程级开关，默认 `1`；设为 `0` 禁止 Windows 用户变量补读 |

兼容 `COS_SECRET_ID`、`COS_SECRET_KEY`、`COS_TOKEN`、`COS_BUCKET`、`COS_REGION` 简写。相同层级优先 `TENCENT_COS_*`。

读取优先级：**进程变量（包括明确空值）→ 当前 Windows 用户变量 → 默认值**。每次调用重新读取，不写入系统变量，不自动读取 `.env`、其他项目配置或其他用户信息。已有配置在项目 `.env`/设置文件中的，需要自行迁到环境变量。

如果客户端仍继承旧的非空进程变量，它会覆盖新用户变量，需要彻底退出并重启客户端；不要在 MCP 配置里给密钥设置空字符串占位，空值会阻止用户变量回退。Linux/macOS 由启动客户端的进程或客户端 MCP `env` 配置传入变量。

## 上传

对助手说：“把这个本地视频上传到 Tencent COS Upload，返回可用于视频参考的 URL，不处理视频内容。”并提供绝对路径。

工具参数示例（路径仅为示例）：

```json
{
  "file_path": "D:/videos/clip-01.mp4",
  "prefix": "references/job-001",
  "presign": true,
  "expires_seconds": 86400
}
```

只有 `file_path` 必填。每次一个文件，不接受目录、通配符或远程 URL。支持 MP4/M4V/MOV/WebM/MKV/AVI/MPEG/MPG/TS/MTS，非空且不超过 5 GiB。只检查扩展名/文件大小，不验证视频解码或内容；按原始字节上传，流式读取，不整段加载到内存。

成功结果包含 `status=uploaded`、`url`、`object_key`、`bucket`、`region`、`size_bytes`、`content_type`、`etag`、`url_type`、`expires_seconds`、`expires_at`。对象键自动添加日期和随机 UUID，不沿用本地文件名，避免同名覆盖；另外发送禁止覆盖请求头（桶开启/暂停版本控制时，此请求头可能不生效）。

将 `url` 原样交给需要网络视频地址的模型/API。私有链接是签名 **GET** 链接，不应以 HEAD 请求失败判定不可下载；本插件不额外下载验证，结果标注 `access_verified=false`。

## 下载

使用上传结果中的 `object_key`，不是完整 URL：

```json
{"object_key":"references/clip-01.mp4","file_path":"D:/videos/downloaded-clip.mp4"}
```

调用 `download_object`。目标为绝对文件路径，父目录必须存在，目标文件必须不存在。流式下载至临时文件，校验 ETag 与长度后无覆盖落盘，成功返回 `status=downloaded`、本地路径、大小及本地 SHA-256。不转码，单个对象上限 5 GiB；网络/磁盘异常不会覆盖现有目标文件。

## 删除

先调用 `delete_object` 预览：

```json
{"object_key":"references/clip-01.mp4"}
```

默认 `confirm=false`，只读取对象信息。返回 `confirmation_required` 和 `etag`。用户已明确授权删除该对象后，携带预览中的 ETag 再调用：

```json
{"object_key":"references/clip-01.mp4","confirm":true,"expected_etag":"预览返回的etag"}
```

只接受一个精确对象键，不接受 URL、目录、通配符或批量目标；不执行历史版本删除。未开启版本控制时可能永久删除；开启版本控制时可能创建删除标记，返回值会注明，不能保证所有删除均可恢复。删除会使旧下载/参考链接失效。

**预览不等于用户授权。** ETag 是乐观检查，并不是原子条件删除。操作期间请停止对该对象键的并发写入；工具不会假装已获得 COS 的原子事务保障。

## 更名 / 同桶移动

先调用 `rename_object`：

```json
{"object_key":"references/old.mp4","new_object_key":"references/new.mp4"}
```

用户已明确授权后，再带上 `confirm=true` 与预览 `etag` 对应的 `expected_etag` 调用。

顺序为：检查源/目标 → 服务端复制 → 校验副本 ETag/大小及源对象未变 → 删除旧对象 → 返回新签名 URL。**不是原子更名**；操作期间停止对两个对象键的并发写入，也不要修改桶版本控制/ACL。

安全限制：同一已配置桶、单对象不超过 5 GiB、目标不存在、桶从未开启版本控制、源对象继承桶默认 ACL。不满足时会拒绝，不擅自覆盖或改变访问权限；自定义 ACL、已启用/暂停版本控制的桶暂不支持更名。原对象元数据采用 COS COPY 语义，创建时间和旧 URL 不保留。

部分失败状态：

| 状态 | 含义 |
|---|---|
| `copy_outcome_unknown` | 副本是否完成未知，未尝试删除原对象 |
| `copied_source_retained` | 校验未通过，未尝试删除原对象 |
| `copied_delete_outcome_unknown` | 副本已校验，删除响应未知；检查两端，不要盲目重试 |
| `renamed` 且 `url=null` | 更名已完成，仅签名失败，不要再次更名 |

没有任何自动回滚删除：异常时优先保留数据供用户处理。

## 安全、费用和失败处理

- 上传会产生腾讯云存储、请求及后续访问流量费用；创建插件/运行离线测试不会上传。
- 签名 URL 本身是临时访问凭证，不应公开分享；持有者在有效期内可能读取该视频。接收 URL 的模型服务会取得读取权限。签名参数中包含签名所需的标识/令牌，不能把完整 URL 当作无敏感信息的日志。
- 过期的是 URL，不是对象。仅显式调用删除/更名才会请求删除指定对象；不设置生命周期、不自动清理。需要自动清理时由用户在 COS 控制台配置桶生命周期。
- 不会把私有桶改成公开桶。`presign=false` 仅适用于已有公开读策略或正确配置的 CDN，否则 URL 可能返回 403。
- 上传报错返回 `upload_outcome_unknown`：对象可能已存在，先按 `object_key` 检查，不要盲目重复上传。签名失败返回 `uploaded_url_failed`：视频已经上传，保留对象键，不要重新上传。`source_changed` 表示上传期间源文件有变化，需确认后使用。
- 插件不做整文件自动重试；官方 SDK 可能进行网络级重试。客户端超时/取消也不保证在途上传立即停止。上传前请等待上游切片/导出完成。
- 删除响应异常返回 `delete_outcome_unknown`，应先检查桶中状态；不要因响应超时自动再删。下载、复制、预览读取等也可能产生请求/流量费用。
- SDK 原始异常及调试日志被抑制，避免泄露密钥、签名地址。MCP 客户端仍可能保存调用路径和返回 URL；公开日志前需要脱敏。
- 不携带凭证、桶配置、原视频或依赖项目源码；独立运行。

官方参考：[Python SDK](https://github.com/tencentyun/cos-python-sdk-v5)、[签名 URL](https://intl.cloud.tencent.com/zh/document/product/436/31548)、[复制与移动](https://cloud.tencent.com/document/product/436/65826)。
