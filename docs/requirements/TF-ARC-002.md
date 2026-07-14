# 分层创作架构与工作区

## 1. 元数据

- ID：TF-ARC-002
- 标题：分层创作架构与工作区
- 状态：in_delivery
- 目标版本：Foundation
- 优先级：P0
- 全局位置：产品外壳/创作空间
- 直接依赖：TF-GOV-001、TF-ARC-001
- 责任域：产品架构/前端架构
- 个人 DRI：main-agent

## 2. 背景与问题

开放创作既需要高层业务编排，也需要小说、分镜、图片和时间线等高密度编辑。若把所有内容铺成画布节点，画布不可用；若把流程全部藏在 Agent 或工作台，用户又无法理解和复刻。

产品必须固定主 Workflow、Agent、Media Recipe 和 Workbench 的职责与调用方向。

## 3. 目标与非目标

目标：

- 让导航、编辑体验和运行时类型保持解耦。
- 主画布只承载可独立调度的业务能力节点。
- 重复领域内容在工作台编辑，底层媒体链在 Recipe 中表达。
- 每次跨层调用具有固定 revision、typed I/O、trace、成本和失败归属。

非目标：

- 不把工作区名称编码成 Artifact 类型系统。
- 不允许工作台成为第二运行时。
- 不用大型固定业务节点复制 Toonflow FlowData。

## 4. 用户与权限

- V1 Core 当前项目 owner 可从目标入口和模板进入工作区，无需打开画布。
- 同一 owner 可展开主画布重排业务能力、进入 Media Recipe Lab 编辑底层媒体图，并在 Agent Studio 管理 SOP 与工具；项目成员角色仅在 TF-TEAM-001 后生效。
- 所有层都受项目 owner_scope、ResourceRevision 和工具权限约束。

## 5. 用户场景与主流程

1. 用户从模板创建项目并进入对应领域工作区。
2. 工作区展示当前固定 Revision 和可编辑 ResourceDraft。
3. 用户需要改变业务顺序时展开主 Workflow。
4. AgentInvoke 只输出 ArtifactVersion；节点需要人工精修时，由主 Workflow 创建 WorkbenchTask 并固定输入快照和期望输出。
5. 工作台保存产生新 ArtifactVersion/ResourceDraft；owner 提交后，workflow-owned ResourceCommit 以 compare-and-swap 冻结 Revision，运行时才把 typed ResourceRef 交给下游。
6. 媒体节点调用固定 MediaRecipeRevision，并显示内部 trace 摘要。

## 6. 功能需求

- FR-1：产品外壳必须提供项目、资源库、创作工作区、主画布、Agent Studio、Recipe Lab 和设置入口。
- FR-2：主 Workflow 只允许注册业务节点、固定 Agent/Recipe、有限 Subworkflow 和 WorkbenchTask。
- FR-3：Agent 只能调用批准模型与工具并产出 ArtifactVersion，不得调用 Agent、Workflow、Subworkflow、Recipe、Human Gate、WorkbenchTask 或 ResourceCommit，也不得写业务 ResourceDraft/Revision。
- FR-4：Media Recipe 只能调用媒体算子和 Provider，不得包含 Agent、Human Gate 或任意代码。
- FR-5：Workbench 只能读取固定 Revision/ArtifactVersion、编辑 Draft、向 workflow-owned WorkbenchTask 提交内容并显式请求运行；它不直接推进 Run/NodeRun。
- FR-6：WorkbenchTask 必须定义输入快照、目标工作台、typed output、base_revision/draft_version、ResourceCommit 策略和完成状态，并由主 Workflow 拥有。
- FR-7：跨层调用必须记录 revision、输入、trace、成本和安全错误。
- FR-8：节点卡只展示摘要、状态和打开工作台命令，长内容不撑大节点。
- FR-9：候选 Toonflow UI 必须通过 adapter 只消费新合同。
- FR-10：架构检查必须阻止逆向调用和第二套运行真相。
- FR-11：官方 managed Agent 在画布上可保持单卡片，但编译器必须产生可展开 `ManagedAgentTaskPlan`，显式列出 AgentInvoke、RequestInput、workflow-owned WorkbenchTask/Human Gate、ResourceCommit 与每步 typed I/O。

## 7. 交互与展示

- 默认首屏为目标、模板或 Agent 问答，不是空白画布。
- 工作区以列表、表格、编辑器、故事板或时间线呈现领域内容。
- 主画布节点提供打开工作台、查看输入输出、运行和错误入口。
- managed 节点的默认卡片展示聚合业务状态，高级运行视图展示实际编译任务与各自责任层。
- Recipe 内部图与主业务图视觉和导航上明确区分。
- 用户从任一工作台返回时保持项目、资源和选中节点上下文。

## 8. 数据、类型与公共接口

调用严格使用主表第 8.2 节矩阵。跨层数据使用 ArtifactRef 或固定 ResourceRef，不传可变 Draft 指针。

WorkbenchTask 至少包含 task_id、owner_workflow_revision_id、input_snapshot_refs、target_workbench、expected_output_schema、base_revision_id、expected_draft_version、HumanTaskStatus、resulting_artifact_refs 和 committed_resource_refs。

`ManagedAgentTaskPlan` 至少包含有序 AgentInvoke、可选 RequestInput、workflow-owned WorkbenchTask/Human Gate、ResourceCommit、typed bindings 和 responsibility mapping；它属于 Workflow 编译产物，不属于 AgentRevision。

工作区路由与布局属于 UI 状态，不写入 ArtifactVersion；需要协作恢复的领域编辑写 ResourceDraft。

## 9. 状态机与业务规则

WorkbenchTask 使用 HumanTaskStatus。submitted 只有在输出通过 schema 和权限校验后才能进入 accepted。

WorkbenchTask accepted 与 ResourceCommit 成功必须可区分；需要下游 ResourceRef 时，只有 CAS 提交完成后父节点才能发布该 ref。AgentInvoke succeeded 不能替代这一提交状态。

工作台提交采用 base_revision 与 draft_version compare-and-swap。冲突时产生 diff，不做最后写入覆盖。

跨层调用固定 Revision；运行中不得解析 latest。内部 trace 失败必须映射回调用节点。

## 10. 失败、降级与恢复

- 目标工作台不可用时 WorkbenchTask 保持 pending，并提供安全错误。
- Recipe 或 Agent 内部失败时主节点显示责任层、可重试性和 correlation ID。
- Draft 冲突时保留双方版本并要求合并。
- 候选移植组件依赖旧 API 时禁用该 adapter，切换新实现。
- 页面刷新后从后端 Draft、Revision 和 Task 状态恢复。

## 11. 安全、隐私、内容与授权

- V1 工作台只允许当前项目 owner 读取项目内固定 Revision/ArtifactVersion 并提交 Draft；跨 owner 输入仍按授权快照和当前 entitlement 校验。
- 跨 owner ResourceRef 必须包含 GrantSnapshot，并在运行时重算 entitlement。
- Agent 与 Recipe 只能获得最小必要字段和批准工具。
- UI 不缓存明文密钥、完整敏感 provider 响应或越权缩略图。

## 12. 观测与运营

- 记录各层调用数量、失败归属、工作台提交时长和 Draft 冲突率。
- trace 可以从主节点下钻至 Agent step、Recipe operator 或 WorkbenchTask。
- 监控逆向调用、运行中 latest 解析和绕过编译器的 provider 请求。
- 组件 adapter 记录来源决策 ID 与版本。

## 13. 验收标准

- AC-1：同一项目可从模板进入工作台、展开主画布并返回，数据 Revision 与选中上下文不丢失。
- AC-2：Agent 尝试调用 Recipe 或 Workflow 时后端拒绝并记录策略错误。
- AC-3：Workbench 直接修改 NodeRun 或调用 Provider 的请求被拒绝。
- AC-4：新增工作区只需注册路由、能力和 schema，不修改主画布固定 slot。
- AC-5：一次 WorkbenchTask 从 Agent Artifact 输入快照到 accepted、ResourceCommit 和下游 ResourceRef，具有完整 base revision、draft version、diff、owner actor 和 run trace。
- AC-6：官方 managed 节点编译后仍以单卡片展示，但高级视图可见 AgentInvoke、人工任务与 ResourceCommit；Agent 尝试创建其中任一 workflow task 时被拒绝。

## 14. 测试场景

- 正常：主 Workflow 调用 Agent、Recipe 和 WorkbenchTask 后得到 typed outputs。
- 边界：51 镜头在分镜工作台编辑但主画布仍保持单个业务节点。
- 失败：Recipe 算子失败映射到主节点且不伪装为 Workflow 失败。
- 权限：越权工作台路由和 ResourceRef 预览均被拒绝。
- 并发/恢复：同一 owner 两个标签页修改同一 Draft 时出现三方 diff；刷新后任务状态恢复。

## 15. 交付与回退

- Foundation 交付产品信息架构、调用矩阵 ADR、路由骨架和跨层 contract tests。
- 候选组件先通过 TF-GOV-002，再接 adapter。
- 可按工作区功能开关逐步开放；关闭某工作区不改变底层 Revision。
- 回退到基础页面时仍能查询资源、运行和审计。

## 16. 已决策事项与开放问题

已决策：工作区是体验层，不是运行时类型；四层调用方向以主表第 8.2 节为准；Agent 只产出 ArtifactVersion，ResourceRevision 冻结由 Workflow/WorkbenchTask 边界负责。

开放问题：各工作区最终导航分组和快捷入口可在可用性测试后调整，但不得改变调用合同。
