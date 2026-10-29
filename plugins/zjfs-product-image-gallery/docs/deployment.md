# 商品图库只读服务

面向已部署的聚水潭商品图库 `dwd.jst_product_image_gallery`。按 `short_name`
（商品简称/原厂编码/货号）精确查找，一码对应一张当前图片；不猜近似货号。

## 能力与数据流

- `lookup_product_images`：最多 20 个货号，按请求顺序返回 ready/not_found/unavailable。
- `get_product_image`：一个货号，返回 MCP 图片内容，可直接在 CardBush 中显示。
- 客户端 EXE → 带独立令牌的局域网 HTTP 请求 → 服务端参数化只读 SQL → 受限图片读取。
- 不开放数据库端口，不返回本机盘符，不需要同事配置数据库、Python 或 NAS。
- 图片随认证后的工具响应返回，没有免认证图片 URL、任意下载 URL 或静态目录服务。
- 无同步、刷新、上传、删除、文件浏览、SQL 输入或路径输入能力。

查询只读事务设置 PostgreSQL 5 秒语句超时、1 秒锁等待超时；只检索指定主键。
取图拒绝越界、符号链接、junction、硬链接及备用数据流，Windows 在读取期间锁住
所有祖先目录和文件，防止检查后的路径替换。校验索引 SHA256 和图片格式，最大
4 MiB、2500 万像素，仅 JPEG/PNG/WebP。图片暂不可取时返回错误，不自动下载或改库。

## 服务端部署

使用已有数据平台的 Python 环境（SQLAlchemy、PostgreSQL 驱动、Pillow）和数据库
配置。数据库访问只发生在服务电脑；可进一步由管理员配置仅 SELECT 权限的数据库角色。

从 CardBush 仓库取通用 `scripts/plugin-distribution/lan_gateway.py`，从本插件取
业务服务和描述文件。保留插件自己的目录结构：

```text
gateway/lan_gateway.py                         # CardBush 通用网关
zjfs-product-image-gallery/lan-tools.json       # 本插件
zjfs-product-image-gallery/service/product_gallery.py
zjfs-product-image-gallery/service/gallery_files.py
```

在网关配置的 `routes` 中增加独立条目（以下是模板，无真实凭据）：

```json
{
  "path": "/mcp/zjfs-product-image-gallery",
  "module": "product_gallery",
  "token": "由管理员生成的至少32字符独立随机令牌",
  "allowed_tools": ["lookup_product_images", "get_product_image"],
  "settings": {"image_root": "D:/company-product-images"}
}
```

`image_root` 必须指向现有图库的实际根目录。迁移前的图片仍在旧目录时，可在
`settings` 中显式添加 `legacy_image_roots` 数组，仅列出实际使用的旧图库目录；
每个目录执行相同的路径、链接和摘要校验，不扩大到其父目录。
网关启动进程的模块搜索路径需包含本插件 `service/` 和数据平台根目录，才能导入
`product_gallery` 及 `packages.core.config`；工作目录使用数据平台根目录。
描述文件从服务代码的父目录读取，不依赖 CardBush 源码中的任何插件模板。
先以 `127.0.0.1` 做本机测试，
再由管理员批准新增图库访问范围；随后设 `lan_enabled: true`、限定
`allowed_clients`，使用 `--host 0.0.0.0 --port 18765`。只开放指定内网及访问令牌，
不要把整个数据平台 API 直接开放出去。HTTP 内网明文的部署条件与原查询网关一致。

## 客户端与 EXE

源目录是本仓库的 `plugins/zjfs-product-image-gallery`，包含可独立运行的 `server.py`。
复制插件根的 `packaging.example.json` 到构建目录，填写源码、运行时、启动器及输出位置；
Python requirements 保持 `[]`，入口为 `${PLUGIN_ROOT}/server.py`。无需从主仓库复制业务文件。
在 CardBush 源码目录调用其通用构建器：

```powershell
python scripts/plugin-distribution/build_plugins.py C:/private-build/gallery.plan.json
node scripts/plugin-distribution/verify_plugins.mjs C:/private-build/product-gallery/plugins
node scripts/plugin-distribution/verify_host.mjs C:/private-build/product-gallery/plugins
node scripts/plugin-distribution/package_installer.mjs C:/private-build/product-gallery/plugins C:/private-build/product-gallery/gallery.exe
```

`verify_host.mjs` 使用已构建的 CardBush 产品宿主。构建器保留插件 ID、工具名、协议和
连接配置格式；将源码 stdio 清单替换为本插件 EXE，客户端不安装服务端数据库依赖。
该插件是原 16 个插件之外的单独增量包，避免重装其他插件。

连接文件：`%LOCALAPPDATA%\CardBushPluginConnections\zjfs-product-image-gallery.json`。

```json
{
  "url": "http://服务电脑的实际局域网IP:18765/mcp/zjfs-product-image-gallery",
  "token": "与该路由一致的访问令牌"
}
```

`0.0.0.0` 只用于监听，不能填入客户端地址。连接文件属于私密配置，不提交仓库；
EXE、数据库、图片及运行时同样不提交。停用只需移除该路由并重启网关。

## 回归验证

在装有本插件 `requirements-test.txt` 的隔离验证环境中，从插件仓库根目录执行：

```powershell
python -m unittest discover -s plugins/zjfs-product-image-gallery/tests -p "test_*.py" -v
python plugins/zjfs-product-image-gallery/tests/integration_cardbush.py --cardbush-root C:/sources/cardbush -v
```

测试使用临时数据库、临时图片与 loopback 随机端口，不读取或修改业务数据。
覆盖查询边界、SQL 参数化、只读事务、图片回传、鉴权、错误脱敏、文件越界、
链接逃逸、Windows 路径替换及不可信工具参数。

## 既有部署

这次仓库迁移不修改已安装的插件、服务端配置或凭据。已运行的 1.0.0 服务副本和
EXE 可继续使用。以后更新服务源码时，按上述新目录结构部署并重启网关，避免只替换
`product_gallery.py` 却遗漏其父目录的 `lan-tools.json`。
