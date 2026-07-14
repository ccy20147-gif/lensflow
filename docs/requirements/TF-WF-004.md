# Workflow 草稿与不可变修订

## 1. 元数据

- ID：TF-WF-004
- 标题：Workflow 草稿与不可变修订
- 状态：in_delivery
- 目标版本：Foundation -> V0 -> V1 Core
- 优先级：P0
- 全局位置：主画布/平台内核
- 直接依赖：TF-ARC-001、TF-WF-002
- 责任域：工作流平台
- 个人 DRI：main-agent

## 2. 背景与问题

用户编辑、Agent 提案和运行可能同时作用于一个工作流。若运行读取可变草稿或保存采用最后写入覆盖，历史运行无法解释，用户修改也会丢失。

Workflow 需要稳定身份、可变 Draft、不可变 Revision 和分离的 graph/layout hash。

## 3. 目标与非目标

目标：

- 从 Foundation 起冻结 Workflow 数据合同和 hash 规则。
- V0 支持保存、加载、激活 Revision 和固定运行。
- V1 支持 owner 双标签并发检测、diff、回滚和 owner 确认的 Agent 提案 Patch。
- 保证任何运行只引用不可变 WorkflowRevision。

非目标：

- 不实现多人实时协作；团队角色、评论审阅和共享编辑统一后置 TF-TEAM-001。
- 不允许激活后的 Revision 原地修改。
- 不把社区 ListingStatus 与内部 RevisionStatus 混用。

## 4. 用户与权限

- 项目 owner 可以编辑 Draft、创建 Revision 和选择 active Revision。
- Workflow Architect 只能输出带 base_hash 的 Proposal ArtifactVersion；owner 确认后由平台 API 应用 Patch。
- 运行服务只读 WorkflowRevision。
- owner 可比较历史 Revision；非 owner 不得查看项目 Draft、Revision 或运行状态。
- 系统迁移必须创建新 Revision 或明确 schema migration 记录。

## 5. 用户场景与主流程

1. 用户创建 Workflow，系统建立空 Draft。
2. owner 手工修改 graph、config 和 layout，或确认 Agent Proposal 后由平台 API 原子应用 Patch；Agent 本身不写 Draft。
3. 保存请求携带 base_hash，成功后返回新 draft hash。
4. 用户请求激活，后端校验并冻结 WorkflowRevision。
5. 运行固定该 revision_id，不读取后续 Draft。
6. 用户可比较、复制或回到旧 Revision 创建新 Draft。

## 6. 功能需求

- FR-1：Workflow 提供稳定 workflow_id，与 Draft 和 Revision 分离。
- FR-2：WorkflowDraft 保存 graph、config、layout、base_revision_id 和 draft_version。
- FR-3：保存必须使用 base_hash compare-and-swap。
- FR-4：WorkflowRevision 内容不可变，并有 revision_number、graph_hash 和 execution_hash。
- FR-5：纯 layout 变化不得改变 execution_hash。
- FR-6：激活 Revision 前必须通过 TF-WF-003 编译与策略校验。
- FR-7：运行记录必须固定 workflow_revision_id。
- FR-8：Revision diff 必须区分节点、边、配置、固定依赖和 layout。
- FR-9：回滚通过旧 Revision 派生新 Draft，不改变 active 历史 Revision。
- FR-10：Agent Proposal/Patch 必须绑定 base_hash、显示 diff 并经 owner 独立确认；只有 TF-WF-004 API 可写 WorkflowDraft 或创建 WorkflowRevision，Agent 不得执行这些写入。
- FR-11：旧 Revision 即使退役仍可读取并保留运行引用。
- FR-12：导入 Workflow 先建立 Draft，不直接激活不可信图。

### 逐版本切片矩阵

| 能力 | Foundation | V0 | V1 Core |
| --- | --- | --- | --- |
| 数据合同 | Workflow/Draft/Revision schema | 持久保存与加载 | 兼容迁移与完整历史 |
| hash | graph、layout、execution 规则 | 运行固定 hash | Agent Proposal/Patch 与 diff 校验 |
| 并发 | base_hash contract | 单用户双标签冲突 | 用户与 Agent 并发裁决 |
| Revision | 冻结与只读规则 | 激活、复制、回退 | 版本比较与迁移诊断 |
| 运行隔离 | revision_id 强制 | V0 运行只读 Revision | 局部运行仍固定 Revision |

## 7. 交互与展示

- 顶部显示 Draft 未保存、已保存、与 active Revision 差异。
- Revision 历史展示创建者、时间、摘要、编译状态和运行数量。
- diff 以节点/边/配置为主，layout 变化单独折叠。
- 冲突界面保留本地 patch，支持刷新、合并或复制为新 Draft。
- 内部激活使用“激活版本”，不使用社区“发布”术语。

## 8. 数据、类型与公共接口

Workflow、WorkflowDraft 和 WorkflowRevision 使用稳定 ID。WorkflowRevision 引用 RegistrySnapshot、固定依赖和内容 hash。

graph 中的 ArtifactRef、ResourceRef、AgentInvoke、MediaRecipeInvoke 与 PackageDependency 遵循主表第 8 节。ArtifactRef 只能固定同 owner_scope 内容；跨 owner 内容必须先提升为 ResourceRevision，再使用带授权证据的固定 ResourceRef。

Draft 可以引用 latest_at_compile 意图，但 Revision 和执行计划只能包含解析后的固定引用。

## 9. 状态机与业务规则

WorkflowRevision 使用 RevisionStatus：draft 仅表示尚未激活的冻结候选，active 可供运行，retired 不用于新运行。

WorkflowDraft 不复用 RevisionStatus，使用 draft_version 和保存状态。

一次激活请求基于明确 draft_hash；激活期间 Draft 可继续编辑，但新修改不会进入该 Revision。

## 10. 失败、降级与恢复

- base_hash 过期时返回结构化冲突，不覆盖现有 Draft。
- 编译失败时保留 Draft 与诊断，不创建 active Revision。
- 激活事务中断时不得留下可运行的半 Revision。
- 历史 schema 无法解析时提供只读原始内容与迁移诊断。
- 缓存丢失后从持久 Draft/Revision 恢复。

## 11. 安全、隐私、内容与授权

- 保存和激活都验证 owner_scope。
- 图中的 secret 只能保存 CredentialBinding 引用。
- 导入图与 Agent Patch 视为不可信输入。
- 固定跨 owner 资源时必须使用 ResourceRef 保存 GrantSnapshot，并在运行前重算 entitlement；跨 owner 裸 ArtifactRef 或附带 grant 字段的 ArtifactRef 必须拒绝。
- 审计记录保存 actor、base_hash、diff hash 和裁决。

## 12. 观测与运营

- 记录保存成功率、冲突率、激活失败、Revision 数和旧版本读取。
- 监控运行读取 Draft、Revision 内容 hash 漂移和孤儿 Revision。
- Agent Proposal/Patch 应用与 owner 手工保存分别统计冲突和拒绝原因。
- 每次激活关联 compiler plan 与 build SHA。

## 13. 验收标准

- AC-1：激活 Revision 后修改 Draft，已启动及后续固定旧 Revision 的运行输入不变。
- AC-2：两个标签页使用同一 base_hash 保存时最多一个成功。
- AC-3：只移动节点会改变 layout hash，不改变 execution_hash。
- AC-4：修改节点配置、边或固定资源 revision 必须改变 execution_hash。
- AC-5：回到旧 Revision 会创建新 Draft，旧 Revision content hash 保持不变。
- AC-6：Agent Patch 未经 diff 展示、确认或 base_hash 复核不能应用。

## 14. 测试场景

- 正常：创建、保存、激活、运行、比较和回退。
- 边界：空图、50 节点、纯 layout 修改和大量 Revision。
- 失败：编译失败、激活事务中断、旧 schema 不可解析。
- 权限：非 owner 项目访问、跨 owner 裸 ArtifactRef、缺授权 ResourceRef、无权激活和 secret 明文导入被拒绝。
- 并发/恢复：owner 双标签、owner 手工保存与平台应用已确认 Agent Patch 竞争、服务重启后 Draft 恢复。

## 15. 交付与回退

- Foundation 先交付 schema、hash 和 contract tests。
- V0 开放保存、激活和固定运行。
- V1 再开放完整 diff、Agent Patch 和迁移诊断。
- 回退 UI 或 compiler 时不修改历史 Revision；不兼容 Revision 只读。

## 16. 已决策事项与开放问题

已决策：任何运行固定不可变 WorkflowRevision；内部激活与社区上架是不同状态。

已决策：V1 私有项目 owner-only；Agent 只输出 Proposal ArtifactVersion，WorkflowDraft/Revision 写入由 owner 确认后的平台 API 执行。

开放问题：Revision 自动摘要可由规则或模型生成，但不影响 hash、diff 和审计真相。
