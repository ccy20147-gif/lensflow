# Human Gate 与 RequestInput

## 1. 元数据

- ID：TF-WF-008
- 标题：Human Gate 与 RequestInput
- 状态：in_delivery
- 目标版本：V1 Core
- 优先级：P0
- 全局位置：主画布/平台内核
- 直接依赖：TF-WF-006、TF-OPS-004
- 责任域：运行时平台/核心产品
- 个人 DRI：main-agent

## 2. 背景与问题

创作流程需要用户选择候选、确认框架、补充约束或阻断连续性问题。若等待状态只存在于前端弹窗，刷新、超时和重复提交会让运行丢失或被错误恢复。

Human Gate 和 RequestInput 必须是持久业务对象，并与 WorkbenchTask 的富内容编辑职责区分。

## 3. 目标与非目标

目标：

- 持久化 Gate、输入请求、决策、超时和恢复。
- 支持 Agent 仅通过 RequestInput 暂停并获得 typed input；Human Gate 始终由 Workflow 定义和拥有。
- 区分 advisory、domain_required 和 policy_required 强度。
- 保证重复提交、越权提交和过期提交不会推进两次。

非目标：

- 不让前端直接设置 RunStatus。
- 不用 RequestInput 承载复杂工作台编辑，后者属于 WorkbenchTask。
- 不允许用户或 Agent 删除强制 Gate。

## 4. 用户与权限

- V1 一般 Gate、RequestInput 和 WorkbenchTask 只由当前项目 owner 处理；项目成员/指定项目 actor 能力仅在 TF-TEAM-001 后生效。
- policy_required Gate 可由满足策略的平台代理服务或平台审核员处理；这类运营主体不是项目成员，也不获得项目编辑权。
- Agent 可以创建符合自身 revision 与 schema 的 RequestInput，不能创建 Human Gate、指定审批者或把 RequestInput 伪装成富内容工作台。
- 运维人员可诊断卡住任务，不能代表用户接受创作决策。

## 5. 用户场景与主流程

1. Workflow NodeRun 到达编译计划中预定义的 Human Gate，或 Agent 发出符合 AgentRevision 的 RequestInput。
2. 后端持久化输入快照、问题、选项/schema、处理者和超时策略。
3. Run/NodeRun 进入 waiting_user，并通过事件通知前端。
4. 用户查看固定输入，提交接受、拒绝、修订要求或 typed input。
5. 后端校验 actor、版本、schema 和幂等 token。
6. 决策写入后恢复对应 attempt，并通知下游。

## 6. 功能需求

- FR-1：Human Gate 和 RequestInput 必须有稳定 ID、run/node/attempt 关联和输入快照。
- FR-2：Gate 必须声明 advisory、domain_required 或 policy_required。
- FR-3：domain_required 与 policy_required 不得从 Draft、Patch 或运行参数删除。
- FR-4：RequestInput 必须声明 typed schema、必需字段、显示提示和最大响应大小。
- FR-5：等待时 RunStatus/NodeRunStatus 使用 waiting_user。
- FR-6：提交必须携带 task version 与 idempotency token。
- FR-7：决策记录必须保存 actor、时间、输入、选择、备注和策略证据。
- FR-8：重复提交只能接受一次，后续返回原裁决或明确冲突。
- FR-9：超时必须执行预先声明的 fail、cancel、default 或 escalate 策略。
- FR-10：拒绝或要求修订的输出必须走显式控制边或安全失败。
- FR-11：恢复必须继续原固定 attempt 输入，不读取 latest。
- FR-12：通知丢失不能影响后端等待真相。
- FR-13：Human Gate 必须由固定 WorkflowRevision/编译策略拥有；Agent、Media Recipe 和 Workbench 均不得创建、删除、降级或调用 Gate，Agent 唯一允许发起的人工任务是 RequestInput。
- FR-14：managed Agent 单卡片若包含 Gate，编译器必须把 Gate 物化在 AgentInvoke 之外，并在高级运行视图标明 `owner_layer=workflow`。

## 7. 交互与展示

- 任务中心展示待处理 Gate/Input、所属项目、节点、截止时间和强度。
- Gate 页面展示输入 Revision、候选、差异和允许动作。
- policy_required Gate 清楚说明所需证据，不提供绕过按钮。
- RequestInput 根据 schema 使用文本、选择、数字、文件引用等合适控件。
- 已处理、过期或撤销任务只读显示裁决和处理者。

## 8. 数据、类型与公共接口

HumanTaskRecord 至少包含 task_id、task_kind、owner_layer、owner_revision_id、run_id、node_run_id、attempt_id、input_snapshot_refs、assignee_scope、policy_strength、schema_ref、timeout_policy、HumanTaskStatus 和 task_version。

DecisionRecord 包含 action、typed_payload、actor、policy_evidence_refs、submitted_at 和 idempotency_token。

输出使用 ArtifactRef 或结构化 control decision，不直接修改上游 ArtifactVersion。

## 9. 状态机与业务规则

HumanTaskStatus：pending -> in_progress -> submitted -> accepted 或 rejected，也可进入 cancelled、expired。

对应 NodeRun 在任务未终结时保持 waiting_user；父 Run 仅在没有其他必需工作可推进时聚合为 waiting_user。accepted 后按固定控制计划恢复；rejected 按显式拒绝路径处理。

同一 task_version 只能有一个终结裁决。修改问题或 schema 必须创建新任务版本。

## 10. 失败、降级与恢复

- 通知失败时任务仍可在任务中心查询。
- 提交 schema 错误时保持原状态并返回字段错误。
- 任务过期与用户提交竞争时使用事务锁，只有一个终态成功。
- 服务重启后扫描 waiting_user 与未终结任务并恢复索引。
- 原 attempt 已取消时拒绝晚到决策，不创建下游任务。

## 11. 安全、隐私、内容与授权

- 一般任务处理者必须是当前项目 owner 并具有输入 refs 的当前访问权限；平台代理处理 policy_required Gate 时只获得策略要求的最小证据视图。
- Gate 展示按最小必要原则裁剪敏感 Artifact。
- policy evidence 受审计保护，不能由客户端声明“已验证”。
- RequestInput 文本与文件引用在进入 Agent 前做内容和 prompt injection 防护。
- 决策记录不可无痕修改或删除。

## 12. 观测与运营

- 记录待处理数量、等待时长、接受/拒绝/过期率和重复提交。
- 监控长期 waiting_user、无 assignee、恢复失败和通知积压。
- 事件包含 task_id、run_id、after_seq 和安全摘要。
- policy_required Gate 的绕过尝试立即告警。

## 13. 验收标准

- AC-1：刷新浏览器和重启服务后，waiting_user 任务仍可处理并恢复原 run。
- AC-2：同一 idempotency token 重复提交十次只产生一个 DecisionRecord。
- AC-3：项目 owner 与一个无权账户并发提交一般任务时，只有 owner 的首次合法裁决生效；无权请求不占用终态。
- AC-4：删除 domain_required 或 policy_required Gate 的 Patch 被编译器拒绝。
- AC-5：过期策略为 fail 的任务到期后 run 失败，晚到提交不恢复运行。
- AC-6：RequestInput 输出通过 schema 校验并作为固定 typed input 进入原 attempt。
- AC-7：Agent 尝试创建 Human Gate 或把 RequestInput 的 owner_layer 声明为 workflow 时被策略拒绝；managed 节点中的 Gate 可追踪到固定 WorkflowRevision。

## 14. 测试场景

- 正常：接受、拒绝、要求修订和 typed input 恢复。
- 边界：大文本、可选字段、无截止时间、多候选和任务取消。
- 失败：通知、schema、恢复、过期和下游调度失败。
- 权限：非 owner、跨 owner、伪造平台审核员、无权 policy evidence 和越权输入 refs。
- 并发/恢复：owner/无权账户竞争、过期竞争、重复事件和服务重启。

## 15. 交付与回退

- V1 Core 先交付后端任务、任务中心和基础 Gate，再开放 Agent RequestInput。
- 强制 Gate 通过模板与 compiler policy 同时保护。
- UI 回退时任务仍可经安全 API 查询和处理。
- 发布证据包括刷新、重启、超时、重复提交和绕过攻击演练。

## 16. 已决策事项与开放问题

已决策：前端弹窗不是等待真相；Agent 可以持久 RequestInput，但 Human Gate 只能由 Workflow 拥有和编译。

开放问题：外部邮件或移动推送不是 V1 P0，新增通道只能投递通知，不能承载未认证裁决。
