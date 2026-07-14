# AgentDefinition 与不可变修订

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-AGT-001 |
| 标题 | AgentDefinition 与不可变修订 |
| 状态 | in_delivery |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | Agent Studio/平台内核 |
| 直接依赖 | TF-WF-002、TF-WF-004、TF-WF-005、TF-AGT-006 |
| 责任域 | Agent 平台 |
| 个人 DRI | main-agent |

## 2. 背景与问题

Agent 需要同时服务官方能力、用户配置和工作流复用。若定义、草稿、运行版本混用，历史运行会随提示词、工具或模型策略变化而失真，也无法可靠回滚。

## 3. 目标与非目标

- 建立稳定 Agent 身份、可变草稿和不可变修订。
- 区分平台托管 preset 与用户可配置 Agent，并让运行只消费固定修订。
- 冻结 Agent 运行输出与 Workflow 资源提交的边界：AgentInvoke 只产生 typed ArtifactVersion。
- 非目标：本项不提供 Studio 编辑体验、不定义工具安全细则、不允许 Agent 嵌套调用 Agent、Workflow 或 Media Recipe。

## 4. 用户与权限

- 平台管理员维护 managed preset；V1 仅项目 owner 在授权范围内查看和调用，成员角色由 TF-TEAM-001 后置提供。
- V1 Core 仅对应 owner_scope 的 owner 创建 configurable Agent、编辑草稿、激活或退役修订；项目成员能力仅在 TF-TEAM-001 后生效。
- owner 必须同时拥有 AgentRevision、同 owner_scope 输入 ArtifactRef、固定输入 ResourceRef 和所需工具的当前权限。
- ArtifactRef 只允许在同一 owner_scope 内输入；跨 owner 内容必须先提升为 ResourceRevision，并以带 `grant_snapshot_id` 的固定 ResourceRef 调用。历史 GrantSnapshot 不替代当前 EntitlementDecision。

## 5. 用户场景与主流程

1. 所有者创建 AgentDefinition 并编辑一个草稿。
2. 系统校验名称、typed I/O、SOP、skill 与 tool 引用。
3. 所有者提交草稿，系统以 compare-and-swap 生成不可变 AgentRevision。
4. 画布节点固定该 revision；编译器校验输入、权限和执行策略，AgentInvoke 的成功输出保存为 typed ArtifactVersion。
5. 若官方 managed 节点需要工作台、Human Gate 或资源提交，Workflow 编译器在 AgentInvoke 外显式生成这些任务；AgentRevision 本身不包含它们。
6. 后续修改生成新 revision，既有运行继续读取原 revision；所有者可将旧 revision 重新激活。

## 6. 功能需求

- FR-1：AgentDefinition 必须提供稳定 `resource_id`，草稿与修订遵循 Resource/ResourceDraft/ResourceRevision 合同。
- FR-2：AgentRevision 内容至少包含类别、typed input/output schema、SOP、SkillRevision refs、ToolRevision refs、模型选择策略、RequestInput 策略和执行边界；Skill 非可执行，Tool 才能产生副作用。
- FR-3：`managed_preset` 与 `configurable` 必须是显式类别；preset 的锁定字段不得被项目用户覆盖。
- FR-4：提交修订必须校验 schema identity/version、依赖修订、授权、循环引用和 V1 禁止调用项。
- FR-5：每次 `AgentInvoke` 必须固定 `agent_revision_id`，运行期间不得读取 latest。
- FR-6：修订支持 active/retired；退役不删除历史运行、产物、trace 或授权证据。
- FR-7：系统必须提供修订 diff、回滚式激活和使用该修订的工作流/运行反向索引。
- FR-8：owner 多标签或重试并发提交使用 `draft_version` compare-and-swap，冲突时不得覆盖已保存草稿。
- FR-9：每次成功 AgentInvoke 必须生成一个或多个符合 output schema 的不可变 ArtifactVersion/ArtifactRef；Agent 运行不得创建或修改业务 ResourceDraft/ResourceRevision。
- FR-10：AgentRevision/SOP 只能声明模型、批准 ToolInvocation、有界状态和 RequestInput；必须拒绝 Human Gate、WorkbenchTask、ResourceCommit、其他 Agent、Workflow、Subworkflow 或 Media Recipe 调用。
- FR-11：官方 managed Agent 的展示元数据可绑定 `ManagedAgentTaskPlan` 模板，但模板归 Workflow 节点注册/编译器所有；运行详情必须把聚合卡片展开为显式任务，不能把任务藏进 Agent trace。

## 7. 交互与展示

- Agent 详情页展示类型、所有者、当前活动修订、I/O、依赖、权限风险和最近运行。
- 修订页以字段级 diff 标出 SOP、schema、skill、tool 和模型策略变化。
- 画布节点显示固定 revision 摘要；存在新修订时只提示，不自动升级。
- managed 节点仍可显示为一个业务卡片，但高级运行视图必须区分 Agent Artifact 输出、workflow-owned 人工任务和 ResourceCommit。
- retired 修订保持可读并明确标记，禁止以“已发布”混淆 RevisionStatus 与 ListingStatus。

## 8. 数据、类型与公共接口

- `AgentDefinition` 是 `Resource(resource_type=agent)`；`AgentRevision` 是专用 schema 的 ResourceRevision。
- 修订内容扩展字段：`agent_kind`、`input_schema_ref`、`output_schema_ref`、`sop_steps[]`、`skill_revision_refs[]`、`tool_revision_refs[]`、`execution_policy`。
- 调用使用主表 `AgentInvoke`；输入只接受注册 schema 的 typed values、同 owner_scope ArtifactRef 或固定 ResourceRef，输出绑定只接受在调用 owner_scope 内新建的 ArtifactRef。
- 跨 owner 输入必须是带授权证据的固定 ResourceRef；ArtifactRef 不得携带 grant 或被包装为跨 owner 许可载体。
- skill/tool 只保存不可变引用或 managed preset 标识，不保存 CredentialBinding 或明文 secret。
- `ManagedAgentTaskPlan` 不写入 AgentRevision 内容；其中的 WorkbenchTask/Human Gate/ResourceCommit 由固定 WorkflowRevision 和编译计划拥有。

## 9. 状态机与业务规则

- 修订仅使用 `RevisionStatus = draft | active | retired`；社区 listing 状态不在本项复用。
- 草稿可多次保存；只有成功提交才产生新 revision_number。
- 同一 Definition 可有多个可调用 active 修订，但 UI 必须明确推荐修订；工作流仍固定具体 ID。
- 提交请求以 Definition、draft_version 和内容 hash 幂等；相同请求不得产生重复修订。
- 承载 AgentInvoke 的 NodeRun/Attempt 状态与任何后续 WorkbenchTask、Human Gate、ResourceCommit 状态分离；Agent 节点成功不代表业务 ResourceRevision 已产生。

## 10. 失败、降级与恢复

- schema 或依赖无效时阻断提交，返回字段路径和稳定错误码。
- 并发冲突返回 base/current/local diff，保留 owner 当前标签页内容供手动合并。
- 依赖被撤权时阻断新编译和新运行；历史合法运行保持可审计读取。
- 修订创建后索引失败时由 outbox 重试，修订真相不得回滚或变成半可见状态。

## 11. 安全、隐私、内容与授权

- 查看、编辑、激活、退役和调用分别鉴权并写审计日志。
- 提示词和 SOP 中的敏感信息不得当作凭证；检测到疑似 secret 时阻断提交。
- Agent 输入的最小披露、工具权限和输出净化由 TF-AGT-005 执行，本项不得放宽。

## 12. 观测与运营

- 记录 `agent_draft_saved`、`agent_revision_created`、`activated`、`retired`、`invoke_bound` 事件。
- 指标至少包含提交成功率、校验失败分类、CAS 冲突率、各 revision 调用量和撤权阻断量。
- 审计记录关联 actor、owner_scope、definition/revision、内容 hash、依赖集合和 correlation_id。

## 13. 验收标准

- AC-1：Given 一个合法草稿，When 提交，Then 生成内容不可变且可由 ID 重读的 AgentRevision，并能完成一次 typed 调用。
- AC-2：Given 已运行的 revision A，When 激活 revision B，Then A 的重放仍绑定 A，输出 lineage 不出现 B。
- AC-3：Given 两个相同 base 的并发提交，When 后提交到达，Then 返回冲突且首个 revision 内容未被覆盖。
- AC-4：Given Agent 含 Agent/Workflow/Recipe 调用引用，When 提交，Then 校验失败并定位违规字段。
- AC-5：Given 调用者失去依赖授权，When 新编译，Then 被阻断；既有合法运行及 GrantSnapshot 仍可审计读取。
- AC-6：Given 合法 AgentInvoke，When 成功，Then 输出是符合固定 schema 的新 ArtifactVersion，且运行主体没有 ResourceDraft/Revision 写权限。
- AC-7：Given AgentRevision 声明 Human Gate、WorkbenchTask 或 ResourceCommit，When 提交，Then 校验失败并定位违规 SOP 字段；同一能力作为 managed 节点时只能由 Workflow 编译器外置这些任务。
- AC-8：Given AgentInvoke 输入跨 owner 裸 ArtifactRef，When 编译，Then 请求被拒绝；只有提升后的固定 ResourceRef 携带有效授权证据且当前 entitlement 允许时才可调用。

## 14. 测试场景

- 正常：创建 configurable Agent、提交两版、固定调用、diff、退役和重新激活。
- 边界：空 SOP、schema 最大允许尺寸、多 Artifact 输出、retired revision 的历史读取、同内容幂等提交。
- 失败：损坏 schema、缺失依赖、循环依赖、Agent 越界写 Revision、索引/outbox 暂时失败。
- 权限：非 owner 项目访问、跨 owner 裸 ArtifactRef、授权 ResourceRef 调用、撤权后重跑和管理员 preset 锁定字段。
- 并发/恢复：owner 双标签 CAS 冲突、服务重启后修订与索引恢复、重复提交不增版。

## 15. 交付与回退

- 以功能开关控制 Agent 创建和调用，先启用内部 managed preset，再开放 configurable。
- schema 与修订表只做向前兼容迁移；回退代码不得修改或删除已生成 revision。
- 发布证据包括 contract tests、权限矩阵、并发测试、固定版本 E2E 和审计样例。

## 16. 已决策事项与开放问题

- 已决策：运行固定 AgentRevision；V1 禁止 Agent 内嵌 Agent、Workflow、Subworkflow 或 Media Recipe。
- 已决策：AgentInvoke 只产出 ArtifactVersion；Human Gate、WorkbenchTask 和 ResourceCommit 都是 Workflow 任务。
- 已决策：managed preset 与 configurable 分离；社区独立 listing 由 TF-COM-007 在 V1.5 提供。
- 已决策：ArtifactRef 只在同一 owner_scope 内作为 Agent 输入；跨 owner 内容必须先提升并使用带授权证据的固定 ResourceRef。
- 开放问题：无阻塞 V1 Core 的开放问题。
