# 多智能体显式编排

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-AGT-004 |
| 标题 | 多智能体显式编排 |
| 状态 | in_delivery |
| 版本 | V1 Core |
| 优先级 | P1 |
| 全局位置 | 主画布 |
| 直接依赖 | TF-AGT-001、TF-WF-006、TF-WF-007、TF-WF-008 |
| 责任域 | Agent 产品/运行时 |
| 个人 DRI | main-agent |

## 2. 背景与问题

世界观、框架、扩写等能力需要协作，但若把多个 Agent 藏在一个 Agent 内，用户无法替换、并行、审查或定位失败。多 Agent 协作必须在业务画布上成为可见、可编译的 DAG。

## 3. 目标与非目标

- 支持多个固定 AgentRevision 通过 typed artifacts 顺序或并行协作。
- 将等待用户、失败、成本和产物归属显示到具体节点。
- 非目标：不提供 Agent 内嵌调用、隐式共享内存、递归协作或无限自迭代。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可查看、选择、连接、配置和运行 AgentRevision；项目成员、共享编辑、审阅和只读角色统一后置 TF-TEAM-001。
- owner 需拥有所有 Agent、工具、同 owner ArtifactRef 和固定输入 ResourceRef 的当前权限。
- 项目 trace 与产物仅 owner 可查看；平台运营主体仅能在独立审计授权下查看净化信息，不能据此获得源资源复用权。

## 5. 用户场景与主流程

1. 用户在主画布连接“世界观智能体 -> 小说框架智能体 -> 扩写智能体”。
2. 编译器验证每个 AgentRevision、typed port、授权、预算和控制流。
3. 运行时按 DAG 调度；Agent 输出不可变 ArtifactVersion，后继在同 owner_scope 内通过 ArtifactRef 读取，跨 owner 输入只能使用带授权证据的固定 ResourceRef。
4. 框架 Agent 请求输入时该节点进入 waiting_user，依赖分支暂停，无关分支可继续。
5. 恢复后下游执行，运行视图按节点呈现成本、失败和 lineage。

## 6. 功能需求

- FR-1：每个 Agent 必须是独立可见节点并固定 `agent_revision_id`。
- FR-2：Agent 间只允许注册 schema 的 typed port 连接，不允许隐藏会话内存或按名称查找产物。
- FR-3：顺序、并行、Join、Fallback、有限 Map/OrderedMap/Fold 复用 TF-WF-007，不另建 Agent 控制流。
- FR-4：每个节点独立记录 AgentInvoke、attempt、checkpoint、step trace、用量、成本、输入和输出 refs。
- FR-5：RequestInput 复用 TF-WF-008，等待只阻塞依赖闭包并保存问题、期限和恢复 token。
- FR-6：重跑单一 Agent 节点必须基于固定输入快照生成新输出版本，并标记下游 stale。
- FR-7：失败归属到具体 Agent node/attempt；Fallback 不得掩盖原始失败与成本。
- FR-8：编译器必须拒绝 AgentRevision 内的 Agent/Workflow/Recipe 嵌套引用。
- FR-9：Human Gate、WorkbenchTask、ResourceCommit 和业务 ResourceRevision 只能由 Workflow 计划/运行时拥有；Agent 节点只能输出 typed ArtifactVersion，不能创建或修改这些对象。

## 7. 交互与展示

- 画布节点显示 Agent 名称、revision、输入输出、运行状态和成本摘要。
- 并行分支和 Join 使用标准工作流视觉，不引入“智能体群聊”黑盒。
- 运行抽屉按节点展示步骤、RequestInput、工具调用、产物和错误。
- 节点升级必须显式选择新 revision 并显示 I/O、权限与行为 diff。

## 8. 数据、类型与公共接口

- 节点配置使用主表 `AgentInvoke`；边传输同 owner_scope ArtifactRef、固定 ResourceRef 或注册 typed value。
- 跨 owner 内容必须先提升为 ResourceRevision，并以带 `grant_snapshot_id` 的固定 ResourceRef 连接；禁止给 ArtifactRef 附加 grant 以绕过 owner_scope。
- `AgentNodeTrace` 扩展运行记录：`agent_revision_id`、`step_traces[]`、`checkpoint_refs[]`、`tool_invocation_refs[]`。
- 共享业务状态必须表现为不可变 ArtifactVersion；可编辑业务身份通过 ResourceDraft/Revision 提升。
- 编译计划固定所有 Agent 与 schema revision，不读取 latest。

## 9. 状态机与业务规则

- Agent 节点使用 NodeRunStatus；整图使用 RunStatus；补问使用 HumanTaskStatus，三者不得合并。
- waiting_user 恢复以 human_task_id 和答案 hash 幂等，重复答案不重复调度下游。
- 并行分支的输出顺序由端口/映射定义，不能依完成时间隐式决定。
- 重试只创建新 attempt；完成 attempt 的输出不可原地替换。

## 10. 失败、降级与恢复

- 单节点失败按图策略停止依赖闭包、进入 Fallback 或等待人工处理，无关分支不被误杀。
- worker 失联由 lease/epoch 恢复；晚到结果不得覆盖新 attempt。
- AgentRevision 或授权失效只阻断新编译/新运行，运行中行为按安全策略取消或隔离并留痕。
- 刷新后从持久 Run/NodeRun/HumanTask 恢复，不依赖前端内存。

## 11. 安全、隐私、内容与授权

- 每条边按最小披露传递字段；Agent 不能读取未连接的项目产物。
- 每个节点在运行前复核 Agent、工具、输入资源和输出用途授权。
- 任一节点的工具调用只能走平台批准的 ToolInvocation 与凭证 broker，不能借多 Agent 编排获得任意网络或代码能力。
- trace 中敏感提示、owner 回答和工具输出按数据类别与独立运营授权净化；错误不得泄露 secret。

## 12. 观测与运营

- 运行拓扑、node/attempt、AgentRevision、输入输出 refs、等待时长、成本和失败原因全链路关联。
- 指标包括多 Agent 运行成功率、关键路径时长、并行利用率、waiting_user 恢复率和节点重跑率。
- 运营可按 AgentRevision 聚合质量/失败，但不得跨租户查看正文。

## 13. 验收标准

- AC-1：Given 三个 compatible Agent 节点，When 顺序运行，Then 每个输出均为可追踪版本且下游只读取连接输入。
- AC-2：Given 两个并行 Agent 和 Join，When 完成顺序颠倒，Then Join 输出确定且可重放一致。
- AC-3：Given 中间 Agent waiting_user，When 刷新、重启并回答，Then 从同一 checkpoint 恢复且不重复前置节点。
- AC-4：Given 一个节点失败并配置 Fallback，When 执行，Then 原失败与成本保留、Fallback 可识别、无关分支按计划运行。
- AC-5：Given AgentRevision 内含嵌套调用，When 编译，Then 图在运行前被拒绝。
- AC-6：Given Agent 节点尝试创建 Gate、WorkbenchTask、ResourceCommit 或业务 Revision，When 编译或执行，Then 请求被拒绝且只保留其 typed ArtifactVersion 输出；相应任务只能由 Workflow 计划创建。
- AC-7：Given 边包含跨 owner 裸 ArtifactRef，When 编译，Then 连接被拒绝；使用带有效授权证据的固定 ResourceRef 后才可运行。

## 14. 测试场景

- 正常：三 Agent 顺序、双分支并行、Join、人工补问和单节点重跑。
- 边界：零输出 Agent、最大允许并行度、有限 Map、多个 waiting_user 节点。
- 失败：工具失败、schema 失配、Fallback 失败、预算耗尽、晚到 worker。
- 权限：非 owner 项目访问、节点间越权读取、跨 owner 裸 ArtifactRef、Agent/输入资源撤权和平台运营 trace 隔离。
- 并发/恢复：重复回调、答案重复提交、运行取消竞态、服务重启和事件重放。

## 15. 交付与回退

- 先用官方三 Agent 基准启用，再开放任意 configurable Agent 组合。
- 功能开关可禁用新多 Agent 编译；既有运行继续按固定计划恢复或安全取消。
- 交付证据含顺序/并行基准、waiting_user 恢复、失败归属、权限与确定性重放报告。

## 16. 已决策事项与开放问题

- 已决策：多智能体协作必须显式建模在主画布，节点间不共享隐式状态。
- 已决策：控制流归属通用工作流运行时，Agent 自身不得嵌套调用。
- 已决策：V1 项目 owner-only；Agent 节点只产出 ArtifactVersion，Gate、WorkbenchTask、ResourceCommit 与 Revision 均归 Workflow 所有。
- 开放问题：无阻塞 V1 Core 的开放问题。
