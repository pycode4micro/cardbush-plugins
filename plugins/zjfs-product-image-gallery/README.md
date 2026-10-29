# 商品图库

按聚水潭商品简称（原厂编码/货号）精确查图，提供 `lookup_product_images` 和
`get_product_image`。取图通过已认证的 HTTP 查询直接返回 MCP 图片；无需访问
服务电脑的盘符，不接收任意文件路径或 URL，不进行近似款替换。

客户端源码、图库服务端及回归测试由本仓库维护。CardBush 主仓库提供通用 HTTP
网关和 EXE 打包器，插件运行时不依赖 CardBush 源码目录。

## 安装与连接

从 `cardbush-plugins` 市场安装 `zjfs-product-image-gallery`。源码版客户端仅需
Python 3.10 或更新版本，使用标准库，无需 `pip install`。通用 stdio 客户端也可
直接运行 `python <插件绝对路径>/server.py`，不要求工作目录位于插件内。

内部 EXE 发行包包含运行时。安装后由管理员将连接文件放到
`%LOCALAPPDATA%\CardBushPluginConnections\zjfs-product-image-gallery.json`：

```json
{
  "url": "http://服务电脑的实际局域网IP:18765/mcp/zjfs-product-image-gallery",
  "token": "管理员单独分配的至少32字符访问令牌"
}
```

地址不能填 `0.0.0.0`；这是服务监听地址。令牌不写入插件源码，不发到聊天或仓库。
插件可以离线列出工具；实际查询需要服务电脑在线且该图库路由已启用。
批量查询最多 20 个货号，单次取一张图片，图片最大 4 MiB，仅支持 JPEG/PNG/WebP。
每个货号对应图库里的一张当前图片，不代表该商品全部颜色图或详情图。

`CARDBUSH_PLUGIN_CONNECTIONS` 可显式指定连接配置目录；默认使用上述用户目录。
凭据由管理员另行分发，源代码、插件市场和示例不包含可用业务地址或令牌。

## 服务端、打包和测试

服务端代码在 `service/`，由数据平台主机运行。客户端不会导入服务端依赖。
部署与 EXE 构建见 [部署说明](docs/deployment.md) 和 [构建计划模板](packaging.example.json)。

在单独的验证环境安装 `requirements-test.txt`，从本仓库根目录执行：

```powershell
python -m unittest discover -s plugins/zjfs-product-image-gallery/tests -p "test_*.py" -v
```

独立测试不需要 CardBush 源码或业务配置。显式验证与 CardBush 通用网关的连接时：

```powershell
python plugins/zjfs-product-image-gallery/tests/integration_cardbush.py --cardbush-root C:/sources/cardbush -v
```

所有测试使用临时数据库、临时图片、无效测试令牌和 loopback 随机端口。
`server.py` 的标准库传输实现来自 CardBush 的通用 `lan_client.py`，随插件一同分发，
无需在运行时下载代码。插件 ID、工具名及连接文件格式保持与 1.0.0 内部 EXE 一致。
