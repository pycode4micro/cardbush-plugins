# 千川素材上传与回查

完整 `produce` 的最后一步：将本次成片上传到对应主播的千川素材库，名称带字面量 `Ai`，官方回查确认后结束。适用于纯剪辑和明确选择的生成钩子路线。用户指定仅本地、先审阅、plan/materials/handoff 时不上传；用户单独要求上传已有文件时复用该文件，不重剪。

先读取当前安装的 `qianchuan-mcp-operator/SKILL.md` 及其素材操作参考，发现实际 namespace 和 schema。本页是主 Skill 的编排规则，不新增 `commerce_*` 工具，不把 `upload` 伪装为编辑器阶段参数。Skill 不授予账户写权限；用户已授权完整流程且目标明确时直接完成入库，不逐片再次索要上传确认。

## 1. 锁定文件和主播

- 从本次成功渲染及 `commerce_delivery_get` 结果锁定 project_id、render_job_id、实际输出路径、源主播和款号。默认 `delivery_ready=true` 后入库，不能挑目录里“看起来最新”的另一个视频。
- 如果用户明确要求上传当前待验文件，可以上传该指定文件并记录实际授权；`delivery_ready=false`、未验项目及失败记录保持原状。素材上传成功不证明剪辑合格、平台审核通过或已投放，不据此改写审阅事实。
- 读取本项目已有上传回执，计算实际文件 SHA-256、MD5 和字节数。SHA-256 用于本地身份，千川 `video_signature` 按实际工具返回核对（本次接口使用 MD5）。成片内容变更就是新版本；命名变化不改变文件身份。

## 2. 匹配千川账户

1. 已有配置用 `qianchuan_connection_status`；缺配置才用 `qianchuan_setup_status`，按插件自己的配置方式处理。`qianchuan_health` 读取实际上传目录、大小限制和全局写状态。不要借用其他项目的 `.env`、数据库或旧案例路径。
2. 用 `qianchuan_get_accounts` 读取可访问账户，结合 `qianchuan_list_account_policies` 的配置名称或用户已指定映射定位候选。广告账户名可能为空，不能因此认定没有该主播，也不能拿第一个可写账户代替。
3. 对候选调用 `qianchuan_get_authorized_aweme_accounts(advertiser_id=...)`，按实际分页读取并核对主播抖音身份、授权关系和已知别名。保存核对依据；`advertiser_id`、百应子账号 ID、`aweme_id`、展示用抖音号不是同一种 ID。
4. 同一主播有多个目标广告账户且已有上下文无法唯一确定时，只询问目标账户；不猜测、不自动改传其他主播。对唯一确定目标调用 `qianchuan_get_account_policy`，确认允许 `material_upload` 且写入已启用。被策略阻止时报告实际缺口，不自行开启写入、扩大权限或启用投放能力。

## 3. 命名、暂存和去重

- 默认上传名称：`{款号}_{主播}_{短主题}_Ai_{YYYYMMDD}.mp4`。例如 `KSU1310_珂欣_穿出门不撞衫_Ai_20261024.mp4`，仅为格式示例，不作为下一次账号或商品默认值。按当前业务日期填写，主题简短，不用路径分隔符；已有 `Ai` 标记时不重复追加。新内容版本可增加版本后缀。
- 名称通过上传工具的 **`filename`** 传入，不能仅重命名本地文件后就认定远端名称正确。`Ai` 是用户要求的名称标记；**纯原片剪辑的 `is_aigc=false`**，含生成钩子等生成内容时依据真实来源和当前接口契约填写，不能按文件名推断或隐瞒实际来源。
- `file_path` 必须是千川服务能访问的本地文件，并处于它实际配置的 `video_upload_root` 内。需要时复制到该目录，确认绝对路径、大小和复制前后哈希一致。远程 MCP 使用服务端可见路径和受支持的传输方式，不能把 Agent 本机路径假称为服务端文件。只做暂存复制，不额外转码。
- 先用 `qianchuan_get_videos` 按目标广告账户和 MD5 签名查询已有素材；输入为 `params`，其中 `advertiser_id` 为真实账户 ID，`filtering.signatures` 为签名数组，附 `page` / `page_size`。完整读取相关结果。
- 同账户、同签名、名称符合 `Ai` 规则的现有素材直接复用并回查，避免中断恢复后重复上传。已有同签名素材但名称不符时检查当前插件是否支持改名；无支持能力则报告命名缺口，不谎称改名，也不为了改名盲目重复上传。

## 4. 单次上传

调用实际的 `qianchuan_upload_video`，参数如下：

| 参数 | 来源 |
|---|---|
| `advertiser_id` | 上一步核实的目标账户 ID，按 schema 的整数类型传入 |
| `file_path` | 已验证且服务端可见、位于实际上传目录内的文件 |
| `filename` | 带 `Ai` 的完整素材名称 |
| `is_aigc` | 成片真实来源，不由名称决定 |
| `confirm` | 在现有用户授权范围内填 `true`；不是另加一次审批 |
| `reason` | 本次用户任务、主播、商品及“仅上传素材库，不投放”范围 |

`labels` 仅在用户需要且当前接口支持时填，不编造标签。上传工具负责素材上传和初次回查，不绑定商品或计划。保存响应中的 video_id、material_id、签名、官方 request_id 和初次验证结果；不把大整数 material_id 转成可能丢精度的浮点数，回执以字符串保存 ID。

## 5. 官方回查才算确认

优先复用上传响应里已经匹配成功的官方素材记录；否则调用 `qianchuan_get_videos(params=...)`，在同一 `advertiser_id` 下使用 `filtering.video_ids=[返回的 video_id]`，没有可靠 ID 时用 `filtering.signatures=[实际 MD5]`。返回字段以当前接口为准，本次 `data.list` 行的 `id` 对应 video_id。

逐项核对：目标广告账户、video_id、material_id、filename 与请求完全一致且含 `Ai`、signature 与实际文件一致；返回 size/duration/width/height 时再对照文件。缺少关键字段、名称不符或匹配不一致时保留待确认，不能凭 HTTP 成功、`code=0` 或空列表判断完成。

| 实际结果 | 下一步及可报告状态 |
|---|---|
| 上传与官方回查匹配 | `uploaded_and_verified`；可报告已入素材库且回查确认 |
| 上传前已存在且回查匹配 | `reused_and_verified`；说明复用现有素材，没有新上传 |
| 返回 `status=uploaded` 但 `verified=false` / 回查为空 | `verification_pending`；可能索引延迟，继续只读回查，不重复上传 |
| 超时、断连或 `upload_result_unknown` | 结果未知，先按签名对账；禁止直接重试上传 |
| 官方明确拒绝或账户/文件不匹配 | 记录真实错误，修复实际原因；不改成成功 |

回查短暂为空可在 15、30、60 秒后进行最多三次只读补查；等待期间不占编辑器重任务槽，也不因等待而重剪、转码或重传。仍不可确认时保存回执并准确报告待回查；没有用户安排的持续任务时不擅自创建后台监控。以后恢复先按原账户、原 ID/签名回查，确认先前未入库及错误原因已解决后才考虑新提交。

## 6. 保存回执并结束

在项目输出目录保存 `qianchuan_upload_receipt.json`，至少包含：

- project_id、render_job_id、输出路径、SHA-256、MD5、字节数和实际 `delivery_ready` / 未验项目。
- 主播、款号、目标 advertiser_id 及抖音身份匹配依据、filename、is_aigc、实际用户任务范围。
- 上传/复用状态、video_id、material_id、官方上传和回查 request_id、查询时间及实际匹配结果。保留官方证据来源，不用自己写的 `verified=true` 替代官方回查。
- 本流程是否执行了计划绑定或状态修改；默认均为 `false`，如发现已有绑定只描述实际情况，不冒称从未投放。凭据、Cookie、token 和签名下载 URL 不写入可迁移回执。

**此流程在素材库结束。** 不调用创建/绑定/启用计划、定时投放或预算调整工具；后续投放需要用户另行指示。最终告诉用户主播及目标素材库、带 `Ai` 的名称、素材 ID、是否已回查确认及“未开启投放”。若仍待验/待回查则同时说明，不把上传成功和内容审核混成一个结论。
