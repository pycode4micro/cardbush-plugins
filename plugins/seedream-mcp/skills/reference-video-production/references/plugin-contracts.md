# 插件接口约定

本文件是能力适配说明，不是特定去真人化插件的安装清单。工具名前缀由客户端决定；先发现工具并读取 schema，不因名字相似猜接口。

## 可替换的图像/视频预处理

| 项目 | 需要确认 |
| --- | --- |
| 输入 | 本地路径、图片/视频格式，是否支持时间范围 |
| 覆盖 | 单脸/多脸、脸/头/全身，侧脸/遮挡支持 |
| 保留 | 商品、动作、背景、字幕和音轨哪些会改变 |
| 执行 | 同步输出或异步任务；实际 CPU/GPU 依赖 |
| 输出 | 成功状态、真实路径、时长、漏检/失败报告 |

优先专用插件；能力缺失/范围不符就询问工具或已处理素材。不固定包名、源码目录、显卡或方法，不自动安装依赖/下载模型。处理范围取决于本次替换要求，扩大到头发/全身不能是隐含副作用。图像遮罩保护商品像素，不重新设计纽扣、花纹。

## COS 上传

以下是当前 Tencent COS Upload 的能力，换实现时以新 schema 为准：

| 工具 | 主要参数 | 语义 |
| --- | --- | --- |
| `upload_video` | `file_path`；可选 `prefix`、`presign`、`expires_seconds` | 上传一个视频，不切片；返回 URL、object_key、过期信息 |
| `download_object` | `object_key`、`file_path` | 下载配置桶的对象，不覆盖本地文件 |
| `delete_object` | `object_key`、`confirm`、可选 `expected_etag` | 先预览，再按用户授权删除精确对象 |
| `rename_object` | `object_key`、`new_object_key`、`confirm`、可选 `expected_etag` | 同桶复制后删除，非原子；有版本控制/权限限制 |

流程只需要上传，不默认授权其他操作。`status=uploaded` 后用实际 `url`；`access_verified=false` 是未验证，不等于失败或已验证成功。`upload_outcome_unknown`、`uploaded_url_failed` 等状态先核查，不能盲目新建副本。

当前 `upload_video` 不接 PNG/JPEG/WAV/MP3。图片/音频可按生成能力走合法资产 URI、URL 或本地输入（显式 `local.allow_local_files=true`）。需要外传图片/音频时发现支持相应类型的工具，不改后缀伪装视频。

预签名 URL 无需公共读 ACL；到期不等于对象删除，临时凭据也可能更早失效。完整签名 URL 仅存私有运行记录。密钥按相应插件的环境变量/用户变量方式配置，不写进 Skill 或分发包。

## Seedance

顺序：`seedance_capabilities` → 本地媒体核对 → `seedance_preview_request` → 经授权 `seedance_create_task` → `seedance_get_task`。

读取 `profiles`、`media_limits`、`request_schema`、`local_options_schema`。从本次 profile 获取 `max_duration`、`resolutions`、`images/videos/audios/total`，结合媒体规则解析单段最短长度与总时长。不能把 `max_duration` 当所有媒体的统一规则。无法解析的限制须核实官方资料，不能跳过。

### 结构模板（占位符不可提交）

```json
{
  "request": {
    "model": "<当前模型或端点>",
    "content": [
      {"type": "text", "text": "<完整覆盖约定、镜头安排及逐字台词>"},
      {"type": "image_url", "image_url": {"url": "<已处理商品图或合法资产引用>"}, "role": "reference_image"},
      {"type": "video_url", "video_url": {"url": "<COS 返回的已处理切片 HTTPS URL>"}, "role": "reference_video"},
      {"type": "audio_url", "audio_url": {"url": "<用户指定音色的合法引用>"}, "role": "reference_audio"}
    ],
    "generate_audio": true
  }
}
```

替换所有占位符，没有某类参考就删除该项。按实际能力添加分辨率、画幅、输出时长、人物资产和模式。本地图片/音频需显式允许；本地视频不会由生成适配器自动上传。资产 URI 格式、类型及权限须核实，不猜裸 ID 或 group_id 可用。

2.5 的 `omni_reference_task_type` / `output_format` 不传到 2.0 系列；“pro”与实际模型 ID 的映射由能力返回确定。edit 对输入时长、输出 duration、ratio 有额外约束。输出自适应不放宽参考总时长。

预览给出媒体标签映射，但隐藏 URL，且不抓取远程媒体。私有清单保留 URL 与处理文件映射，不能由脱敏预览声称远程字节/时长/平台接受性已核实。只有请求实际带视频 URL 才是视频参考。

原生音频不保证完全复刻，提示词也不是确定性编辑保证。能力预览、离线测试、真实生成成功、成片效果验收必须分别报告。
