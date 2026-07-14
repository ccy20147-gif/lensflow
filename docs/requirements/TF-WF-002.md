# 节点注册表与强类型端口

## 1. 元数据

- ID：TF-WF-002
- 标题：节点注册表与强类型端口
- 状态：in_delivery
- 目标版本：Foundation
- 优先级：P0
- 全局位置：平台内核
- 直接依赖：TF-ARC-001、TF-GOV-001
- 责任域：工作流平台
- 个人 DRI：main-agent

## 2. 背景与问题

开放画布若靠显示名称、字符串前缀或前端硬编码判断类型，会允许错误资源连接、旧节点静默变义和模板不可重放。所有节点能力必须由版本化注册表与强类型端口描述。

注册表既服务前端发现，也服务后端编译和执行快照。

## 3. 目标与非目标

目标：

- 定义不可变 NodeDefinitionRevision 与端口 schema。
- 以 type_id、schema_id、schema_version 和 cardinality 校验连接。
- 提供受控、显式、版本化的转换器。
- 让旧 WorkflowRevision 能解析原节点合同或得到明确迁移诊断。

非目标：

- 不允许用户在 V1 上传任意代码节点。
- 不按节点显示名称决定执行器或资产身份。
- 不由前端注册表副本成为最终裁决者。

## 4. 用户与权限

- 平台节点维护者可以提交新定义 revision。
- 安全与平台审核者批准执行器、权限和 Provider 能力声明。
- 普通创作者只能实例化 active 且有权使用的节点。
- 旧 Revision 的定义快照只读，不因节点退役被删除。

## 5. 用户场景与主流程

1. 节点作者提交定义、配置 schema、typed ports、执行器和 UI metadata。
2. 注册服务验证 schema、自身兼容性、权限和测试证据。
3. 审核后将定义 revision 激活并生成 registry snapshot。
4. 前端节点库读取 snapshot；编译器使用相同 revision 校验图。
5. 节点升级时新建 revision，旧 WorkflowRevision 继续固定旧合同。

## 6. 功能需求

- FR-1：NodeDefinitionRevision 必须有稳定 node_type_id、版本、输入输出端口和 config schema。
- FR-2：每个端口必须声明 type_id、schema_id、schema_version、cardinality 和 required policy。
- FR-3：类型兼容禁止仅按名称或字符串前缀推断。
- FR-4：转换必须通过注册 ConverterRevision 显式发生，并进入执行计划。
- FR-5：定义必须声明 executor identity、digest 或可解析的固定实现引用。
- FR-6：定义必须声明权限、成本估算、Provider 能力和 policy gate 元数据。
- FR-7：注册表必须生成不可变 snapshot，供 WorkflowRevision 和编译计划引用。
- FR-8：节点退役后旧 revision 与 schema 仍可查询，或返回明确不可重放诊断。
- FR-9：注册验证必须包含 mock 成功、schema 失败、取消和安全错误合同测试。
- FR-10：UI metadata 只能影响展示，不能改变执行语义。
- FR-11：节点定义不得包含明文 secret 或用户凭证。
- FR-12：V1 注册入口只接受平台构建或审批包，不接受任意脚本。

## 7. 交互与展示

- 节点目录展示名称、用途、输入输出、版本、状态和权限要求。
- 端口兼容说明显示预期 schema、实际 schema 和可用转换器。
- 退役节点在新建时隐藏，在旧图中显示迁移提示。
- 节点作者界面展示注册检查和 contract test 结果。
- 用户不需要理解内部 executor digest，但可查看节点版本和来源。

## 8. 数据、类型与公共接口

NodeDefinitionRevision 至少包含 node_type_id、revision_id、semantic_version、input_ports、output_ports、config_schema、executor_ref、policy_metadata 和 ui_metadata。

PortTypeRef 使用 type_id、schema_id、schema_version、cardinality。ArtifactRef 与 ResourceRef 遵循主表第 8.1 节。

RegistrySnapshot 固定所有定义 revision、转换器 revision 和 schema hash。

## 9. 状态机与业务规则

节点定义使用 RevisionStatus：draft、active、retired。只有 active 可用于新 Draft。

兼容升级可以新增可选端口或向后兼容字段；破坏性变化必须创建新主版本，不能覆盖旧 revision。

同一 node_type_id 与版本只能对应一个内容 hash。

## 10. 失败、降级与恢复

- schema 无效、端口 ID 重复或 executor 未固定时拒绝激活。
- RegistrySnapshot 缺失时编译失败，不回退 latest。
- 转换器不可用时连接明确报错，不进行隐式 JSON coercion。
- 节点包撤回时旧运行证据保留，新编译按安全策略拒绝或迁移。
- 注册服务恢复后从持久 revision 重建缓存。

## 11. 安全、隐私、内容与授权

- 节点定义与执行器包通过来源、签名和审批检查。
- 配置 schema 标识 secret 引用字段，禁止将 secret 值持久化进图。
- 节点声明最小 Resource/Artifact 读取范围。
- UI metadata 与富文本需净化。
- 任意代码、系统命令和未审批网络能力在 V1 被拒绝。

## 12. 观测与运营

- 记录节点注册、激活、退役、兼容失败和 snapshot 生成。
- 监控旧节点使用量、迁移阻断和转换器失败。
- 对相同版本 hash 漂移和未知 executor 立即告警。
- registry 查询按 snapshot_id、workflow_revision 和 compiler trace 关联。

## 13. 验收标准

- AC-1：同名但 schema_id 不同的两个端口不能连接。
- AC-2：注册显式转换器后，编译计划记录转换器 revision 并可执行转换测试。
- AC-3：新增节点定义后，前后端从同一 snapshot 读取且无需改画布主组件。
- AC-4：覆盖已激活版本内容的请求被拒绝。
- AC-5：退役节点的旧 WorkflowRevision仍能读取固定定义，无法执行时返回精确迁移诊断。
- AC-6：含明文 secret、任意脚本或未固定 executor 的定义不能激活。

## 14. 测试场景

- 正常：注册、激活、发现、实例化和退役节点。
- 边界：列表类型 cardinality、可选端口、兼容 schema 升级和旧版本。
- 失败：重复 ID、坏 JSON Schema、未知转换器和 executor hash 漂移。
- 权限：未审批作者、无权节点和 secret 配置读取被拒绝。
- 并发/恢复：并发激活相同版本只有一个成功；缓存丢失后从持久 snapshot 恢复。

## 15. 交付与回退

- Foundation 交付注册 API、schema、snapshot、转换器机制和 contract test harness。
- 首批节点以代码随版本交付，但仍通过同一注册合同。
- 新 snapshot 出错时回退上一 snapshot，不修改已固定 WorkflowRevision。
- 发布证据包含十种测试节点、兼容矩阵和退役重放演练。

## 16. 已决策事项与开放问题

已决策：类型按 schema identity/version 裁决；V1 不开放任意代码节点。

开放问题：未来插件签名与独立市场属于后续范围，不改变当前审批注册合同。
