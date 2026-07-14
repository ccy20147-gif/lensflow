# 自定义智能体 Studio

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-AGT-002 |
| 标题 | 自定义智能体 Studio |
| 状态 | in_delivery |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | Agent Studio |
| 直接依赖 | TF-AGT-001、TF-AGT-005、TF-AGT-006、TF-WF-001、TF-WF-003、TF-WF-006、TF-WF-008 |
| 责任域 | Agent 产品/平台 |
| 个人 DRI | main-agent |

## 2. 背景与问题

创作者需要把自己的方法沉淀成可复用能力，但纯提示词既缺少输入输出合同，也无法说明工具权限、检查点和失败位置。Studio 必须兼顾非技术配置与运行时可审计性。

## 3. 目标与非目标

- 让用户通过 SOP、skills、批准 tools 和 typed I/O 定义、试跑并提交自定义 Agent。
- 让 Agent 作为动态业务节点进入 Vue Flow，支持有界 RequestInput，并把 typed 输出固定为 ArtifactVersion。
- 非目标：V1 不提供任意代码、任意网络、Agent/Workflow/Recipe 嵌套或独立社区 listing。

## 4. 用户与权限

- Agent 作者可创建和编辑自己 owner_scope 下的草稿。
- V1 仅项目 owner 可试跑、提交修订和调用；TF-TEAM-001 交付后才允许项目成员按 capability 分权。
- 工具凭证只能由有权主体绑定，作者不可读取 secret。
- 调用跨 owner 资源时必须通过当前 EntitlementDecision。

## 5. 用户场景与主流程

1. 作者从空白或官方模板创建 configurable Agent。
2. 在向导中定义用途、输入输出 schema、SOP 步骤、skills、tools 和补问规则。
3. Studio 做静态校验并显示数据披露、成本与权限摘要。
4. 作者用固定样例在隔离试跑区执行，查看逐步 trace、RequestInput 和 typed ArtifactVersion output。
5. 通过校验后提交 AgentRevision，随后从节点目录拖入主画布并固定调用。

## 6. 功能需求

- FR-1：Studio 必须提供用途、typed I/O、SOP、skills、tools、模型策略和 RequestInput 的结构化编辑器。
- FR-2：SOP 步骤必须有稳定 step_id、目标、输入映射、输出映射、失败策略和最大尝试数。
- FR-3：Skill 与 Tool 只能从可访问、批准且兼容的修订目录选择；Skill 按 TF-AGT-006 装配，Tool 按 TF-AGT-005 执行。
- FR-4：静态校验必须拒绝嵌套 Agent、Workflow、Subworkflow、Media Recipe、任意代码和未批准网络访问。
- FR-5：试跑必须使用隔离运行、固定输入和预算上限，并展示 step trace、工具披露、用量和失败归属。
- FR-6：Agent 可通过 TF-WF-008 产生持久 RequestInput；这是 Agent 唯一可直接发起的人工交互，问题和答案必须符合 schema 并可恢复，Agent 不得创建 Human Gate 或 WorkbenchTask。
- FR-7：提交必须生成 TF-AGT-001 定义的不可变 revision；草稿不得直接出现在生产画布。
- FR-8：节点注册必须由 revision 的 typed I/O 动态派生，不修改画布主组件。
- FR-9：复制现有 Agent 必须建立 lineage，去除原 CredentialBinding，并重新校验权限。
- FR-10：试跑和生产 AgentInvoke 成功时只允许创建符合 output schema 的 ArtifactVersion；禁止 Agent 写 ResourceDraft/ResourceRevision 或执行 ResourceCommit。
- FR-11：需要富内容人工编辑、审批或资源冻结时，工作流作者必须在 Agent 节点外显式连接 WorkbenchTask/Human Gate/ResourceCommit；Studio 不把这些任务包装进 SOP。

## 7. 交互与展示

- 采用分区编辑：概览、I/O、SOP、能力、工具与权限、试跑、版本。
- schema 编辑同时提供表单和原始 JSON Schema 视图，错误定位到字段路径。
- SOP 以可排序步骤展示；每步可展开高级失败策略，默认保持低认知负担。
- 试跑区并列显示输入、步骤时间线、工具调用、RequestInput、Artifact 输出和成本，并明确提示“输出尚未提交为业务 ResourceRevision”。
- 保存草稿、试跑和提交修订必须是不同命令，并显示当前 draft_version。

## 8. 数据、类型与公共接口

- 编辑对象为 Agent ResourceDraft，提交后形成 AgentRevision；结构沿用 TF-AGT-001。
- `SopStep` 扩展字段为 `step_id`、`instruction`、`input_bindings`、`output_schema_ref`、`retry_policy`、`checkpoint_policy`。
- 工具仅存 `tool_revision_ref` 和所需 scope；凭证使用不透明 CredentialBinding ID，由 TF-AGT-005 在运行时解析。
- 画布调用严格使用 `AgentInvoke(agent_revision_id, typed_inputs, config)`。
- AgentInvoke 输出映射为 `ArtifactRef[]`；Studio 不暴露 ResourceDraft/Revision 写入、Human Gate、WorkbenchTask 或 ResourceCommit step 类型。

## 9. 状态机与业务规则

- Studio 草稿生命周期与 RevisionStatus 分离；试跑产生 RunStatus/NodeRunStatus，不改变 revision。
- RequestInput 只允许将运行置为 `waiting_user`，答复后从已持久 checkpoint 恢复。
- workflow-owned Human Gate/WorkbenchTask 的状态不进入 Agent SOP 或 Agent checkpoint；它们由外层 Workflow/NodeRun 单独恢复。
- 每个步骤和整个 Agent 都必须有执行次数、token、时间和成本硬上限。
- 自动保存以 draft_version 幂等；提交时 base 变化必须显式合并。

## 10. 失败、降级与恢复

- 试跑工具不可用时标出具体 step，不允许以虚构输出通过提交校验。
- 浏览器刷新或后端重启后，可从 RunEvent 恢复试跑与 waiting_user 状态。
- 输出不符合 schema 时按步骤策略有限重试，耗尽后失败并保留原始安全诊断。
- 被撤权 tool/skill 使新试跑和新编译阻断；编辑内容仍可读并提供替换入口。

## 11. 安全、隐私、内容与授权

- Studio 不显示、导出或复制 secret；日志与提示预览需清理敏感字段。
- 工具数据披露按字段显示并要求作者确认，实际执行再按调用者权限复核。
- 用户输入、样例和输出遵循项目隐私边界；试跑数据不得自动成为公共样例。
- 内容与素材 Gate 由 TF-SEC-001 执行，Studio 不能提供绕过开关。

## 12. 观测与运营

- 事件包括草稿保存、静态校验、试跑开始/等待/恢复/结束、revision 提交和画布注册。
- 指标包括首个可用 Agent 完成率、校验错误分布、试跑成功率、RequestInput 恢复率和 schema 失败率。
- 支持视图可按 correlation_id 查看步骤、工具、成本与安全错误，默认不暴露用户正文。

## 13. 验收标准

- AC-1：Given 新用户，When 通过 Studio 定义合法 SOP 与 I/O 并试跑，Then 可提交 revision 并无需改前端代码出现在节点目录。
- AC-2：Given 一个需要补问的 Agent，When 用户刷新并稍后回答，Then 运行从同一 checkpoint 恢复且不重复已完成工具调用。
- AC-3：Given SOP 引用 Agent/Workflow/Recipe 或任意代码，When 校验，Then 提交被阻断并显示违规 step_id。
- AC-4：Given 作者无凭证查看权，When 复制或导出 Agent，Then 不含 secret/CredentialBinding 且可重新绑定后运行。
- AC-5：Given 输出不满足 schema，When 达到重试上限，Then NodeRun 失败、错误可定位且下游不收到部分输出。
- AC-6：Given 作者尝试在 SOP 中加入 Human Gate、WorkbenchTask、ResourceCommit 或直接 ResourceRevision 输出，When 静态校验，Then 提交被阻断并定位 step_id；改为外层 Workflow 连接后可编译。

## 14. 测试场景

- 正常：空白创建、模板创建、skill/tool 选择、隔离试跑、提交、画布调用。
- 边界：最大 SOP 步骤数、复杂嵌套 schema、多 Artifact 输出、零工具 Agent、单次 RequestInput 超时。
- 失败：schema 不兼容、工具失败、输出污染、预算耗尽、依赖撤权。
- 权限：非 owner 提交、跨 owner Skill、无权凭证、复制后重新授权。
- 并发/恢复：双标签编辑、刷新 waiting_user、worker 重启、RunEvent 重放和重复回答。

## 15. 交付与回退

- 通过功能开关按内部作者、受邀作者、全量项目逐步开放。
- revision schema 向前兼容；回退 UI 后仍保留草稿、运行和已发布 revision 的只读访问。
- 交付证据包括端到端录像、schema contract tests、安全测试、恢复测试和节点动态注册证明。

## 16. 已决策事项与开放问题

- 已决策：V1 自定义 Agent 由 SOP、skills、tools 和强类型 I/O 组成，禁止嵌套调用。
- 已决策：自定义 Agent 只产出 ArtifactVersion，只能通过 RequestInput 等待用户；Gate、Workbench 和资源提交必须由 Workflow 外置。
- 已决策：工具能力完全受 TF-AGT-005 约束；Studio 不创建后门式自定义连接器。
- 开放问题：无阻塞 V1 Core 的开放问题。
