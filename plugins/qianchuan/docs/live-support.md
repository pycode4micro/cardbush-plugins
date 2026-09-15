# 直播支持状态

本轮仅开发和模拟测试，不连接真实账户，不启动后台、不修改正在运行的计划。

- qianchuan_get_live_delivery_capabilities：离线查看支持范围。
- qianchuan_analyze_live_snapshot：对提供的直播快照离线分析，消耗和成交额显式使用元；缺失不补零、拒绝NaN/负数、零消耗ROI为null、区分归因成熟度。不生成或执行写请求。
- qianchuan_get_live_ad_report：标准千川计划报表，固定LIVE_PROM_GOODS和QIANCHUAN过滤，明确计划ID和日期。它不是全域报表；不把标准报表结果当全域完整效果。

官方参数依据：https://open.oceanengine.com/tools/visual_debug.html?docId=1697466415173644

## SDK子集适配（2026-09-15）

新增 qianchuan_get_live_uni_create_contract、qianchuan_validate_live_uni_payload、qianchuan_create_live_uni_ad。字段根据巨量引擎官方SDK：
https://github.com/oceanengine/ad_open_sdk_go/blob/master/models/model_qianchuan_uni_aweme_ad_create_v1_0_request.go
以及同目录 request_delivery_setting、request_creative_setting 与枚举模型核对。

当前仅支持LIVE_PROM_GOODS、立即排期SCHEDULE_FROM_NOW、显式creative_setting与放量/控成本两种出价模式。控成本需要显式ROI；放量禁止roi2_goal（含null）。不要求商品数组、商品库存或video_schedule_type。不同计划仍须复用现有余额、账户策略、抖音号授权、预算上限、确认与冷却安全门。若现有账户策略强制商品范围或素材范围，仍可能阻断直播，不自动移除这些限制。

QIANCHUAN_LIVE_CREATE_ENABLED默认false，与全局写开关独立；未修改任何真实配置。通用创建和raw路径也不能绕过直播开关。完整直播预检会按LIVE_PROM_GOODS/CREATE查询抖音号资格，不依赖商品查询触发。

仍未覆盖指定时段直播、自选素材组合及SDK未描述的条件必填规则。SDK结构正确不等于官方业务验证成功。尚未进行真实创建或真实直播测试，不宣称生产可用。

## 直播全域只读查询（2026-09-15）

1. `qianchuan_get_live_uni_report_config`：查询官方直播抖音号、直播素材、视频素材三个主题的可用维度和指标。
2. `qianchuan_get_live_uni_report`：按配置返回的维度、指标和过滤条件查询一页全域数据，固定ALL_DATA，不回退到标准报表。时间格式YYYY-MM-DD HH:MM:SS，按账户报表时区填写；本地限制每次最多31个自然日。调用者继续按官方分页查询，不把空页当零消耗，不自行换算指标单位或归因口径。
3. `qianchuan_get_live_uni_diagnostics`：先读精确计划详情，确认ad_id和LIVE_PROM_GOODS，再读取一页官方审核建议；保留状态、原始结果、request_id和分页。缺失/错误/商品计划都停止后续诊断。actions始终为空，不启停、不改ROI/预算。审核建议并非完整流量资格诊断；complete=false明确未获取全部分页。

参数和主题来自官方SDK（不是凭商品报表推测）：
https://github.com/oceanengine/ad_open_sdk_go/blob/master/models/model_qianchuan_report_uni_promotion_data_get_v1_0_data_topic.go
https://github.com/oceanengine/ad_open_sdk_go/blob/master/examples/qianchuan_report_uni_promotion_data_get_v10_example.go
https://github.com/oceanengine/ad_open_sdk_go/blob/master/examples/qianchuan_uni_promotion_ad_suggestion_v10_example.go

这些工具不执行广告写操作，但OAuth获取令牌可能刷新授权存储，因此MCP不标注严格readOnlyHint=true。本次仅模拟验证，未查询真实账户、未重启服务、未修改运行配置。

测试：tests/test_live_offline.py 全程封禁httpx网络请求，使用虚构账户和模拟响应。没有真实业务验证，不宣称已生产验证。
