# MCP 工具映射

## 目录

- 会话与授权
- 商品和创建资产
- 报表与诊断
- 创建与投后
- 经营档案与无人值守
- Agent 策略编排
- 账户策略

## 会话与授权

| 目的 | 工具 | 关键判断 |
|---|---|---|
| 服务状态 | `qianchuan_health` | OAuth 已配置；写开关只影响写入 |
| OAuth 授权记录 | `qianchuan_oauth_authorizations` | 检查 active token，不输出敏感 token |
| 管理账户列表 | `qianchuan_get_accounts` | 不以是否列出目标 ID 作为业务权限唯一依据 |
| 原始工具规格 | `qianchuan_list_tool_specs` | 只在确认路径/风险/读写属性时使用 |

## 商品和创建资产

| 目的 | 工具 | 使用方式 |
|---|---|---|
| 聚合投前发现 | `qianchuan_discover_launch_assets` | 新投放首选；PC 千川全域传 `launch_type="UNI_AWEME"`，自动使用全域候选商品和全域抖音号，并返回余额、配额 |
| 店铺可投商品 | `qianchuan_get_available_products` | 用 product filter 和 page_size 控制结果量 |
| 抖音号可投商品 | `qianchuan_get_aweme_available_products` | 已确定 aweme 场景后使用 |
| 授权抖音号 | `qianchuan_get_authorized_aweme_accounts` | 检查短视频/直播带货权限和状态 |
| 全域授权抖音号 | `qianchuan_get_uni_authorized_aweme_accounts` | PC 千川全域身份必须以此接口为准 |
| 全域计划可投商品 | `qianchuan_get_uni_promotion_products` | 全域直建前按官方返回值确认商品和库存；不要只复用标准商品接口结果 |
| 全域计划已绑定商品 | `qianchuan_get_uni_promotion_ad_products` | 创建后按 `ad_id` 核对计划实际绑定商品；不要与候选商品接口混用 |
| 余额 | `qianchuan_get_account_balance` | 使用 `account_valid` 做投前校验；先核对单位 |
| 在投配额 | `qianchuan_get_ad_quota` | 创建前检查可用容量 |
| 素材库 | `qianchuan_get_materials` | 选择已有素材；分页读取 |
| 图片/广告素材 | `qianchuan_get_images`, `qianchuan_get_ad_materials` | 按创建请求所需类型查询 |
| 上传商品卡方图 | `qianchuan_upload_image` | 传 `require_square_750=true`；仅上传素材库，必须记录 `image_id` 与文件签名 |
| 读取全域计划素材 | `qianchuan_get_uni_promotion_ad_materials` | 绑定前后均读取；按 image_id → product_id 做精确核验 |
| 绑定商品卡方图 | `qianchuan_add_uni_promotion_product_card_images` | 传 `{advertiser_id,ad_id,bindings:[{product_id,title,image_ids}]}`；专用安全封装 |

商品返回的 `id` 是创建广告使用的商品 ID；`inventory` 用于最低库存边界。`aweme_id` 是创建计划和拉取抖音素材使用的身份 ID。不要使用展示用 `aweme_show_id` 替代。

### 全域商品卡方图素材链路

```text
本地 750x750 质检 + manifest
→ qianchuan_upload_image(require_square_750=true, is_aigc=true)
→ 回读 image_id / material_id / request_id，按文件签名去重
→ get_uni_promotion_ad_detail + get_uni_promotion_ad_products + get_uni_promotion_ad_materials
→ add_uni_promotion_product_card_images（先一个商品小批量）
→ get_uni_promotion_ad_materials 逐张核验 image_id 只属于预期 product_id
```

商品卡方图使用专用工具，禁止手写通用嵌套 payload。官方实际约束：一个商品一个多品节点；一个 `image_material` 内只含一个 `image_id`；`image_mode="SQUARE"` 必须搭配一个该商品专属的 `COMMODITY_CARD` 标题；标题长度 5–55，且不要为每张图重复相同标题。上传成功、绑定成功、审核通过和开始投放是四个不同状态，逐一回读。

## 报表与诊断

| 目的 | 工具 |
|---|---|
| 经典计划账户数据（不含全域） | `qianchuan_get_account_report` |
| 经典计划广告数据（不含全域） | `qianchuan_get_ad_report` |
| 素材数据 | `qianchuan_get_material_report` |
| 搜索词 | `qianchuan_get_search_word_report` |
| 全域计划数据 | `qianchuan_get_uni_promotion_report`，先用 `qianchuan_get_uni_promotion_report_config` 确认主题、维度与指标 |
| ROI 诊断 | `qianchuan_diagnose_roi` |
| 素材疲劳 | `qianchuan_analyze_material_fatigue` |
| 官方建议 | `qianchuan_suggest_roi_goal`, `qianchuan_suggest_budget` |
| 效果估算 | `qianchuan_estimate_effect` |
| 本地快照 | `qianchuan_sync_ad_snapshots`, `qianchuan_list_ad_snapshots` |
| 策略方案 | `qianchuan_build_roi_strategy`, `qianchuan_get_roi_decision` |

减少截断：缩小日期范围，使用必要 fields，设置合理 page/page_size，按日或对象分页汇总。官方返回成功但 list 为空时仅表示当前查询未返回数据，不是接口失败，也不能据此认定零消耗。按[空结果排查](safety-and-troubleshooting.md#no_data-或成功但列表为空)区分日期、活跃计划与查询口径；不要用经典报表补充或替代全域报表后宣称已覆盖账户总消耗。

## 创建与投后

### 先选择正确产品链路

| 产品链路 | 创建工具 | 对象关系 | 何时使用 |
|---|---|---|---|
| PC 千川全域 | `qianchuan_create_uni_aweme_ad` | 一次直接创建全域计划，响应 `data.ad_id` 即计划 ID | 商品标准投放被官方废弃后的主链路 |
| 标准 Campaign/广告 | `qianchuan_create_campaign` → `qianchuan_create_ad` | Campaign 与广告两段创建 | 仅当官方仍支持当前目标和场景 |
| 随心推全域订单 | `qianchuan_create_aweme_uni_promotion_order` | 独立的抖音端订单产品 | 只有用户明确要求随心推订单时 |

全域计划不需要标准 Campaign 前置。不要为了获得 `campaign_id` 调用已废弃的标准商品投放，也不要用随心推订单替代 PC 千川全域计划。

### PC 千川全域直建链路

```text
health + get_launch_authorization + get_account_policy
→ discover_launch_assets(launch_type="UNI_AWEME")
→ get_uni_authorized_aweme_accounts(filtering={marketing_goal:"VIDEO_PROM_GOODS",scene:"CREATE"})
→ SELF 自营号不强制要求 can_control_uniprom=true；非 SELF 且 can_control_uniprom=false：get_uni_authorizable_shops → 经用户确认后 apply_uni_promotion_authorization → 重新查资格
→ 必要时 get_uni_authorized_aweme_accounts / get_uni_promotion_products 分页补查
→ get_uni_promotions 查重
→ get_uni_aweme_create_contract 读取精确字段与模板
→ 从目标商品 img/square_image_list 提取 /obj/ 后的 image ID，并用 get_images 精确核验
→ preview_launch_plan(launch_type="UNI_AWEME", campaign_payload=null, ad_payload=完整全域payload)
→ create_uni_aweme_ad(confirm=true, reason=具体依据)，只提交一次
→ 验证 code=0 且 data.ad_id 存在
→ get_uni_promotions + get_uni_promotion_ad_detail + get_uni_promotion_ad_products 回读核验
→ get_uni_promotion_report
→ update_uni_promotion_ad_budget / update_uni_promotion_ad_roi2_goal / update_uni_promotion_ad_status
→ 再次回读 + create_strategy_review
```

`launch_type="UNI_AWEME"` 是本地预演的校验分支；锁定授权的 `allowed_marketing_scenes` 需包含同名内部哨兵 `UNI_AWEME`。它不应塞进官方 `ad_payload`，也不应伪造成 `marketing_scene="UNI_PROMOTION"`。完整 payload 只使用全域创建工具规格/官方文档支持的字段。

### 多计划细分编排

```text
只读诊断零消耗/审核/资格/排期/预算利用率
→ 定义 Core / Growth / Explore 计划池与每个计划唯一商品或同质分组
→ get_launch_authorization：确认 max_ads_per_day >= 本轮待创建数量
→ 每个计划独立：查重 → get_uni_aweme_create_contract → preview → 单次 create → 全域详情/商品回读
→ 分别记录计划 ID、预算锚点、阶段 ROI、停止条件和 strategy review
→ 达到最小样本后再比较计划级报表；无消耗先诊断
```

`max_ads_per_day` 是锁定创建边界，当前值不足时必须先由用户明确更新，不能由 Agent 自动提高。多个全域计划的总预算要同时满足用户总额和每计划的官方下限；不要批量提交创建请求。

创建资格查询必须带当前 `marketing_goal` 与 `scene="CREATE"`。只有目标抖音号返回
`has_authorized=true`，商品全域同时返回 `has_shop_permission=true` 且
`is_product_uni_prom_disabled=false` 才能预演通过。未带场景过滤的查询用于其他流程，
其授权字段不能覆盖 CREATE 场景结论。`auth_type` 含 `SELF` 时不强制要求 `can_control_uniprom=true`；非 SELF 抖音号授权但 `can_control_uniprom=false` 时，
禁止创建；按锁定抖音号和官方可授权店铺走受控授权申请。

商品全域 `VIDEO_PROM_GOODS` 的单商品也必须使用：

```json
{
  "product_ids": [3825578976894648335],
  "multi_product_creative_list": [
    {
      "product_id": 3825578976894648335,
      "creative_type": "PROGRAMMATIC_CREATIVE",
      "hide_in_aweme": true
    }
  ],
  "programmatic_creative_media_list": {
    "block_video_material": [],
    "title_material": [],
    "video_material": []
  },
  "delivery_setting": {
    "budget": 300,
    "smart_bid_type": "SMART_BID_CUSTOM",
    "roi2_goal": 50,
    "qcpx_mode": "QCPX_MODE_ON",
    "video_schedule_type": "SCHEDULE_FROM_NOW",
    "start_time": "2026-07-16",
    "end_time": "2026-07-18",
    "daily_delivery_time": 0.5,
    "deep_external_action": "AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI",
    "enable_aigc_creative": true
  }
}
```

顶层 `product_ids` 和多品列表表示同一实际投放商品集合，必须完全一致。多品列表每项
仅发送 `product_id`、`creative_type="PROGRAMMATIC_CREATIVE"` 和
`hide_in_aweme=true`；两个手工计划详情均返回该布尔字段。当前商品全域创建省略
`creative_setting`、`creative_card`、
`product_channel_info`、`uni_product_info`、`product_infos` 和 `products`。
详情响应中多品列表下的 null、空数组或系统默认字段不能自动复制到创建请求。
`SMART_BID_CONSERVATIVE` 必须省略 `roi2_goal`。平台样本计划
`1870839628901003` 提供了控成本计划的最小多品结构依据；真实的 `product_ids` 单字段
请求已被官方以“商品全域计划请使用多品结构”拒绝。

自动素材链路的 `programmatic_creative_media_list` 必须保留三个空数组；调试器生成的
`video_material:[{"aweme_item_id":0}]` 是无效占位，必须删除。控成本完整模板已真实创建
计划 `1870853765929163`，创建后立即暂停并回读为 `DISABLE`。官方详情将请求中的
`enable_aigc_creative=true` 归一为 `false`，不要仅凭请求值报告 AIGC 已启用。

创建前查重至少组合：计划名称、商品 ID、抖音号 ID、近期创建时间和本地审计。若上次写请求超时或结果未知，先回读确认，禁止直接重发。业务失败后修正 payload 也需要用户重新明确授权下一次真实提交。

全域写后核验：

1. 创建响应同时满足 `code=0` 和 `data.ad_id` 存在；保存 `request_id`。
2. 用 `qianchuan_get_uni_promotions` 找到该 ID，不用标准 Campaign 列表判断。
3. 用 `qianchuan_get_uni_promotion_ad_detail` 核对名称、预算、ROI 目标、抖音号与状态。
4. 用 `qianchuan_get_uni_promotion_ad_products` 核对该 `ad_id` 实际绑定目标商品；`qianchuan_get_uni_promotion_products` 只用于创建前候选发现。
5. 任一步暂时查不到时报告 `verification_pending`，等待查询窗口，不重复创建。

### 全域计划升级为乘方计划

这是对已有 PC 全域计划的单对象升级，不是创建、预算更新或普通 ROI2 调整。官方接口为
`POST /v1.0/qianchuan/ad/overall_marketing/update/`，请求只包含：

```json
{
  "advertiser_id": 1811432276115675,
  "ad_id_list": [{"ad_id": 1871046310971595, "roi2_goal": 50}]
}
```

MCP 不暴露通用 payload 写入，改用：

```text
get_uni_promotion_ad_detail(ad_id)
→ 确认 exact ad_id 且 opt_status/status 包含 DISABLE
→ qianchuan_upgrade_uni_promotion_to_multiplier(advertiser_id, ad_id, roi2_goal,
    confirm=true, reason=具体业务理由)
→ 检查 data.upgrade_msg 中该 ad_id 的 flag
→ get_uni_promotion_ad_detail + get_uni_promotions 回读
→ create_strategy_review
```

需要 Account Policy `ad_update`、全局写开关、具体 `confirm/reason` 与冷却窗口。工具限制为单计划，避免批量升级误操作；不改变商品、抖音号、素材或预算白名单。顶层 `code=0` 不足以说明成功，必须检查该计划的 `upgrade_msg.flag=true`；失败时读取 `error.error_code`、`error.error_message` 和 `request_id`，禁止重复提交。

### 标准商品广告链路（仅官方仍支持时）

```text
discover_launch_assets
→ get_launch_authorization
→ preview_launch_plan(launch_type="STANDARD")
→ create_campaign
→ 将 campaign_id 写入广告请求
→ preview_launch_plan(launch_type="STANDARD")
→ create_ad
→ get_ad_detail
→ get_ad_reject_reasons
→ get_ad_learning_status
→ get_ad_report
```

创建接口：

- `qianchuan_create_campaign`
- `qianchuan_create_ad`
- `qianchuan_create_uni_aweme_ad`：PC 千川全域计划直建，不需要 Campaign
- `qianchuan_create_aweme_uni_promotion_order`：随心推全域订单，不是全域计划前置步骤

写请求必须携带 `confirm=true` 和具体 `reason`。标准 Campaign 创建成功响应应包含 `campaign_id`；标准广告或全域计划创建成功响应应包含 `ad_id`。收到 transport timeout 或未知结果时先查询，不直接重发。

投后读取：

- `qianchuan_get_campaigns`
- `qianchuan_get_ads`
- `qianchuan_get_ad_detail`
- `qianchuan_get_ad_reject_reasons`
- `qianchuan_get_ad_learning_status`
- `qianchuan_get_uni_promotions`
- `qianchuan_get_uni_promotion_ad_detail`
- `qianchuan_get_uni_promotion_products`
- `qianchuan_get_uni_promotion_ad_products`

全域投后专用写入：

- `qianchuan_update_uni_promotion_ad_budget`：payload 使用 `advertiser_id` 与 `update_budget_infos=[{"ad_id": ..., "budget": ..., "previous_budget": ...}]`
- `qianchuan_update_uni_promotion_ad_roi2_goal`：仅对详情确认支持 ROI2 的计划（如适用的 `SMART_BID_CUSTOM`）使用；payload 使用 `advertiser_id` 与 `update_roi2_infos=[{"ad_id": ..., "roi2_goal": ...}]`，不得对 `SMART_BID_CONSERVATIVE` 设置 `roi2_goal`
- `qianchuan_update_uni_promotion_ad_status`：payload 使用 `advertiser_id`、`ad_ids` 与 `opt_status`；只允许可逆的 `ENABLE` / `DISABLE`，不得提交 `DELETE`
- `qianchuan_upgrade_uni_promotion_to_multiplier`：只升级一个已暂停的 PC 全域计划；参数为 `advertiser_id`、`ad_id`、`roi2_goal`，不直接接收官方批量 payload

不要用 `qianchuan_call_raw_tool` 执行全域写入。全域创建专用封装执行 launch authorization 与实时商品库存/余额预检；全域预算、ROI2、状态封装执行账户策略、`confirm/reason`、冷却和审计，预算另以 `previous_budget` 检查变化率。raw 写不能替代这些安全门。

## 经营档案与无人值守

| 档案/动作 | 获取 | 保存/执行 |
|---|---|---|
| ROI、毛利、退款、成本、预算上限 | `qianchuan_get_autonomy_profile` | `qianchuan_save_autonomy_profile` |
| 商品/身份/素材/创建预算/次数 | `qianchuan_get_launch_authorization` | `qianchuan_save_launch_authorization` |
| 新投放预演 | — | `qianchuan_preview_launch_plan` |
| 固定目标无人值守预算循环 | 先读取 autonomy profile | `qianchuan_run_autonomous_budget` |

档案一旦 configured 就复用。更新必须源于用户明确请求，不根据官方建议自动改档案。

`qianchuan_run_autonomous_budget` 只做固定目标下的预算增减，不负责分阶段 ROI、创建计划/广告、素材轮换或多场景决策。需要这些能力时让 Agent 使用下列工具编排，不把一次工具调用描述为持续运行。

## Agent 策略编排

```text
health + locked profiles + account policy
→ list_strategy_reviews 恢复上轮状态
→ official object state + learning/reject status + reports
→ diagnose_roi / build_delivery_plan / material_fatigue
→ 选择场景与阶段，执行边界检查
→ create / update_budget / update_bid / update_roi_goal / update_ad_status
→ 读取官方结果核验
→ create_strategy_review 保存决策上下文
```

策略记忆：

- `qianchuan_list_strategy_reviews`：读取近期场景、阶段、基线和回退锚点。
- `qianchuan_create_strategy_review`：保存本轮证据、动作、结果和下一检查时间。
- `qianchuan_list_material_decisions` / `qianchuan_create_material_decision`：维护素材赢家、候选和疲劳结论。
- `qianchuan_list_campaign_playbooks` / `qianchuan_build_delivery_plan`：在已有 playbook 时复用规则；不要擅自修改锁定经营边界。

MCP 没有调度器。持续无人值守需由外部 Agent 运行器或任务调度器周期调用上述循环。

## 账户策略

读取：`qianchuan_get_account_policy`、`qianchuan_list_account_policies`。

管理工具默认关闭时，不尝试在 MCP 内开启权限；让用户通过本地管理 API 配置。真实创建至少需要：

- policy `write_enabled=true`
- PC 千川全域包含 `ad_create`；标准两段创建才同时需要 `campaign_create` / `ad_create`
- `allowed_product_ids` 覆盖所选商品
- 必要时 `allowed_material_ids` 覆盖所选素材

标准对象的预算和状态写入使用 `qianchuan_update_budget`、`qianchuan_update_bid`、`qianchuan_update_roi_goal`、`qianchuan_update_ad_status`。全域对象使用 `qianchuan_update_uni_promotion_ad_budget`、`qianchuan_update_uni_promotion_ad_roi2_goal`、`qianchuan_update_uni_promotion_ad_status`。不要混用标准与全域 ID 或接口。

全域升级为乘方计划另需 Account Policy 包含 `ad_update`；它不由 `roi_goal_update` 权限替代。
