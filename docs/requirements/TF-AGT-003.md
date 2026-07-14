# Workflow Architect Agent

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-AGT-003 |
| 标题 | Workflow Architect Agent |
| 状态 | in_delivery |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | 默认入口/主画布 |
| 直接依赖 | TF-AGT-001、TF-AGT-005、TF-WF-001、TF-WF-002、TF-WF-003、TF-WF-004、TF-WF-006、TF-OPS-002、TF-SEC-001 |
| 责任域 | Agent 产品/工作流平台 |
| 个人 DRI | main-agent |

## 2. 背景与问题

开放画布对新用户门槛高，用户希望用自然语言生成或修改流程。但让模型直接改图会绕过 schema、授权、预算与人工确认，并在草稿变化时误应用过期建议。

## 3. 目标与非目标

- 由官方 Architect Agent 将意图转成可解释、可校验、可估算的图变更提案。
- 在任何写入前展示 diff，并由工作流平台基于最新 draft hash 复核。
- 非目标：Agent 不直接写 WorkflowDraft、不绕过编译器、不自动确认、不在内部调用 Workflow。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可请求解释、生成提案、确认并应用提案；项目成员、共享编辑、审阅和只读角色统一后置 TF-TEAM-001。
- Agent 只接收 owner 可访问的节点注册表、同 owner ArtifactRef、已授权固定 ResourceRef、资源摘要和脱敏草稿快照。
- 应用时重新判断节点、资源、provider、预算和素材权限，不能继承提案生成时的结论；跨 owner 裸 ArtifactRef 一律拒绝。

## 5. 用户场景与主流程

1. 用户描述“从 idea 生成世界观、框架并扩写，框架前让我确认”。
2. 宿主以固定 `AgentInvoke` 把 draft hash、可用节点/schema 和约束作为 typed input 交给 TF-WF-006 运行时；真实模型请求遵循 ProviderInvocationAttempt 与双 Outbox 合同。
3. Agent 只返回承载 `WorkflowChangeProposal` 的 typed ArtifactVersion，含节点/边操作、理由、假设和替换槽；不得写 Draft、创建 Gate/WorkbenchTask 或冻结 Revision。
4. 主机调用图编译、权限与成本服务产生校验报告和估算，在画布展示 diff。
5. 用户确认后，平台对最新 hash 再校验并原子应用；hash 已变化则要求重算。

## 6. 功能需求

- FR-1：Agent 输入必须包含 workflow_revision/draft hash、typed intent、可用节点版本、schema 和用户约束快照。
- FR-2：输出必须是 schema 化 `WorkflowChangeProposal`，禁止以自然语言补丁直接执行。
- FR-3：提案支持 add/remove/update node、connect/disconnect、layout hint，以及“由 Workflow 创建注册 Human Gate 节点”的建议，并为每项提供理由；提案本身不得创建 Gate 或其他 workflow task。
- FR-4：Agent 只负责提案；WF-003、OPS-002 和 SEC-001 分别完成结构、预算和权限/安全复核。
- FR-5：UI 必须展示逐项 diff、输入输出类型、成本区间、权限缺口、降级与不可逆影响。
- FR-6：应用前必须比较 proposal.base_draft_hash；不一致时禁止部分套用。
- FR-7：确认必须是用户的独立持久命令，携带 proposal_id 和 validated_plan_hash。
- FR-8：恶意提示不得使 Agent 返回隐藏节点、未注册类型、latest 引用、任意代码或越权资源。
- FR-9：失败提案可修改意图重新生成，但每次生成和确认均保留 lineage。
- FR-10：任何真实外部模型请求必须由 TF-WF-006 执行：网络副作用前在同一事务持久化 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent；dispatcher 为请求生成稳定 idempotency key 并在 provider 支持时传递；结果以 execution epoch/fencing token 条件验证，并在同一事务写入 typed ArtifactVersion、至多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent。
- FR-11：发送后无法确定 provider 是否接收时 AttemptStatus 必须进入 `unknown`，只能通过查询、回调、账单或人工对账收敛；禁止盲目重提或直接 fallback。确需新请求时必须从原始固定输入重新做能力快照、编译、授权和成本估算，并创建新的 Attempt 与 dispatch outbox。
- FR-12：AgentInvoke 只能产生 typed ArtifactVersion；WorkflowDraft 写入、Human Gate、WorkbenchTask 与 WorkflowRevision 创建均由宿主 Workflow/API 在 owner 确认后执行，不属于 Agent 或 ToolInvocation 的副作用。

## 7. 交互与展示

- 默认入口提供目标输入和常用约束；高级用户可从画布选择局部范围请求建议。
- 提案以画布 ghost 节点和侧栏列表展示，新增、删除、修改使用不同非仅颜色标记。
- 成本、权限、缺失配置和替换槽在确认按钮前集中显示。
- 用户可逐项查看但 V1 仅支持整份原子应用，避免部分 diff 破坏已校验计划。

## 8. 数据、类型与公共接口

- `WorkflowArchitectInput`：`base_draft_hash`、`intent`、`registry_snapshot_ref`、`visible_resource_refs[]`、`constraints`。
- `WorkflowChangeProposal`：`proposal_id`、`base_draft_hash`、`operations[]`、`assumptions[]`、`rationale[]`、`replacement_slots[]`。
- `ProposalValidation`：`compiled_plan_hash`、`schema_errors[]`、`entitlement_errors[]`、`cost_estimate_ref`、`non_blocking_diagnostics[]`。
- Agent 通过固定 `AgentInvoke` 运行并输出 ArtifactRef；该 ArtifactRef 只在同一 owner_scope 内消费。跨 owner 可复用内容必须先提升为 ResourceRevision，再以带授权证据的 ResourceRef 使用。
- 外部调用记录直接遵循 TF-WF-006 与主表第 8.4 节的 ProviderInvocationAttempt、公共 AttemptStatus、ProviderInvocationRecord、ProviderOutputBinding 和双 Outbox 合同，不建立 Architect 专用调用状态。
- WorkflowDraft 写入仍由 TF-WF-004 API 在 owner 确认后执行，不属于 Agent 或 ToolInvocation。

## 9. 状态机与业务规则

- 提案状态为 generated -> validating -> valid/invalid -> confirmed/applied 或 expired/rejected；不复用 RunStatus。
- Agent 的 NodeRunAttempt/ProviderInvocationAttempt 使用 TF-WF-006 公共 AttemptStatus；provider 内部 queued/processing 仅作为 task binding 事件，不扩展提案状态。
- draft hash、注册表修订或当前权限变化都会使旧 validation 失效。
- `apply(proposal_id, plan_hash, draft_hash)` 必须幂等且原子；重复请求返回同一结果。
- Agent 的“建议可用”不构成用户确认，也不构成编译成功。

## 10. 失败、降级与恢复

- Agent 输出解析失败时保留安全摘要，不生成可确认对象；仅在前一 Attempt 已确定终止且策略允许时创建新 Attempt，`unknown` 未对账前禁止重提或 fallback。
- 校验服务不可用时提案保持 generated，不开放确认按钮。
- 应用过程中并发冲突返回新 diff，不写入部分节点或边。
- 页面刷新后从数据库恢复提案、校验和确认状态；SSE 丢失不影响事实。

## 11. 安全、隐私、内容与授权

- 输入只包含用户可见资源的最小元数据，不把 secret、完整私有内容或 CredentialBinding 提供给 Agent。
- ArtifactRef 输入必须与项目同 owner_scope；跨 owner 内容仅接受已提升 ResourceRevision 的固定 ResourceRef，并同时校验 `grant_snapshot_id` 与当前 EntitlementDecision。
- 提示注入内容不得修改系统调用矩阵、工具 allowlist 或确认要求。
- 资源授权和素材 Gate 在生成、校验、确认三个时点均按需执行，确认时以当前 EntitlementDecision 为准。

## 12. 观测与运营

- 记录意图 hash、输入注册表版本、AgentRevision、proposal、validation、确认 actor、应用结果和 correlation_id。
- 指标包括提案有效率、确认率、过期率、编译错误类别、估算偏差和确认后撤销率。
- 支持审计必须能回答“谁在何种草稿与权限下确认了哪些图变化”。

## 13. 验收标准

- AC-1：Given 一个合法创作意图，When 生成并确认提案，Then 画布原子产生可编译图，所有节点与边来自注册表。
- AC-2：Given 提案生成后草稿已变化，When 用户确认，Then 返回 stale hash 且工作流不发生任何写入。
- AC-3：Given 恶意意图要求越权资源、任意代码或自动确认，When 生成/校验，Then 违规项被拒绝且无隐藏写操作。
- AC-4：Given 预算或授权在确认前失效，When 应用，Then 当前复核阻断并显示可操作原因。
- AC-5：Given 重复提交同一 apply 请求，When 服务重试，Then 只产生一次草稿版本且返回相同结果。
- AC-6：Given 外部模型请求在发送后响应丢失，When dispatcher 无法证明 provider 是否接收，Then AttemptStatus 为 `unknown`，系统不重提、不 fallback，并通过对账收敛后至多形成一条 ProviderInvocationRecord。
- AC-7：Given Agent 提案包含 Human Gate、WorkbenchTask 或 Revision 创建意图，When AgentInvoke 完成，Then 输出仍只是 typed proposal ArtifactVersion；只有 owner 确认后宿主 Workflow/API 才能创建对应任务或 Revision。
- AC-8：Given 输入包含跨 owner 裸 ArtifactRef，When 生成或校验提案，Then 请求被拒绝；替换为带有效授权证据的固定 ResourceRef 后才可继续。

## 14. 测试场景

- 正常：从空画布生成流程、局部增加 Human Gate、替换节点、确认应用。
- 边界：50 节点提案、空意图、多种兼容端口、仅布局变化、零成本节点。
- 失败：非 JSON 输出、未知节点、类型不兼容、预算服务中断、过期 hash。
- 权限：非 owner 项目访问、私有资源、撤权、跨 owner 裸 ArtifactRef、授权 ResourceRef 和素材 Gate 阻断。
- 并发/恢复：owner 双标签修改草稿、重复确认、provider `unknown` 对账、服务重启和双 Outbox 重放后状态一致。

## 15. 交付与回退

- 先对内启用仅“解释/建议”的无写入模式，再开放带确认的图修改。
- 功能关闭时保留历史 proposal 和审计，只移除新建与应用入口。
- 交付证据包括红队提示集、过期 hash 测试、权限预算测试、原子应用 E2E 和 diff 视觉验收。

## 16. 已决策事项与开放问题

- 已决策：Architect Agent 只能提案，图校验、成本估算、确认和写入由宿主平台完成。
- 已决策：V1 不支持提案的任意部分应用；不把确认隐含在聊天回复中。
- 已决策：Architect 的外部模型请求统一由 TF-WF-006 可靠执行；Agent 不创建 Gate、WorkbenchTask、WorkflowDraft 或 WorkflowRevision。
- 开放问题：无阻塞 V1 Core 的开放问题。
