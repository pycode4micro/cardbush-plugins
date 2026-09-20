# 查询入口与业务口径

## 用户问“昨天消耗多少”

调用 `qianchuan_start_spend_report(start_date="YYYY-MM-DD", end_date="YYYY-MM-DD")`。
省略 advertiser_ids 表示当前选中授权的 active QIANCHUAN 账户与本地白名单的交集，
不是所有历史授权的并集。显式账户传字符串数组，可避免长ID丢失精度。
拿 job_id 每10秒调用 `qianchuan_get_spend_report_result`，不要另起并发任务或扫描19个主题。

- 日期按北京时间。时间范围含首尾日期，最多31天。
- `marketing_goal=ALL` 包含直播和商品；也可选择 LIVE_PROM_GOODS / VIDEO_PROM_GOODS。
- 使用 `/v1.0/qianchuan/report/all_promotion/get/`，指标 `stat_cost_for_roi2`，单位元。
- UNI_PROJECT 是全域计划，OVERALL_PROJECT 是乘方计划，分别查询并保留分项。
- **范围仅PC千川全域+乘方，不含随心推、历史标准投放，也不是财务结算账单。**
- 商品全域不等于纯商品卡流量；不能给这份汇总贴“纯商品卡消耗”的标签。
- `complete=true` 才可展示 `total_cost_yuan` 为该范围总额。
- `running/partial` 的 `known_cost_yuan` 只是已查到的分项小计，未知不补0。
- `rows[].parts[]` 保留场景、错误码和 request_id；真实0和缺字段严格区分。
- 后台任务内存保留30分钟（清理在新建时触发），重启会丢失；not_found后可重新查询。
- 单进程队列顺序执行；GET按token+路径限速，最多3次有界重试，POST/上传不自动重试。
- 多宿主进程仍共享官方额度，所以不要在多个Agent同时重跑。此实现不是跨进程限流器。

## 何时用其他接口

| 要回答的问题 | 入口 | 注意 |
|---|---|---|
| 该用哪个报表 | qianchuan_report_routing_guide | 离线说明，无需猜测 |
| 当前授权账户 | qianchuan_connection_status / qianchuan_get_accounts | 网关解析的业务账户，不是OAuth根账号/店铺号 |
| 按商品、项目、素材分析全域效果 | qianchuan_get_uni_promotion_report_config → qianchuan_get_uni_promotion_report | 选一个对应粒度主题，按返回配置选指标 |
| 直播全域细分 | qianchuan_get_live_uni_report_config → qianchuan_get_live_uni_report | OVERALL_ROI_LIVE_*，不是标准直播报表 |
| 商品全域细分 | OVERALL_ROI_PRODUCT_* / SITE_PROMOTION_PRODUCT_* | 指标以主题配置为准；不能跨主题相加 |
| 历史标准账户/计划 | qianchuan_get_account_report / qianchuan_get_ad_report | stat_cost；空结果不证明全域零消耗 |
| 计划运行状态 | qianchuan_get_uni_promotion_ad_detail | 状态不等于已产生消耗 |
| 审核建议 | qianchuan_get_uni_promotion_ad_suggestions | 不等于完整投放诊断，不自动执行 |
| 旧全域汇总兼容入口 | /report/uni_promotion/get/ | 本轮遇50000；新总消耗查询不用该入口，不猜参数反复试 |

## 必须避免

1. 将 stat_cost 与 stat_cost_for_roi2 任意替换。
2. 将含额外成本的 stat_cost_for_overall_roi2 当成纯广告消耗。
3. 将不同主题/维度重复描述的同一笔消耗相加。
4. 把接口成功但空列表、请求失败、无权限、缺指标当0。
5. 平均各行ROI；应按同口径成交额合计/消耗合计重算，且标明归因成熟度。

## 官方依据与验证

接口及场景枚举：
https://github.com/oceanengine/ad_open_sdk_go/blob/master/api/api_qianchuan_report_all_promotion_get_v10.go

指标与单位：
https://github.com/oceanengine/ad_open_sdk_go/blob/master/models/model_qianchuan_report_all_promotion_get_v1_0_response_data.go

截至2026-09-19，使用上述只读官方汇总接口查询9月18日51个授权账户，
顺序降频后全部返回code=0。原有标准报表均为空，旧uni_promotion/get返回50000，
因此不能将“标准报表成功”作为“当前全域数据完整”的证据。不包含任何写入验收。
