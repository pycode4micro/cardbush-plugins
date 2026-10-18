---
name: baiying-dialogue-video
description: 编排百应主播商品视频，从选账号商品、备料校验到分阶段纯剪辑、花字音效与成片验收。默认全身纯展示配独立原声；仅在用户明确要求时采用生成钩子。已有本地素材可直接进入剪辑；只查下载链接时用 baiying-replay。
---

# 百应商品视频流程

先确定终点：`plan` 只规划；`materials` 只备料；`handoff` 导入异步队列后停止；`produce` 才编辑和渲染。已有授权及明确参数直接沿用，不能将“先不实际剪辑”升级为 produce。

按 [组件](references/components.md) 发现实际工具 schema。编辑器为 `video-editer@cardbush-plugins`，服务名 `video_editer`。本流程使用 0.6.1+：修复零时长词、数值断句及过小花字；缺工具或仍为旧版时报告版本缺口，不临时写脚本替代转写、验收或启动另一套平台。

## 选择唯一执行路线

- **纯剪辑是当前默认。** 不调用视频生成或语音合成。约 3 秒全身自然展示，独立选另一处完整、有情绪的原声。画面人物不能明显持续讲话；把口播静音不等于纯展示。接真实切片正文。
- 用户明确要求生成钩子时，才读取 [生成及对话路线](references/generated-route.md)。不要把对话路线的台词、口型或工具混进纯剪辑。
- 只做 plan/materials/handoff 时，执行相应终点，不创建剪辑时间线。

## 纯剪辑按阶段执行

实际选片前读 [分阶段操作](references/source-only-stages.md) 和 [合格样例与返修](references/quality-and-repair.md)。这两个引用是执行依据；不需要一次读完整套历史参考。

1. **锁定账号商品并备料。** 账号/商品 ID 来自实际查询；指定登录身份时显式传入。下载用 baiying-replay，保存源哈希与任务 ID。只有用户允许择品才换商品。数量够不等于镜头角色够，详见 [素材与恢复](references/workflow.md)。
2. **创建或恢复阶段状态。** 创建编辑器项目、排队导入视频，随后 `commerce_workflow_start`。恢复工作先 `commerce_workflow_status`；按返回的 `stage`、`next_tool` 和缺项继续，不从头重复提交。
3. **先转写再选候选。** 按 [ASR 与完整句证据](references/speech-evidence.md) 完成内置 `speech_transcribe_submit` → `speech_candidates_get` → `speech_range_validate`，使用真实完整候选句及上下文。配音/正文通过 `commerce_speech_candidate_submit` 以句子 ID 提交，禁止跳过转写手填秒数；展示通过 `commerce_candidate_submit` 检查。等待任务成功后实际看/听，再 `commerce_candidate_review`。ASR 不认证情绪和句意；已知不合格填 false，缺少视听能力时用 `commerce_candidate_defer` 将未知事实留为 null，仅制作待审阅版本。
4. **先排成立的正文。** `commerce_timeline_apply` 用通过检查的候选一次组装：独立配音、钩子源声静音、画面与正文原速、完整正文单元、一个段落转场。读取返回的实际时间表后再装饰。仅视听审阅未完成时显式 `preview_only=true` 可组装审阅片；已知不合格或无声正文仍禁止。正式输出必须完成候选与成片检查。
5. **再加花字和声音。** 读 [布局](references/typography-layout.md) 与 [音效](references/typography-sound.md)。优先 `commerce_typography_add` 生成透明字和短动效；实际看图提供脸、动作、原字幕避让框。添加 role=sfx 音轨后，用 `commerce_presentation_record` 登记真实事件 ID 和本片配色。
6. **预览及返修。** `commerce_render_submit(preview=true)` → 成功后 `delivery_inspection_submit` → 等检查任务成功。审阅实际连续画面和声音，音效另做 `audio_review_submit`。用 `delivery_review_record(inspection_job_id=...)` 登记证据。失败修内容、生成新版本；执行失败才重试旧任务。
7. **最终交付。** 审阅版通过后提交 `preview=false`，对最终文件重新检查并记录。`commerce_delivery_get` 返回 delivery_ready=true 才称合格交付。缺能力/未试听/缺检查只交待审阅版并列明缺项。

选钩子先比较完整原声的吸引力，再找能容纳它的全身展示；普通穿法说明不能因为短、容易装入三秒就入选。画面和独立配音成对复查烧录字幕的意思，位置不重叠仍可能冲突。具体淘汰例见 [样例与返修](references/quality-and-repair.md#纯剪辑选材的反例)。

## 当前商品剪辑默认要求

用户新要求优先；这些是本商品流程的默认值，不扩展为其他片种限制。

- 成片通常约 45 秒，30 秒以上、60 秒以内。钩子约 3 秒；第一段正文先稳定讲清一个点，再切对应演示，完整语句和动作优先。[正文编排](references/editorial.md)
- 画面和正文 1.0x；仅用户允许时，独立配音可保调加速，不能截断尾字。泛泛夸质感、普通面料参数不能冒充强情绪钩子。
- 正文选区必须有实际可用声音。唯一无声画面例外是钩子的全身展示，并须另配有效声音。文件音轨存在和整片平均有声不能代替区间检查。[源音频](references/source-audio.md)
- 开头优先宽中心线 x=0.5、靠下安全区的字幕式花字；正文穿插其他安全位置的非字幕式花字。花字与我们添加的普通字幕互斥，源片烧录文字另行避让。
- 同片主题一致，最多 3 种主题对比色；不同视频可以重新选主题。透明底、主辅层级、拆色/下划线，不用色板卡片，也不让全片字形大小和入场都一样。
- 装饰转场至少 1 处、默认 1 处、最多 2 处，放在完整段落边界；硬切和花字动效不算转场。声音不能随调整花字而丢失。
- GPU 优先、重任务总并发 1。导入、候选检查、渲染、声音检查及失败重试走共享队列；外部重工具须共享资源锁或明确串行，不直接启动无管控的 FFmpeg/ASR。
- 插件测量不等于内容判断。不能用自写 JSON、not_applicable、改标签或仅增加 revision 消除失败；不会看/听时保留待审阅。
