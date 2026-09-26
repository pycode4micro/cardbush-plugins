# 资料库 · Knowledge Library demo

独立 CardBush / MCP 插件：先把已有资料按领域、部门、场景建库，再由 Agent 检索少量原文片段，读上下文并注明出处。也适合个人笔记和创作资料。

不修改 CardBush 源码，不自动扫描会话，不向系统提示词塞入整库，不调用外部模型或上传文档到外部服务。普通检索不打开 App；`knowledge_open` 才提供与宿主主题同步的管理面板。

## 立即试用

在 CardBush 插件页添加或刷新 [Cardbush Plugins 市场](https://github.com/pycode4micro/cardbush-plugins)，选择**资料库（knowledge-library）**安装并启用。插件运行主机需要 **Node.js 22.13+**；仓库内已包含运行依赖，不需要现场 `npm install`。Node.js 24 LTS 或更新受支持版本也可使用。

如需通过本地 ZIP 安装，在此插件目录运行 `python -X utf8 scripts/package.py`，生成 `release/knowledge-library-0.1.0-demo.zip`，再从 CardBush 插件页选择**从本地 ZIP 安装**。市场安装直接使用仓库里的插件目录，不依赖 ZIP。

安装后对 Agent 说：

> 建立一个“客服资料库”，用于售后咨询。先打开资料管理面板，让我上传已有手册，再用“退货后多久退款”检查检索效果。

或：

> 把我指定的资料目录导入“研发运维”资料库，检索部署失败后的回滚条件，并标注依据。

本机路径只对本机插件有效。云 Agent 上需要把包安装到云端、将资料放在服务器允许目录，或通过面板上传。SSH 是 CardBush 与 Agent 的连接方式，资料插件在 Agent 所在主机通过 stdio MCP 运行，无需另开公网端口。

直接看独立网页 demo：

```sh
node runtime/demo.mjs
```

终端显示 `http://127.0.0.1:<随机端口>`。该预览只绑定本机，使用单独的 `.demo-data/`，首次启动写入 4 个资料库、11 份**虚构样本**。页面支持检索、上传、文本录入、创建资料库、设置同义词、归档与恢复。网页 demo 禁止读取任意主机路径；目录导入由 MCP 接口负责。预览数据与正式插件数据分离。

可以试问：

- 员工服务：一线城市出差住宿能报销多少？年假可以攒到明年吗？
- 产品与售后：客户想退钱怎么办？Aurora Hub 主机保修几年？
- 研发运维：灰度发布错误率超过多少要回滚？RPO 和 RTO 是多少？
- 个人资料：室内人像适合怎样的光线？毛衣衣领如何先设计？

## 支持什么

| 能力 | demo 行为 |
| --- | --- |
| 资料组织 | 每库有名称、用途、部门、场景、领域同义词；Agent 可先列目录选择范围 |
| 导入 | PDF、DOCX、Markdown、TXT、HTML、CSV；服务器文件／目录、上传文件、直接文本 |
| 建立索引 | 提取文本、按页或文本范围分段、中文分词＋双字补充、SQLite FTS5 / BM25 |
| 查询 | 按库、部门、场景筛选；默认 6 个片段，最多 12 个；配置同义词扩展，不生成答案 |
| 出处 | 文档 ID、版本、来源、PDF 页码或提取文本行号、稳定的 MCP 原文资源 URI |
| 维护 | 同来源内容相同跳过、内容更新生成新版本；归档移出搜索、恢复重新进入 |
| 多进程 | 同一主机本地磁盘上的 SQLite WAL 事务；更新时校验版本，避免旧写入覆盖新内容 |
| 上下文 | 每次工具结果追加必要片段，不改写已有消息，不动态改工具 schema；可保留历史出处 |

扫描 PDF 没有 OCR，空白／扫描页会报错或给出未索引警告。Word 的位置是提取文本行号，不冒充原始页码。表格导入是文字检索，不解释公式；不支持旧 `.doc`、图片、PPT、网页抓取或网盘自动同步。

每份文件最多 16 MB、PDF 最多 500 页、提取文本最多 200 万字符、每批最多 100 份文件。解析在独立 Worker 中执行，限制内存和时间，失败不替换旧索引。目录导入跳过隐藏项、符号链接和开发依赖目录，不删除或改写原文件。

## 工具

- `knowledge_catalog`：列可见资料库及用途；指定库后分页列文档。
- `knowledge_library`：初始化／更新库设置，更新需提供 `expected_revision`。
- `knowledge_import`：导入资料，返回逐文档新增／更新／未变化／失败。`library_id + source_key` 是文档身份。
- `knowledge_search`：问题与范围 → 原文片段和出处。
- `knowledge_read`：按检索返回的文档 ID、revision、chunk 读取证据；`next_chunk` 分页。
- `knowledge_document`：按当前版本归档／恢复。
- `knowledge_open`：打开管理面板。面板不是查询的副作用，应由 Agent 在回复中明确引入。

检索内容与元数据标记为外部资料。HTML 不执行脚本，界面用文本节点展示资料；文档里的“忽略指令／提升权限／外发口令”不获得操作权。这些措施不等于已经解决所有模型提示注入问题，企业接入仍须使用真实权限边界。

## 数据与云端配置

默认数据目录为插件运行账户的 `~/.cardbush-knowledge/`，独立于安装包，升级代码不会删库。用宿主环境／连接配置设置：

| 环境变量 | 用途 |
| --- | --- |
| `KNOWLEDGE_DATA_DIR` | 独立绝对数据目录，例如 `/srv/cardbush-knowledge/support` |
| `KNOWLEDGE_IMPORT_ROOTS` | 允许导入的绝对目录数组的 JSON，例如 `["/srv/company-docs"]`；`[]` 禁止路径导入，只保留上传／文本；未设置允许显式指定的主机路径 |
| `KNOWLEDGE_ALLOWED_LIBRARIES` | 可信连接允许访问的库 ID，逗号分隔；未设置可访问实例所有库，空字符串表示无库可访问 |
| `KNOWLEDGE_READ_ONLY` | `1` 为查询连接，不暴露写入工具；需先由管理员实例初始化同一数据目录 |

推荐先由管理员连接导入，数字员工使用只读连接。每个实例一个数据目录；同机多进程可以共享本地库，**不要把 SQLite 文件放在跨主机网络盘上当分布式数据库**。

**本 demo 是单个可信租户的原型，没有企业 SSO／成员身份／行级 ACL。** 部门和场景只是筛选字段。环境变量限制是在 MCP 工具边界生效；如果 Agent 以完全访问模式直接读同账户的数据库文件，筛选无法阻止它。真正多租户／敏感部门隔离，应由独立系统账户、挂载权限或认证检索服务限制数据访问，不能依赖模型自觉传部门名。

同义词是人工可维护的领域词组，当前不含 embedding、向量库、重排模型或自动回答。先用真实资料和真实问题评估；如果大量换说法问题召回不足，再加入语义检索与重排，不需要改变上层工具接口。大批量持续同步、OCR、认证权限、审计与备份是进入企业生产前的后续工作。

## 开发与验证

```sh
npm ci
npm test
node scripts/benchmark.mjs
# CARDBUSH_SOURCE_ROOT 指向已构建的 CardBush 源码；以下验证宿主发现和实际 MCP App
node --test test/cardbush.integration.mjs
node scripts/test-ui.mjs
python -X utf8 scripts/package.py
```

`npm test` 使用临时资料，不访问用户文档、不调用 LLM。覆盖 18 个模拟业务查询、出处回读、增量更新、归档、权限范围、只读模式、异常文件、中文／英文、PDF／DOCX 真解析、查询语法注入、并发事务、真实 stdio MCP 及离开源码／node_modules 后的运行。

测量输出在 `release/evaluation.json` 和 `release/benchmark.json`。模拟样本命中率不能代表真实企业资料准确率，也没有测量生成回答正确率。UI 测试使用沙箱 iframe 和真实 MCP，检查上传、原文展开、归档恢复、浅色／深色／窄屏及外部文本转义。

技术依据：[SQLite FTS5](https://sqlite.org/fts5.html)、[Node SQLite](https://nodejs.org/api/sqlite.html)、[MCP server concepts](https://modelcontextprotocol.io/docs/learn/server-concepts)、[unpdf](https://github.com/unjs/unpdf)、[Mammoth](https://github.com/mwilliamson/mammoth.js)。运行依赖已打包，许可证见 `THIRD_PARTY_NOTICES.txt`。
