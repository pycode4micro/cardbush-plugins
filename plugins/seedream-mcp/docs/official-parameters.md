# 官方参数映射与核对范围

核对日期：2026-09-10。请求：`POST https://ark.cn-beijing.volces.com/api/v3/images/generations`，HTTP Bearer 方舟 API Key。

主要来源：

- [火山接口文档入口](https://www.volcengine.com/docs/82379/1541523)（中国站正文动态加载，本次未完整取得 Pro 专属正文）。
- [官方 Python SDK 请求签名和 JSON body](https://github.com/volcengine/volcengine-python-sdk/blob/master/volcenginesdkarkruntime/resources/images/images.py)。
- [官方 Python SDK 请求/响应类型](https://github.com/volcengine/volcengine-python-sdk/blob/master/volcenginesdkarkruntime/types/images/images.py)。
- [BytePlus 官方 Seedream 教程与 Pro 能力表](https://docs.byteplus.com/api/docs/ModelArk/1824121)。国际站模型前缀为 `dola-`，不能直接混用中国站 ID 和账号。默认中国站 ID 与现有项目配置一致；本次未通过付费调用验证账号可用性。

| 官方 JSON 字段 | 类型/选项 | 适配行为 |
| --- | --- | --- |
| `model` | 字符串 | 默认 Pro；可传 endpoint ID |
| `prompt` | 非空字符串 | 原样传递；不暗加内容 |
| `image` | 字符串/字符串数组 | URL 或 base64 data URI；最多10张、保序 |
| `size` | `1K`/`2K`/`宽x高` | Pro 像素面积921600–4624220，比例1:16–16:1 |
| `response_format` | `url`/`b64_json` | 原样传递；本地保存且省略时选择 base64 |
| `output_format` | `png`/`jpeg` | 原样传递，保存后缀以实际图片字节判断 |
| `watermark` | bool | 未指定不发送；false 不会被省略 |
| `optimize_prompt_options` | `{mode, thinking}` | mode standard/fast；thinking 为 SDK 字段，Pro 支持未确认 |
| `layer_decomposition` | bool | 透传；所有返回图层及空间元数据保留 |
| `stream` | bool | false 可透传，true 拒绝；此适配器不实现 SSE 图片流 |
| `sequential_image_generation` | auto/disabled | Pro 不支持普通组图，auto 拒绝 |
| `sequential_image_generation_options` | `{max_images}` | schema 暴露，Pro 模式拒绝使用 |
| `seed` | int | 多模型 SDK 字段，需显式允许未核实参数 |
| `guidance_scale` | number | 同上 |
| `optimize_prompt` | bool | 旧版 SDK 字段，同上 |
| `tools` | `[{type: string}]` | SDK 扩展工具字段，同上；不保证 Pro 支持 web_search |

`extra_body` 是 SDK/适配器的扩展入口，其键合并到 HTTP body，不发送一个名为 extra_body 的字段。`extra_headers`、`extra_query`、`timeout` 是 SDK 传输选项，不是图片模型参数；不把任意鉴权头和接口地址作为模型可调用的输入。超时通过环境变量配置。

Pro 能力约束以 BytePlus 官方教程为依据，不把相邻版本的4K、14张参考图或批量/流式能力套过来。官方教程支持 jpeg/png/webp/bmp/tiff/gif/heic/heif 等参考格式；本地验证使用 Pillow，HEIC/HEIF 无解码器时给出转 PNG/JPEG 提示，远程 URL 交由平台处理。输入文件不超过30MiB、36MP，边长大于14px。

便利项只有 `local.reference_images`、`aspect_ratio`、`resolution`、`save_images`、`allow_unverified_parameters`，均不以同名字段传给官方接口。没有已核实的单独官方 `quality` 或 `aspect_ratio` 参数，所以不会伪造它们。

返回数据保留全局 `model`、`usage`、`created_at`、`tool` 及新增字段，每张图片/图层保留 `size`、`output_format`、`z_index`、`bounding_box.absolute`、`bounding_box.normalized`、`name`、`description` 等。base64 保存成功后替换为 `local_path`，失败会记录 `save_error`，不会因保存失败自动再花钱生图。
