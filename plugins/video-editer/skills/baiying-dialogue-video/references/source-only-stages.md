# 纯剪辑分阶段操作（video-editer 0.6.1+）

本路线固定为原片全身展示配独立原声，不调用生成，不读取生成钩子工具。先把用户要求带入 account、product、target_seconds；不会看/听时保留待审阅，不猜测事实。返回字段是操作依据，旧聊天里的阶段描述不是。

## 1. 启动与恢复

新项目：project_create → media_register 导入视频 → media_job_status 确认成功并保存 asset.id → commerce_workflow_start(project_id,account,product,target_seconds=45)。已有素材复用源哈希相同的已成功资产。

中断后：commerce_workflow_status(project_id)。它返回当前阶段、候选 ID、现有渲染/检查任务、next_tool 和 required_actions。排队/运行中的任务继续查询，终态失败才 retry；无可恢复条件或候选不适用时 commerce_candidate_skip 记录原因，避免无限重试。不要再次提交同一任务来催进度。

不要把 604 行旧 workflow-plan.json 当成 API。以下工具保存真正执行状态；规划模板只供高级路线参考。

## 2. 每次只判断一个候选

**选素材之前必须先转写。** 按 [ASR 与完整句证据](speech-evidence.md) 完成模型准备、排队转写、完整句候选与时间戳校验。`commerce_workflow_status` 会返回 `source_transcription` / `transcript_wait` / `transcript_failed` 及下一步参数。转写依赖随插件提供，不临时补工具。默认原声，禁止把 ASR 当成重新配音。

配音和正文：`speech_candidates_get` 读取原文及前后文 → `speech_range_validate` 校验连续句 ID → `commerce_speech_candidate_submit(project_id,transcript_job_id,sentence_ids,role)`。工具使用已校验区间生成证据，避免手填错秒。展示画面：实际看过后 `commerce_candidate_submit(project_id,asset_id,start,end,role="hook_picture")`，它要求该源已有覆盖所选区间的转写；无声展示可用显式无语音记录。

候选检查排队生成连续预览、带实际 PTS 的首/中/尾画面和选区声音测量。成功后实际看/听返回的文件，再调用 commerce_candidate_review(job_id,facts,method,note)。没有 ASR 绑定的旧候选不能直接沿用或通过 defer 豁免。

| role | 必须逐项观察的 facts | method |
|---|---|---|
| hook_picture | same_person、same_garment、full_body_visible、natural_display、no_continuous_speaking、burned_captions_compatible | continuous_review |
| hook_voice | complete_sentence、emotional_delivery、applicable_to_product、intelligible | listening |
| body | same_person、same_garment、complete_meaning、complete_action、visible_support_for_claim、intelligible_source_speech | continuous_review_and_listening |

candidate_review 的 facts 必须来自真实观察：符合为 true，已知不符合为 false，note 说明现象与时间点。未能观察的项目用下面的 defer/null 分支，不把未知写成通过或已确认失败。首中尾截图帮助定位，不证明三秒内一直没有讲话。自然微笑、转身不等于持续讲话；连续口型与讲解手势应选另段。

0.5.1+ 的审阅片分支：当前环境不能真实看/听时，不调用 candidate_review 冒充通过；用 commerce_candidate_defer，观察到的事实为 true，未知为 null，note 写明缺失能力与待查项。pending 与 rejected 不同：已知 false、无声正文/配音和已被拒绝的候选不能转成 pending。只有明确 preview_only=true 的 commerce_timeline_apply 与 preview=true 渲染可使用 pending；正式渲染和 delivery_ready 仍阻断。用户批准或具备能力后需对真实证据正常 review，不能用 warning_resolutions 豁免。

先选有吸引力的完整原声，再按其实际时长找 2–4 秒展示。存在多个候选时，比较至少两句原文、上下文、吸引人的原因和正文接法，淘汰普通穿法说明；候选不足就写明，不为填表编句。不要先固定一段展示，再为塞进时间窗挑最短、最平淡的台词。完整原声优先，换展示区间比换成弱钩子更合理。

钩子画面选 2–4 秒自然展示，全身及服装廓形可见；只到膝盖不算完整全身。画面与独立原声确定后，成对记录展示区间内可见烧录字幕和所选原话；字幕几何不重叠不等于意思兼容。原字幕讲另一句话即不合格，静音不会消除原字幕；换干净展示或有依据的相容区间，不能缩小新花字掩盖问题。配音必须是一句适用于本商品的完整情绪话，不能把肩线参数、泛夸质感或“解开扣子当开衫穿也好看”这种普通穿法说明当作强钩子。详见 [选材反例](quality-and-repair.md#纯剪辑选材的反例)。

正文选完整讲解和可见动作。没有非静音证据或声音待核实的正文不能通过；钩子画面可以无声，但 hook_voice 必须有效。拒绝的候选不能靠改标签变为 accepted，重选区间或补料。

## 3. 一次组装时间线

准备 1 个 accepted hook_picture、1 个 accepted hook_voice，以及足够撑起约 45 秒的 accepted body。body 至少两个完整单元，才能在段落之间放一个转场。

commerce_timeline_apply 的参数：

- hook_picture_job_id / hook_voice_job_id：真实候选检查 job_id。
- body_job_ids：按完整语意排列的真实候选 job_id。
- expected_revision：最新 timeline_get 的 revision。
- voice_speed：默认 1.0；用户允许只加速配音时才用 1.25。
- transition_after_body：在第几个正文单元后转场，一起点数；不能放钩子末尾或最后一个单元后。
- transition：transition_list 中可用的装饰动画；轻横移 slide_push 为起点，按实际内容选择。
- replace_timeline：默认 false。明确重新编排已存在时间线时才 true，工具保留撤销快照。

工具自动：只静音钩子源声、加入独立原声、保持所有画面原速、检查完整配音是否放得下、第一正文单元至少 3 秒、总长接近目标、添加一处段落转场，最后返回实际 time_map。语句放不下应换句子/选区，不能截尾。

先检查这个结构成立，再装饰；不要先制作十张花字后发现没有可用正文。

## 4. 花字和音效

根据画面选择本片 1–3 个主题色，不是从固定全局色板里选。读 typography-layout.md 的平台禁区示意，连续查看准备放字的画面，保存脸、商品动作、烧录字幕的 reserved_boxes。

优先 commerce_typography_add：

- text / emphasis / support：明确主句、其中需突出的一段、可为空的辅助句。text 可用一个换行符拆成两行，不能为填满字段总加小字。长句缩为可读重点；撑不下时先删重复辅助句、改排版或换位置。
- style：钩子用 hook_center；正文按重点用 keyword_stack 或 detail_underline，与安静普通字幕穿插。
- start/end：使用实际 time_map 的输出秒数。
- box 和 reserved_boxes 均为归一化 [x0,y0,x1,y1]，不是像素。hook_center 的 box 中心必须在 x=0.5、靠下允许区域，窄一些也不能整体左移。
- theme_colors：本片共享的 #RRGGBB 列表。描边为中性色，不做整块背景。

工具生成透明 PNG 和可编辑动效，限制动画不超出已审阅矩形，并裁开我们添加的普通字幕的重叠时间。它不会识别人脸、商品或烧录字幕，reserved_boxes=[] 只能表示实际检查后没有额外遮挡。

0.6.1 起在此商品流程内检查字号下限：主字至少为画布宽度的 7%，辅助字至少 3.6%，并复查后续缩放。1080 宽约为主字 76px、辅助字 39px。这只是防止缩成小标签，不是好看的证明；须在手机观看尺寸上与原字幕、认可样例比较。放不下时换方案，不删除真实避让框。参数错误先修正再提交，不逐个像素反复试错。

音效 WAV/MP3 和已有图片用 media_import 导入，取得 asset.id 后再 audio_track_add(role="sfx")；media_register 只收视频，不要将音效提交成视频任务反复重试。media_import 使用同一个重任务资源锁。短音效须许可明确，跟随少量关键词/转场，不逐字响；不要用其他主播的台词充当音效。保留音轨 ID，调音量用 audio_track_update。

最后 commerce_presentation_record(theme_colors,hook_text_ids,body_text_ids,sfx_audio_ids)，传已存在的真实事件 ID。图层几何成立不代表视觉效果或听感合格，下一阶段检查实际输出。

## 5. 预览与验收

commerce_render_submit(preview=true) → render_job_status 成功 → delivery_inspection_submit(project_id,render_job_id) → media_job_status 成功。检查任务与其他重任务共享并发 1；它提供绑定本输出的画面、试听 WAV、逐源选区与逐成片段声音信息。

调用 delivery_review_form 得到完整检查表，初始 not_checked。实际连续看 3 秒钩子、接缝到正文前 8–10 秒以及整片；实际听原声与最终混音。音效单独 audio_review_submit(render_job_id=...)，保存每处对照样本并试听。按事实填写 delivery_review_record，传 inspection_job_id 和已成功的音效检查 job_id。

听不见、看不懂或没有能力审阅时保持 not_checked/false，给出缺口。不是换一个 method 名字就算听过。插件核验证据身份与技术结果，不独立判断自然度。

## 6. 返修与最终交付

失败检查有具体 repair_action：换画面/换原声、延长完整讲解单元、修复文字遮挡或修音效。内容失败必须实际改变相应内容；增加 revision、换无关花字、把 fail 改 pass 都不能清除。执行失败的 render_retry 只重跑旧剪法。

预览检查通过后 commerce_render_submit(preview=false)。最终输出重新 delivery_inspection_submit、必要音效检查、delivery_review_form/record，再 commerce_delivery_get。只有该工具 delivery_ready=true 才称合格成片；preview_reviewed 只表示预览通过。

检查不可用时交待审阅版并列出未验项目，不另写 delivery_ready=true，不用自制 FFmpeg 输出替代登记成片。素材不足按用户已授权来源补料；无可用素材则报告缺口，不擅自换账号、换商品或调用生成。
