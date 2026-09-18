# 视频生成去真人化处理 0.2.3

0.2.3 增加交付前逐镜头抽查、帽子/侧背头部的人工区域补漏指引，以及带归一化网格和实际椭圆轮廓的取坐标脚本。覆盖统计新增 `no_mask_frames` 和 `fallback_only_frames`，区分无掩膜与无网格但已有回退遮挡的帧；保留旧字段含义和检测默认值。

0.2.2 修复 Windows 后台启动仍弹出终端窗口的问题。启动、依赖检查、CPU/GPU 工作进程及 FFmpeg 都按无窗口方式运行；MCP 消息保持原始字节传递，关闭连接时正确传递输入结束。使用原有依赖，无需补装新包。

0.2.1 修复首次启动时 MCP Settings 的 `lifespan` 类型前向引用尚未解析的问题。
创建服务前调用 `model_rebuild()`，保留警告检查，依赖版本及视频处理参数保持兼容。

本地视频石膏白模处理插件，技术标识 `video-face-stylizer`。默认将检测到的整头（脸和头发）覆成不透出原始纹理的白色石膏；有可靠人脸网格时叠加三维脸部光照。原视频不修改，输出保留尺寸、音频和其他画面。需要保留头发时，显式选择 `coverage="face"`。

## 这版改了什么

- 检测分辨率与输出分辨率分开，检测阈值可调。全画面检不出时，搜索前一头部区域、分割得到的头部区域和重叠分块，坐标映射回原画面。
- 头部分割独立于人脸检测。使用包内模型的头发与脸部类别，排除身体皮肤；整幅分割无头部时，再做分块分割。
- 增加独立人物检测，自动遮挡限制在人物范围内；仅靠分割得到的区域还检查头发、位置与尺寸，减少花束、手臂和躯干的误涂。人工区域不受自动人物检测限制。
- 短暂漏检可由经过前后向一致性检查的光流接续，默认最长 0.2 秒。检测到切镜或跟踪不可信时停止沿用。
- 增加按源视频时间指定的 `head_regions`，用于远景、侧脸、背身等自动检测仍不可靠的镜头。直接填充白模，不用模糊替代。
- 返回实际遮挡统计和需复查的时间段。依赖版本锁定，首次连接自动准备 Python、依赖、模型校验和编码器检查。

## 安装与依赖

适用于 Windows x64 和本地 STDIO MCP 宿主。CPU 模式无需独显；GPU 模式需要 OpenGL 3.3 驱动。视频处理在本地完成，无需 API 密钥。

**CardBush：直接导入这个源码 ZIP。** 配置通过 `${PLUGIN_ROOT}/scripts/start.ps1` 启动。首次连接会自动准备 Python 3.12 和插件目录中的 `.venv`，安装锁文件里的完整依赖，校验包内模型，并验证真实模型推理、CPU 渲染编译和 FFmpeg。无需自行补装 Python 包或修改 site-packages。后续连接使用已准备的环境。

首次准备需要访问 GitHub、Python 下载源和 PyPI；网络不可用时会明确报错。ZIP 包含源码、模型及依赖锁文件，**不包含全部 Python 和第三方 wheel 二进制**。下载时间取决于网络；若宿主首次连接超时，可在插件目录双击 `setup.cmd` 完成安装后重新连接。

也可先解压到长期保留目录，双击 `setup.cmd`。脚本会生成绑定该目录解释器的本机 ZIP；导入本机 ZIP 后必须保留原目录。不要把这种本机包复制到其他电脑。需要指定解释器时使用 `scripts/setup.ps1 -Python 'C:\Python312\python.exe'`。

依赖在 `pyproject.toml` 声明，`uv.lock` 锁定所有直接和间接依赖及下载校验信息；`requirements.lock` 是带哈希的导出副本。Python 包含 MediaPipe、OpenCV、NumPy、Numba、ModernGL、MCP、Pydantic、psutil 和 imageio-ffmpeg；FFmpeg 随其 wheel 提供，四个模型/网格资产随插件提供，其中新增 EfficientDet-Lite0 人物检测模型，无需另装推理库。没有系统 Python 时由 uv 自动下载，不依赖 Windows Store 的 python 别名。安装脚本使用 uv 0.12.10；下载插件私有 uv 时校验官方 SHA256。

## 工具与任务

| 工具 | 用途 |
| --- | --- |
| video_face_capabilities | 检查依赖、模型、默认参数与 GPU |
| render_video_cpu | CPU 推理、渲染、合成和编码 |
| render_video_gpu | CPU 推理、合成和编码，OpenGL 渲染三维脸部 |
| get_video_job | 查询同一个任务，可等待 0–20 秒 |
| cancel_video_job | 取消指定任务，随后查询至终态 |

两个渲染工具的参数相同：

```json
{
  "request": {
    "input_path": "C:\\Videos\\input.mp4",
    "output_directory": "C:\\Videos\\results",
    "start_seconds": 8,
    "duration_seconds": 4,
    "max_height": 0,
    "coverage": "head",
    "output_filename": "plaster_8_to_12.mp4"
  }
}
```

返回 `job_id` 后，使用同一 ID 和 `output_directory` 调用 `get_video_job`。只有 `state="succeeded"` 的输出才是完成视频；其他终态有 failed、cancelled、interrupted。不要重复提交来查询进度，也不要在已有任务仍运行时再提交另一模式。GPU 不可用会报错，不会暗中切换 CPU。

输入和输出目录必须为绝对本地路径。输入支持 MP4/MOV/MKV/AVI/WebM/M4V，实际解码能力取决于 FFmpeg。输出新建 H.264 MP4 和 benchmark JSON，不覆盖已有文件。省略 duration_seconds 处理剩余全片。

## 可调处理参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| coverage | head | 整头白模；face 为原版仅脸部模式，漏检时该帧保留原画面 |
| person_filter | true | 自动遮挡需通过独立人物检测，并限制在人物范围内；人工区域不受限 |
| person_detection_confidence | 0.35 | 人物检测阈值，0.1–0.95 |
| max_height | 0 | 输出保持原尺寸；设置偶数高度可缩小，不升采样 |
| detection_max_side | 1280 | 检测工作图最长边，256–2560；裁切提高脸部占比比外部升采样更有帮助 |
| min_face_detection_confidence | 0.35 | 人脸检出阈值，0.05–0.95 |
| min_face_presence_confidence | 0.45 | 网格存在阈值，0.05–0.95 |
| min_tracking_confidence | 0.5 | VIDEO 跟踪阈值，0.05–0.95 |
| tiled_detection | true | 全画面漏检时做局部和分块人脸检测 |
| tiled_segmentation | true | 整幅分割无头部时做分块头部分割 |
| detection_tile_grid | 3 | 分块每轴数量，2–4；越细处理成本越高 |
| segmentation_confidence | 0.4 | 头部分割阈值，0.1–0.95；过低会增加误遮挡 |
| segmentation_requires_hair | true | 仅分割补漏时要求有相邻头发，减少手臂误涂；人脸网格和人工区域不要求头发 |
| segmentation_head_fraction | 0.35 | 仅分割补漏时，头部中心需在人物框上方这一比例内；特殊姿势可设为 1，或用人工区域 |
| mask_padding | 0.08 | 按分割头部短边扩张遮挡范围，0–0.4 |
| temporal_hold_seconds | 0.2 | 允许光流补齐的最长缺口，0–1 秒；0 关闭 |
| head_regions | [] | 人工指定的头部区域，仅 head 模式 |

### 补漏区域

每个区域有开始、结束时间和一个或多个位置关键帧。坐标为原画面归一化的 `[左, 上, 右, 下]`。时间均是**裁切前源视频的绝对秒数**，区间左闭右开；关键帧之间线性插值，区间边缘保持最近的位置。每个切镜单独设置区域，避免跨切镜插值。

例如处理源视频 8–12 秒，其中 8–10 秒的人物头部从左向右移动：

```json
{
  "head_regions": [{
    "start_seconds": 8,
    "end_seconds": 10,
    "keyframes": [
      {"seconds": 8, "box": [0.20, 0.15, 0.32, 0.38]},
      {"seconds": 9.5, "box": [0.35, 0.15, 0.47, 0.38]}
    ]
  }]
}
```

将这个字段放入渲染工具的 request。框内会按椭圆填充白模，矩形四角不在填充范围内；需要检查整个头部和帽檐都落在椭圆内，快速移动或明显变形时增加关键帧。mask_padding 只影响自动分割，不扩大人工椭圆。该机制无需人脸检测成功，但位置是否合适仍需查看画面。

### 帽子、侧背与交付前抽查

`segmentation_requires_hair=true` 限制的是分割候选，人脸网格和人工区域不要求头发；帽子、头巾、光头或侧背镜头可能只盖住部分头部。此时优先按镜头用 head_regions 补跑，不把关闭 person_filter 当作关闭头发筛选，也不因覆盖数字好看而扩大到整个人物框。

成功后先查看每镜头首/中/末、转场两侧和 review_intervals，检查帽檐、发型、耳朵残留及宠物、商品、手部的误遮挡。修复后再次抽查，再交付或送入下游。使用插件 Python 执行 `skills/video-face-stylizer/scripts/head_region_grid.py` 可抽帧、标注 5% 网格，并用 `--box` 预览与渲染一致的椭圆轮廓；命令与 QC 流程见 [补漏与抽查](skills/video-face-stylizer/references/head-region-repair.md)。

## 如何判断结果

- `faces_detected_frames` / `missed_frames` 仅统计三维人脸网格，漏网格不代表没有头部遮挡。
- `coverage.covered_frames` / `uncovered_frames` 表示该帧是否存在至少一个遮挡区域，**不是“全部人物都遮好了”的证明**。
- `coverage.no_mask_frames` 与 uncovered_frames 含义相同，包括空镜，不能直接叫“真人漏遮帧”；`coverage.fallback_only_frames` 统计无人脸网格但最终已有遮挡的帧（分割、光流、人工区域），同一帧只计一次。它是 covered_frames 的子集，不与 covered_frames 相加。
- coverage 还包括 full_frame_faces、crop_faces、segmentation_crop_frames、segmentation_fallback_frames、temporal_fallback_frames、manual_mask_frames；不同方式可能重叠，不能简单相加。
- person_frames / no_person_frames 单独记录人物检测情况。未开启 person_filter 时这两个计数为 0，不能据此判断人物是否出现。
- `review_intervals` 为源视频上的左闭右开时间段：person_not_detected 表示人物检测未通过，no_head_mask / no_face_mask 表示没有头部/脸部遮挡，segmentation_only 表示只有分割补漏，optical_flow 表示沿用跟踪遮挡。无人物的空镜和头部出画镜头也会被列为未遮挡，应结合画面判断。

不返回推测性的 hair_unsupported_frames 或 mask_partial：头发筛选拒绝的区域也可能是手臂，面积变小也可能是转身或出画，不能据此判定真实漏遮。用现有复查区间和源/输出画面定位，统计只报告实际执行事实。

非常小的头部、严重遮挡、快速移动、侧背镜头仍可能漏检或误分割。默认分割筛选适合站立、坐姿人物；光头、横躺等特殊画面可调整相应筛选项或用 head_regions 补漏。自动结果必须抽查。当前每帧最多渲染一张三维脸，头部分割可覆盖其他检出的头部；不保证多人完整覆盖。建议固定帧率素材；可变帧率素材应先转固定帧率。

白模是视觉效果，不承诺可靠身份匿名化，也不保证下游视频生成模型一定换脸、去掉白模或接受素材。插件本身不会提交付费生成任务。新版增加分割和局部搜索，旧版的处理速度不能直接沿用；以本次 job_wall_seconds 为准。

## 数据与开发

输入视频不修改。记录、日志、临时视频及 CPU 编译缓存位于输出目录的 `.video-face-stylizer-jobs`；删除后无法查询旧任务。宿主终止进程后任务可显示 interrupted，不自动重跑。启动环境标记为插件内的 `.runtime-ready.json`，移动源码目录或更换锁文件后会重新准备。

模型来源和 SHA256 见 `src/video_face_stylizer/engine/model_sources.json`，第三方许可见 models/MEDIAPIPE_LICENSE.txt。本插件代码采用 MIT 许可。

```powershell
uv sync --locked --extra test
uv run --locked --extra test pytest -q
.venv\Scripts\python.exe tests/integration_smoke.py --output C:\Temp\vfs-new-test-directory
.venv\Scripts\python.exe scripts/package_plugin.py --output ..\video-face-stylizer-source.zip
```

integration_smoke 生成无私人内容的短片，经过真实 PowerShell MCP 启动入口、异步 worker、CPU/GPU 渲染、分段遮挡和视频/音频解码校验。输出目录应使用未存在的新目录。打包排除运行环境、安装标记、日志、验证素材和缓存，拒绝符号链接。

分割类别与模型说明参考 [MediaPipe Image Segmenter](https://developers.google.com/edge/mediapipe/solutions/vision/image_segmenter) 和 [Object Detector](https://developers.google.com/edge/mediapipe/solutions/vision/object_detector/python)，环境管理参考 [uv 项目文档](https://docs.astral.sh/uv/guides/projects/)。
