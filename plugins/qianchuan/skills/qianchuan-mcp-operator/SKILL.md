---
name: qianchuan-mcp-operator
description: Use the self-contained Qianchuan plugin for advertising reports, campaign operations and materials. Read task-relevant references to select the reporting scope and interpret results; tools enforce their own permissions and validation.
---

# 千川操作与报表

插件内置千川MCP实现，不依赖桌面项目、本地REST、目录平台或生图服务。Skill帮助选择业务口径和解释结果；权限、输入校验和执行保障由工具实现。以工具当前描述、输入schema、返回状态和服务端校验为准。不要为了完成任务读取其他项目的.env或数据库。

首次缺配置时调用 qianchuan_setup_status，根据结果引导用户在插件专属config.env中配置；不要要求用户把密钥发到聊天。配置后重新连接。已有配置时用 qianchuan_connection_status 检查实时连接。OAuth浏览器确认由用户完成。

按用户明确的账户、商品、素材、预算和期限操作。Skill不授予写权限。blocked、attention、setup_required均不代表业务成功。图片库上传、图文库创建和计划绑定是不同结果，不混称。

限时启用已有全域计划直接调用 qianchuan_start_timed_delivery，由服务端校验、登记到期保障、去重启用并回读。无需Agent维护私有状态协议。qianchuan_delivery_deadline_status可读取真实后台状态。

商品ID由用户或可选目录能力提供；生成素材由用户或可选上游模型提供。缺少上游能力时报告缺失输入，不转而访问本机ERP数据库或假定某个固定端口运行。

## 按任务读取参考文档

读取入口后列出本 Skill 的 `references/` 目录，按任务读取相关部分，无需全文加载全部文档。路径相对本 Skill 目录解析。

- 报表、工具选择或计划操作：读 [工具映射](references/tool-map.md)，报表任务先看“报表与诊断”，区分经典与全域口径。
- 空结果、权限失败或执行异常：读 [安全门与排障](references/safety-and-troubleshooting.md) 中对应结果，空列表不能解释为零消耗。
- ROI分析或策略制定：读 [策略参考](references/strategy-playbooks.md) 中适用场景。策略建议可选，结论仍需实际数据支持，不承诺必然达到ROI或销量。

参考中的旧开发环境路径、固定账户示例与Agent自行执行保障流程不适用于此独立插件。账户与权限范围取自用户要求和工具实际返回，不能用参考文档替代当前工具契约。
