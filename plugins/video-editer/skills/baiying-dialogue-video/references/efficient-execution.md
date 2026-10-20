# 批量执行、一次导出与实际审阅（0.7.0+）

沿用已有队列、项目和阶段状态，不增加 Usage 或另一套编排器。GPU 优先，重任务总并发 1；批量提交表示减少工具往返，不代表并行执行重任务。

## 快速路径

1. 导入后选择一个足够的工作素材池，先确认 `speech_runtime_status`，模型缺失才 `speech_model_prepare`。用 `commerce_materials_prepare(project_id, asset_ids)` 一次提交池内素材 ASR；未选入池的其他导入视频不阻塞流程。素材不足再补充一批。
2. 用返回的 `next_arguments` 调 `media_jobs_wait`，默认最多等待 20 秒。有失败先处理其错误再 `media_job_retry`，不要循环新建相同任务。不要每秒轮询每个文件。
3. 返回的句子只是每个源的分页摘要，按 `next_offset` 继续 `speech_candidates_get`，读取上下文后选择完整句。不能将摘要中没有出现的句子当成不存在，也不能将 ASR 当成试听。
4. 用 `commerce_candidates_submit` 批量提交候选。展示项填 `role=hook_picture,asset_id,start,end`；配音/正文项填 `role,transcript_job_id,sentence_ids`，工具自动校验源时间。完成后查看实际候选视频/声音、内部短镜头提示，记录审阅或保持待验。
5. 按原有阶段组片、花字和声音设计。`commerce_render_submit` 默认 `preview=false`，一次导出目标画质；对这一文件完成 `delivery_inspection_submit`、声音对照和审阅，合格后直接 `commerce_delivery_get`。不再为“正式版”重复渲染同一时间线。

`preview` 只控制画质。无法完成感知审阅时，可明确以 `preview_only=true` 组装待验时间线，仍导出目标画质供审阅；此参数保留旧名，不代表必须低清。已拒绝或无声正文/配音不能进入此分支。待验文件不能被称为合格成片。

## 返修和缓存

- 只改音效时，新提交渲染自动复用底片、图形和字幕画面，只重做声音与封装。只改字幕时复用底片、图形和声音。以任务返回的 `reused_stages` 为准。
- 内容修改后用新渲染提交；`render_retry` 只重试原快照，不能修正剪辑内容。两者均走现有队列。
- ASR 和源选区检查按源文件、范围、模型/分析实现复用；缓存不携带审美审批。文件变动、证据损坏或规则版本变化导致失效。
- 新输出的声音、同步和画面检查仍需绑定新文件，不复制旧的 `pass`。不为提速增加重任务并发或关闭无声检查。
- 转码或修复帧率使用 `media_convert_submit`。`interpolate=true` 是明确开启的运动补帧，较慢，不作常规优化；返回新素材后重新导入、分析。失败重试用 `media_job_retry`，不另开终端 FFmpeg 绕过队列。

## 审阅记录

`continuous_review` / `listening` 是方法名称，不是审阅发生的证据。候选审阅的 `review_receipt_paths`、成片各检查的同名字段引用**实际收到的视听工具结果或用户反馈的导出记录**。一次实际审阅可供其覆盖范围内的多项检查引用，不必重复观看或重复填写。

记录为 UTF-8 JSON：

```json
{
  "schema_version": 1,
  "provenance": {
    "kind": "tool_result",
    "producer": "实际视听工具名",
    "reference": "实际工具调用或结果编号"
  },
  "media": {
    "path": "实际已审阅媒体的绝对路径",
    "sha256": "实际媒体的SHA256"
  },
  "modality": "audiovisual",
  "ranges": [[0, 3]],
  "observation": "工具实际返回、与本次画面及声音对应的具体观察"
}
```

- `modality` 为 `video`、`audio`、`audiovisual`、`frames` 或 `user_feedback`。用户反馈同时使用 `provenance.kind=user_feedback`，引用真实用户消息。范围使用该媒体自己的秒数，候选预览从 0 开始，不能直接填原片偏移。
- 候选记录必须对应工具返回的候选预览。成片记录必须对应本次输出；试听也可对应本次检查导出的完整 WAV。不接受其他片子的记录。
- 可将实际收到的工具结果无损整理成此格式；保留原结果编号和具体观察。没有感知输入时，不得临时编写“看过了／听过了”的 JSON 凑字段。ASR、响度、截图存在或 `inject_image` 调用本身均不能证明连续观看或试听。
- 插件校验媒体哈希、范围、模态和来源字段；它不能认证宿主签名，也不能独立证明模型理解了画面。记录真实性仍由调用方负责。缺记录、范围不足、只有截图却声称连续审阅，保持待验。
- 自定义花字缺 `commerce_text/layout` 会明确返回 `typography_unchecked`，字号与主题未检查，不能将跳过当成通过。优先使用会自动生成元数据的 `commerce_typography_add`。
- 内部闪切是检测提示，不能只看时间线边界。查看提示对应的连续画面后再判定；短镜头不一律等于错误。
