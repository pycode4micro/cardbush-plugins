---
name: qianchuan-mcp-operator
description: Optional strategy guidance for Qianchuan advertising, reports and material workflows through the self-contained Qianchuan plugin. Tools enforce their own contracts; this skill is not required for safe execution.
---

# 千川操作建议（可选）

插件内置千川MCP实现，不依赖桌面项目、本地REST、目录平台或生图服务。以工具当前输入schema、返回状态和服务端校验为准。不要为了完成任务读取其他项目的.env或数据库。

首次缺配置时调用 qianchuan_setup_status，根据结果引导用户在插件专属config.env中配置；不要要求用户把密钥发到聊天。配置后重新连接。已有配置时用 qianchuan_connection_status 检查实时连接。OAuth浏览器确认由用户完成。

按用户明确的账户、商品、素材、预算和期限操作。Skill不授予写权限。blocked、attention、setup_required均不代表业务成功。图片库上传、图文库创建和计划绑定是不同结果，不混称。

限时启用已有全域计划直接调用 qianchuan_start_timed_delivery，由服务端校验、登记到期保障、去重启用并回读。无需Agent维护私有状态协议。qianchuan_delivery_deadline_status可读取真实后台状态。

商品ID由用户或可选目录能力提供；生成素材由用户或可选上游模型提供。缺少上游能力时报告缺失输入，不转而访问本机ERP数据库或假定某个固定端口运行。

需要策略参考时可读 references/strategy-playbooks.md；旧参考中的开发环境路径与Agent自行执行保障流程不适用于此独立插件。动态策略仍需实际数据支持，不承诺必然达到ROI或销量。
