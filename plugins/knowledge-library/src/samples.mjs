export const sampleLibraries = [
  { library_id: 'demo-people', name: '员工服务', department: '人事与行政', scenario: '员工制度咨询', description: '模拟企业的差旅、休假与入职制度。所有金额与规则仅用于 demo，不代表任何真实公司。', aliases: [['报销','报账'],['出差','差旅'],['攒到明年','结转'],['入职','新人报到']] },
  { library_id: 'demo-support', name: '产品与售后', department: '客户服务', scenario: '客户问题处理', description: '模拟产品 Aurora Hub 的退换货、质保和故障处理。', aliases: [['退钱','退款','退费'],['保修','质保'],['登不进去','登录失败']] },
  { library_id: 'demo-engineering', name: '研发运维', department: '研发部', scenario: '系统操作与故障响应', description: '模拟线上系统的部署、恢复与凭据管理手册。', aliases: [['挂了','服务中断'],['回退','回滚'],['恢复数据','备份恢复']] },
  { library_id: 'demo-personal', name: '个人资料', department: '个人', scenario: '学习与创作', description: '模拟个人笔记：摄影和服装设计，用于演示与企业资料共用一套管理方式。', aliases: [['衣领','领口']] },
];
export const sampleDocuments = [
  { library_id: 'demo-people', source_key: 'demo:travel', title: '差旅报销制度（2026 修订）', text: '# 差旅报销制度\n本文件仅为虚构 demo 资料。\n## 住宿标准\n一线城市住宿报销上限为每人每晚 600 元，其他城市为每人每晚 400 元。超过标准需在出差前获得部门负责人的书面批准。\n## 提交时限\n差旅结束后 10 个工作日内提交报销单、合规发票及行程证明。\n## 餐饮补贴\n差旅餐补为每人每天 100 元，不可与客户招待餐费重复报销。' },
  { library_id: 'demo-people', source_key: 'demo:leave', title: '年假与结转说明', text: '# 年假与结转说明\n本文件仅为虚构 demo 资料。\n年假余额最多可结转 3 天，结转部分须在次年 3 月 31 日前使用。未使用的结转天数届时失效。病假与婚假不计入年假。\n请假超过 3 个工作日，应提前 5 个工作日发起申请。' },
  { library_id: 'demo-people', source_key: 'demo:onboarding', title: '新同事入职清单', text: '# 新同事入职清单\n本文件仅为虚构 demo 资料。\n首日上午到行政前台领取工牌与电脑。人事负责合同及资料登记，IT 支持负责企业邮箱、VPN 和多因素认证开通。\n设备领用单需要本人签字，入职培训在第一周周四下午举行。' },
  { library_id: 'demo-support', source_key: 'demo:refund', title: 'Aurora Hub 退款处理规则', text: '# Aurora Hub 退款处理规则\n本文件仅为虚构 demo 资料。\n个人客户自签收日起 7 天内，商品未激活且包装完整，可申请退款。已激活产品需要先走故障检测流程。企业定制订单按合同约定处理，不适用普通七天退货。\n退货入库核验后 3 个工作日内原路退款。客服不得要求客户提供支付密码。' },
  { library_id: 'demo-support', source_key: 'demo:warranty', title: 'Aurora Hub 产品质保', text: '# Aurora Hub 产品质保\n本文件仅为虚构 demo 资料。\nAurora Hub 标准版主机的质保期为自购买日起 24 个月，配件为 6 个月。人为损坏、私自拆机和液体侵入不在免费质保范围。\n核验购买凭证与设备序列号后创建维修单，不要向客户承诺未经审核的换新。' },
  { library_id: 'demo-support', source_key: 'demo:login', title: '客户登录失败排查', text: '# 客户登录失败排查\n本文件仅为虚构 demo 资料。\n连续 5 次密码错误会锁定账号 15 分钟。先确认客户端版本和系统时间，再引导用户自行重置密码。\n禁止收集客户密码、短信验证码或恢复码；仍无法登录时记录错误码并升级到技术支持。' },
  { library_id: 'demo-engineering', source_key: 'demo:rollback', title: '生产部署与回滚手册', text: '# 生产部署与回滚手册\n本文件仅为虚构 demo 资料。\n发布采用先灰度后全量。灰度持续 15 分钟；错误率超过 1% 或 P95 延迟超过 800 ms 时停止发布，回滚到上一稳定版本。\n数据库存在不可逆迁移时禁止直接回滚二进制，先由值班负责人确认兼容性方案。\n回滚完成后观察 20 分钟并记录变更编号。' },
  { library_id: 'demo-engineering', source_key: 'demo:backup', title: '数据备份与恢复规范', text: '# 数据备份与恢复规范\n本文件仅为虚构 demo 资料。\n生产数据库每日 02:00 全量备份，备份保留 30 天。恢复演练每月执行一次。\n恢复目标：RPO 不超过 15 分钟，RTO 不超过 60 分钟。先在隔离环境校验完整性，再由值班负责人批准切换。' },
  { library_id: 'demo-engineering', source_key: 'demo:incident', title: '服务中断响应流程', text: '# 服务中断响应流程\n本文件仅为虚构 demo 资料。\nP1 服务中断需在 5 分钟内响应，由值班工程师建立事件记录并通知负责人，每 15 分钟更新一次进展。\n不得未经确认直接清空数据库或轮换所有密钥。服务恢复后两个工作日内完成复盘。' },
  { library_id: 'demo-personal', source_key: 'demo:photo', title: '室内人像摄影笔记', text: '# 室内人像摄影笔记\n模拟个人笔记。\n使用朝北窗户的漫射光，主体距离背景约 1.5 米，避免面部出现强烈斑驳光。固定白平衡拍摄，优先保留皮肤高光。' },
  { library_id: 'demo-personal', source_key: 'demo:garment', title: '毛衣领口设计观察', text: '# 毛衣领口设计观察\n模拟个人笔记。\n圆领更日常，V 领能延长视觉颈线。先在矢量正背面图中确认领口深度与肩线，再生成成衣效果。针织领口应考虑弹性与穿脱，不凭效果图推断实际工艺。' },
];
export async function seedDemo(api) {
  if (api.store.catalog().libraries.length) return;
  for (const library of sampleLibraries) await api.call('knowledge_library', library);
  for (const document of sampleDocuments) {
    const { library_id, ...data } = document;
    await api.call('knowledge_import', { library_id, documents: [data] });
  }
}
