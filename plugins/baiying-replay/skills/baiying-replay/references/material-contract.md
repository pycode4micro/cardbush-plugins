# 素材状态与交接

| 状态 | 含义与下一步 |
|---|---|
| queued / running | 后台工作中，用 material_status 查询，不新建重复任务 |
| waiting_for_clips | 正在有限等待百应生成，查询进度或按用户要求停止 |
| ready | 所设条数、保守时长和来源数满足；尚需内容审阅 |
| needs_input | 当前策略素材不足，按 alternatives 和已有偏好调整来源后 resume |
| partial | 下载失败、扫描不完整、生成超时/拒绝或达到预算；查看 reason 与 clip_plans |
| source_exhausted | 接口扫描完整，当前策略可用来源已尝试，仍不足；不代表历史上不存在其他直播 |
| interrupted | 后台进程不在，resume 原 job_id，复核文件并查询原生成计划 |
| failed | 检查登录、权限、网络、依赖或目录后 resume；不能推断外部任务未提交 |
| cancelled | 保留素材，百应已提交任务可能仍生成；恢复时查原计划 |

`running` 表示进程仍存在。`cancel_requested=true` 表示已请求停止，单个网络/解码操作结束后才停止。

恢复时只发送改变的参数。可调整目标条数/秒数/来源数、质量、原回放/智能切片/重剪策略、相似视频计数、扫描页数、下载次数/容量和生成轮数。`retry_failed=true` 才重置耗尽的失败下载次数。运行中重复 resume 不启动第二个进程。

生成等待超时会保留原计划，之后 resume 再查一次，出现新视频就下载。不通过新建计划重发未知提交；跨素材任务也拦截同片段尚未确认完成的提交。

## 足量指标

- `verified_video_count`：技术检查通过且符合来源策略的文件数，不含完全重复文件。
- `counted_video_count`：用于目标条数验收的数量，默认不重复计入高度相似且尚未审阅的候选。
- `known_source_count`：计入验收的视频来自多少个已知原片段，未知来源 ID 不冒充独立来源。
- `total_duration_seconds`：验证通过文件的总时长。
- `conservative_duration_seconds`：排除未放行的相似候选，再按原片段分组取最长时长相加。
- `shortage`：缺少的条数、秒数和来源数，全部满足才 targets_met=true。
- `semantic_review_required=true`：不同片段可能也重叠，尚未识别人物和讲解内容，不能直接当成成片可用时长。

相似度来自全片分布的低分辨率画面样本，可能误判同机位不同讲解。文件保留供审阅。SHA-256 完全相同的文件只计一份；损坏或变更的已有文件不覆盖，重新下载为其他文件名。

## 文件交接

`materials.json` 的 schema_version 为 `baiying-materials/v1`，包含任务状态、账号/商品、策略、盘点、所有素材及失败/重复原因、媒体证据和缺口。签名链接记录获取时间，之后下载应刷新；本地有效文件可直接交接。

每个已验证素材包含 source（smart_clip/original_fragment）、video_id、fragment_id、本地 path、下载地址、推荐标签和 media：实测时长、宽高、帧率、编码、是否有声音、字节数、SHA-256、完整解码是否通过。similar_to 指向待审阅的近似画面候选。

`editor_handoff.json` 的 schema_version 为 `baiying-editor-handoff/v1`。仅纳入技术验证通过、符合当前策略且非完全重复的文件；不足时也输出部分清单。每项有 source_path、来源片段、哈希、媒体信息和建议的导入工具。

将 import_arguments 与实际 project_id 合并传给 media_register，短视频也进入异步队列。保存账号商品范围、asset_key、sha256、project_id、job_id 和成功返回的 asset_id；再次交接先查这个映射及原任务，避免创建重复导入。源文件哈希变化时按新素材处理，不沿用旧证据。

queued/running 不等于导入完成，resource_wait 是资源等待原因；轮询 media_job_status，只取 succeeded.result，并核对 result.asset.identity.sha256 与交接哈希一致。失败、取消或中断后需要恢复时，用 media_job_retry，记录其新的 job_id 和 retry_of；不重复 media_register 或绕过队列同步导入。排队期间断开会话后仍可凭项目和任务 ID 查询。

交接文件不虚构项目 ID、时间线或镜头判断。以 summary 判断足量程度，部分素材可导入但仍报告原缺口；以实际视听内容决定是否使用 similar_to 候选。只验证剪辑前流程时，不调用时间线编辑或渲染工具。
