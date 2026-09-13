# 安全门与排障

## 安全门

真实创建必须同时满足：

1. advertiser 硬白名单仅允许 `1811432276115675`。
2. OAuth token 有效且业务接口可访问目标账户。
3. launch authorization 已 configured、locked，并允许相应创建动作。
4. 商品、抖音号、素材、目标和场景命中授权范围。
5. 官方实时商品、库存、余额和配额预检通过。
6. 单次初始预算和每日创建次数未超限。
7. account policy 写入开启且 allowed_actions 放行。
8. 全局 `QIANCHUAN_WRITE_ENABLED=true`。
9. 请求包含 `confirm=true` 和具体 reason。
10. 冷却窗口、审计和急停状态允许。

任何一项失败都报告 blocked，不建议绕过。

## 常见结果

### `setup_required`

把响应中的所有问题一次性问给用户。等用户明确回答后保存；不要拆成多轮猜测，也不要使用报表默认值。

### `write_enabled=false`

继续读取、分析和预演。除非用户明确要求进入真实测试，不建议开启写入。开启时还要同步检查 account policy，而不是只改 `.env`。

### `Invalid authorization header`

这是本地服务 Bearer API Key 解析问题，不是千川 OAuth 失败。重新从 `.env` 精确读取 `LOCAL_API_KEY`，去除引号和空格，构造 `Authorization: Bearer <key>`。

### 目标账户不在 OAuth 账户列表

管理账户列表可能展示上级/店铺账户。用目标 ID 调用最小业务读取接口：成功 code=0 表示业务可访问；硬白名单会阻止其他 ID。不要因此反复授权错误账户。

### `hard allowlist blocked`

停止。不要改目标 ID 或扩大白名单。当前 Skill 固定账户 `1811432276115675`。

### `no_data` 或成功但列表为空

区分三种情况：日期无数据、没有活跃广告、查询维度不匹配。依次缩小到单日、查询广告列表/详情、核对 marketing_goal。历史数据可用于说明，不可直接驱动当前 live 写入。

### 商品未返回或库存不足

停止创建。重新调用可投商品接口；不要把搜索结果、旧广告 product_id 或外部商品编码当作当前可投商品。

### 余额字段缺失

当 launch authorization 设置非零最低余额时视为阻断。不要把其他钱包字段猜成 `account_valid`。

### 达到配额、每日次数或冷却限制

等待自然窗口或让用户明确修改锁定边界。不要并发、换名字或用 raw tool 规避。

### 官方参数/枚举错误

读取 `qianchuan_list_tool_specs` 和官方接口文档，修正完整 payload 后重新 dry-run。不要在真实写入上试错。

### “已不再支持商品标准投放的计划操作”

把它视为产品链路迁移信号，不是普通参数错误。停止调用标准 `qianchuan_create_campaign`，改走 PC 千川全域：

```text
discover_launch_assets(launch_type="UNI_AWEME")
→ preview_launch_plan(launch_type="UNI_AWEME", campaign_payload=null, ad_payload=全域payload)
→ create_uni_aweme_ad
```

不要猜 `marketing_scene="UNI_PROMOTION"`。锁定 Launch Authorization 的 `allowed_marketing_scenes` 需要包含本地内部哨兵 `UNI_AWEME`，调用发现/预演时传 `launch_type="UNI_AWEME"`；两者都不会成为官方创建 payload 字段。全域计划由创建接口直接返回 `data.ad_id`，不需要 Campaign。

### 误把随心推全域订单当作全域计划

`qianchuan_create_aweme_uni_promotion_order` 是独立的抖音端/随心推订单产品，不是 `qianchuan_create_uni_aweme_ad` 的前置步骤，也不能用来补一个 Campaign ID。除非用户明确要求该订单产品，否则不要调用。

### 全域创建响应成功但回读未找到

只有创建响应 `code=0` 且存在 `data.ad_id` 才进入核验。随后用全域计划列表、全域详情和 `qianchuan_get_uni_promotion_ad_products` 回读已绑定商品；`qianchuan_get_uni_promotion_products` 只是创建前候选接口。不要查标准 Campaign 列表。短时查不到时报告 `verification_pending` 并等待；禁止以“可能失败”为由重复提交。

### 商品全域提示“请使用多品结构”或“投放商品为空”

先调用 `qianchuan_get_uni_aweme_create_contract`。当前 `VIDEO_PROM_GOODS` 商品全域同时使用
顶层 `product_ids=[...]` 和最小化的 `multi_product_creative_list`；每项只发送
`product_id`、`creative_type="PROGRAMMATIC_CREATIVE"` 与 `hide_in_aweme=true`，
两个商品集合必须完全一致。两个平台手工计划详情均返回该布尔字段。
自动素材链路还必须发送 `programmatic_creative_media_list` 的三个空数组；不要添加
`aweme_item_id=0` 的空视频行。控成本排期必须使用经过真实验证的 QCPX、视频排期、日期、
`daily_delivery_time>=0.5` 与深度转化目标字段。完整合约已创建并暂停计划
`1870853765929163`。
省略 `creative_setting`、`creative_card`、`product_channel_info`、`uni_product_info`、
`product_infos` 和 `products`，也省略详情响应中多品列表下的 null/空值/系统默认字段。
当 `smart_bid_type=SMART_BID_CONSERVATIVE` 时省略 `roi2_goal`。
预演与创建必须复用同一份 payload；
预演不通过时不得调用真实创建。

### 商品字段已齐全但官方仍返回“参数错误”

检查是否误把详情返回字段或旧 SDK 字段带入创建请求，尤其是
`creative_setting`、`multi_product_creative_list` 子字段、`creative_card` 和
`product_channel_info`。平台详情中的默认值是返回态，不是自动可用的创建入参。
修正合约后重新预演，获得用户新的真实写授权后才允许下一次提交。

若所有官方字段模型均已通过，停止继续猜 payload。记录并原样报告业务 `code`、
`request_id` 和完整请求；使用三个或更多 request_id 向巨量引擎开放平台提交字段级
排查。先以 `marketing_goal=VIDEO_PROM_GOODS, scene=CREATE` 查询全域抖音号资格；
未带 CREATE 过滤的结果不可用于判断授权状态。即使 `has_authorized=true`，只要
非 SELF 账号 `can_control_uniprom=false` 必须停止创建，先走可授权店铺查询和经用户确认的全域授权申请。`auth_type` 包含 `SELF` 的自营号则以 `has_authorized=true`、店铺权限正常且商品全域未禁用为创建资格。
MCP 会把官方业务码、HTTP状态、request_id 和原始响应写入审计。

### 全域优化工具字段错误

- 预算：`update_budget_infos=[{ad_id,budget,previous_budget}]`
- ROI2：仅详情确认支持时使用 `update_roi2_infos=[{ad_id,roi2_goal}]`；`SMART_BID_CONSERVATIVE` 禁止设置
- 状态：`{ad_ids,opt_status}`，其中状态只用 `ENABLE` / `DISABLE`

全域 ID 不得交给标准预算/ROI/状态接口。服务会拒绝 `DELETE`；不要改用 raw 写绕过。

### 无人值守状态调用失败或重复暂停

新调用只使用 `qianchuan_update_uni_promotion_ad_status(payload={advertiser_id,ad_ids,opt_status}, confirm=true, reason=...)`。
不要把写体放进 `params`；这只为历史客户端保留兼容，不能作为执行格式。

每次状态变更先读取全域计划详情：已是目标状态就记录 skipped，不提交官方写请求；详情无法确认时 fail closed。终态 `DISABLE` 每个计划每轮只提交一次，回读确认后结束写入循环并保存一份 strategy review。发生超时/未知结果时先回读，不得用重复 `DISABLE` 或 `ENABLE` 试探。

### 全域计划升级为乘方计划

官方接口是 `POST /v1.0/qianchuan/ad/overall_marketing/update/`，但它不是通用的
“overall marketing”自由编辑接口。请求只接受 `advertiser_id` 与
`ad_id_list=[{ad_id,roi2_goal}]`；逐条结果位于 `data.upgrade_msg`。

先读全域&乘方计划详情，确认精确 ID 已经 `DISABLE`。使用
`qianchuan_upgrade_uni_promotion_to_multiplier`，不要调用 raw 工具或旧通用别名。该工具
会拒绝批量条目、非数值/越界 ROI、详情不匹配和运行中计划；升级不扩大任何资产或预算授权。

顶层 `code=0` 但目标条目 `flag=false` 仍是失败。报告对应 `error_code`、
`error_message` 与 `request_id`，回读详情后停止；不要在同一会话用不同 ROI 或相同 payload
反复提交。若计划正在投放，先使用可逆 `DISABLE` 并确认回读，再由用户或已授权无人值守任务
明确发起升级。

### 写请求超时或结果不明

不要立即重试。按产品链路使用标准或全域对象查询，并结合名称、商品、抖音号、创建时间和审计记录确认是否已落地；只有明确未创建才重新提交。

## 急停

任一方式都应阻止后续写入：

- `QIANCHUAN_WRITE_ENABLED=false`
- account policy `write_enabled=false`
- 无人值守预算急停文件 `data/AUTONOMY_STOP`

急停后仅执行读取、状态确认和结果汇报。

## 策略异常保护

- 找不到上轮 strategy review 或无法确认当前阶段时，降级为只读诊断，不猜测继续放量。
- 当前数据窗口未成熟或报表截断时，不用即时 ROI 覆盖成熟窗口结论。
- 同一轮同时修改预算、出价和 ROI 目标会降低可解释性；除紧急止损外，优先一次只改一个主要变量。
- 低 ROI 探索不是无限亏损授权。没有明确预算/动作边界时，只能在已有硬边界内运行；触边后停止新增量。
- ROI 达标但销量没有相对基线增长时，不得报告“策略成功”。
- 库存接近下限、退款异常、审核拒绝或官方对象状态异常时，策略目标自动让位于保护和恢复。
- 预算绝对最小变更额与账户最大变更比例没有交集时，保持不动作并报告配置冲突；不要通过通用 ad/campaign 更新绕过。
- ROI 目标写入上限为 100。不得为了满足“100+”措辞提交大于 100 的目标；只把超过 100 作为可能的实绩结果。

## 信息保护

永不输出或写入报告：access token、refresh token、LOCAL_API_KEY、OAuth HMAC secret。只报告 token 是否存在、OAuth 是否可用、官方 request_id 和业务对象 ID。
