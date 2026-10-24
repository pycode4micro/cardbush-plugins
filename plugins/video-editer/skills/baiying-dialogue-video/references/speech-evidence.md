# ASR 与完整句证据（0.6.1+，纯剪辑必经）

0.7.0+ 优先按 [批量执行](efficient-execution.md) 选择工作素材池并批量转写；下文单项入口用于定点补料。
先转写，再选素材。ASR 识别原片声音，不生成配音。所有候选都须关联该源的转写记录；`hook_voice` / `body` 必须绑定真实句子 ID。无声展示钩子也保存 `no_audio` 或 `no_speech_detected` 记录，只豁免可用口播，不豁免来源检查。

## 工具顺序

1. `commerce_workflow_status(project_id)` 返回 `next_tool`、`next_arguments`、`transcripts`。新导入视频先进入 `source_transcription`。照返回参数处理下一段；重连或上下文压缩后重新读取状态，不自行拼旧时间表。
2. 首次用 `speech_runtime_status()` 查依赖与模型。模型缺失时调用 `speech_model_prepare(project_id)` 并等 `media_job_status` 成功；默认下载固定版本的本地 small 模型，不上传素材。离线迁移用 `source_model_dir` 导入完整的 faster-whisper 模型目录，或在宿主设置 `VIDEO_EDITER_ASR_MODEL_DIR`。Python 依赖随插件 requirements 安装；缺依赖是安装缺口，不临时编写 ASR 脚本。
3. `speech_transcribe_submit(project_id,asset_id)` 默认从 0 秒转写，单任务最多 600 秒。长源由状态返回下一个未覆盖窗口；句子跨窗口时重新转写覆盖完整句的重叠窗口，不把不同任务的半句手拼。已排队任务只查原 ID；失败修环境后走 `media_job_retry`。语种默认 `zh`，其他语种显式指定。默认 `device=auto` 优先 CUDA，实际设备与 CPU 回退原因写入结果。
4. 成功后先用 `speech_timeline_get` 查原片完整句和 ASR 字词未覆盖空档，按 [原始回放选材](original-source-selection.md) 核实转写覆盖与展示画面；空档不是无声或闭嘴证明。再用 `speech_candidates_get(project_id,job_id,role="all")` 分页读取完整候选、前后文、时长、边界依据与低置信字词。状态查询只返回简短回执及转写文件路径，不反复塞入整批逐词 JSON；不要为了找一句话通读全部原始文件。选钩子可用 `role="hook_voice"` 筛 0.8–4 秒，但这只筛时长，不认证情绪。正文可以连接相邻句子 ID，组成一个完整卖点和动作单元，不能只挑一个孤立关键词。
5. `speech_range_validate(project_id,job_id,sentence_ids)` 返回源区间及校验结果。默认保留句首尾很短的空隙；可选 start/end 也必须覆盖全部所选字词，不能侵入相邻未选字词或超出转写范围。
6. 用 `commerce_speech_candidate_submit(project_id,transcript_job_id,sentence_ids,role)` 直接提交配音/正文证据检查，不手抄秒数。该工具再次执行时间校验；结果成功后，继续实际视听与 `commerce_candidate_review` / `defer`。展示画面仍用 `commerce_candidate_submit(...role="hook_picture")` 指定实际看过的 2–4 秒，工具查其转写覆盖；ASR 不能判断嘴是否在动。

普通 `commerce_candidate_submit` 的口播分支也要求 `transcript_job_id` 和 `sentence_ids`；没有转写、跨素材引用、切首尾字、跳过中间句、改写转写文件都不能通过。正式时间线和渲染继续重验这些绑定。旧项目保留历史产物，缺新证据的候选须先补转写再重新检查，不自动追认。

## 完整句怎么选

- 0.6.1 将紧贴相邻有效时间的零时长字词保存在不可单独裁切的词组里，返回 `grouped_word_timings` 与 `grouped_word_timing_requires_listening`。原始词、概率、时间仍在证据内；没有虚构逐字时长，也没有删除说过的字。该组需要整体核听，不能当成精确声学对齐。孤立零时长、倒序、越界等仍失败；先看具体原因，不盲目重复同一失败任务或自行缩窗绕过问题。
- 小数点不是句号：例如 `14.` 与 `3微米以内` 必须合在一起。新版不在这里断句，旧转写若已拆开，选区工具也会要求包含相邻句 ID。数值、单位、否定、比较和条件语仍要核听；不要把结构校验通过误解为语义完整。

- 一次读候选原文及上下文。像“里面搭吊带，外面还能搭大衣”可以用连续两个 ID 组成同一个穿法单元；“如果里面穿了打底，就不会扎”不能只取“就不会扎”升级成无条件承诺。
- 低置信词、ASR 分段形成的边界、窗口首尾都可能不可靠。先对照源字幕/商品资料与声音；源字幕只能辅助文本核对，不能替代试听。不能为了过门槛自己覆盖转写 JSON、把错词包装成卖点。
- 结构化候选的 `meaning_status=unreviewed` 是默认事实。ASR 的停顿、标点和分段只用于提案，`timestamp_valid=true` 只表示没有违反本次识别出的字词范围；不是强制对齐到真实声学边界，更不等于句意、情绪和听感合格。
- 如果短钩子不够完整，换候选，或在用户已授权时调整独立原声音速；画面与正文仍原速。不为凑 3 秒截掉条件或尾字。
- `no_speech_detected` 不等于音频无声，可能是音乐、噪音或识别失败。不能用于正文/配音；可选另一个源或诊断转写，不铺音乐冒充口播。展示仍必须通过全身自然展示检查。全都没有识别出讲话时状态为 `materials_insufficient`、`next_tool=null`，先报告/解决素材或识别缺口，不无限重试同一请求。

## 资源与恢复

模型准备、音频解码、ASR、候选检查和重试共用并发 1 的队列/资源锁。ASR 使用可取消的受监控子进程，CPU 线程遵守既有资源策略。当前默认后端的 GPU 是 CUDA；QSV/AMF/VideoToolbox 编码成功并不代表能用这个 ASR GPU 后端。无兼容 GPU 且允许回退时使用 CPU int8 并记录原因；禁止 CPU 回退时失败并保留记录。实际 CUDA 推理失败也通过队列重试，不在旁路开第二个重任务。

本地 ASR 依赖和 CUDA 要求参考 [faster-whisper 官方说明](https://github.com/SYSTRAN/faster-whisper#requirements)。不为启用 ASR 擅自修改系统驱动或降低资源限制。

ASR 只产生证据，不执行剪辑。用户要求“先不实际剪辑”时，可以转写、读取候选、校验时间戳并验证拦截；不要提交候选视频预览、组时间线或渲染。
