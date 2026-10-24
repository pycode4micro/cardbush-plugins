# 用户明确选择生成或对话钩子时

本文件不是纯剪辑默认路线。用户只要求已有素材剪辑时不读取生成依赖、不创建生成任务。已登记 commerce_workflow_start 的项目固定 source_voiceover；改变模式时保留该项目，另建项目保存新路线。

1. 读取 [钩子选项](hook-options.md)、[内容编排](editorial.md) 和编辑器 [生成接口](../../video-editer/generated-hooks.md)。纯原片对话无需伪装成生成任务，可用显式 clip/audio 工具编排。
2. 先成立真实正文，明确开头提出的具体穿着问题、可见差异及正文兑现镜头。2–4 秒短问短答，避免泛泛夸质感。用户要求保留正文时只改开头及接缝。
3. 账号切换同时换百应素材、人物授权资产和音色。用本次真实切片作为参考；按平台支持的授权资产/经审阅预处理路线执行，不仅换提示词。
4. 生成默认 Seedance 2.0 Fast：doubao-seedance-2-0-fast-260128，2.0-fast，720p、9:16、generate_audio=true。按当次能力确认参考限制；不沿用 2.5 专用 omni_reference_task_type/output_format，不自动换模型。先按 4 秒生成、完整取用 2–4 秒规划，不把放大成 1080p 称为原生 1080p。
5. 只在已有授权范围内提交，保存原 task ID，恢复时查询原任务。short-video-prompts 只用于脚本及生成提示词，不覆盖正文约 45 秒要求。
6. 审阅人物、衣服、动作、音色、台词、尾字和接缝。只有生成结果成功导入并经过 hook_output_review，才在最新 revision 上 hook_attach。对话音画默认 1.0x；用户要求变速时同步处理对口型声音。
7. 保留已要求的花字、音效、低频转场与源音频标准。生成开头不豁免正文资格或最终声音检查。最终依然 delivery_inspection_submit → delivery_review_form/record/get；生成钩子的 hook_picture 按本路线审阅，不套用纯展示不讲话的事实表。
8. 完整流程验收后同样执行 [千川上传与回查](qianchuan-upload.md)，名称带 `Ai`；`is_aigc` 根据最终文件实际包含的生成内容和工具契约填写，不能仅凭文件名判断。入素材库后结束，不自动投放；明确的仅本地/先审阅终点优先。
