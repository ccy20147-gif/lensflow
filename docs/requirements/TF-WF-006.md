# 持久 DAG 执行与异步恢复

## 1. 元数据

- ID：TF-WF-006
- 标题：持久 DAG 执行与异步恢复
- 状态：in_delivery
- 目标版本：Foundation -> V0 -> V1 Core
- 优先级：P0
- 全局位置：平台内核
- 直接依赖：TF-WF-003、TF-WF-004、TF-WF-005、TF-OPS-003、TF-OPS-005
- 责任域：运行时平台
- 个人 DRI：main-agent

## 2. 背景与问题

图片、视频和 Agent 任务可能运行数分钟并通过异步回调完成。浏览器刷新、服务重启、取消、重试、重复回调或晚到 worker 都不能改变运行真相。

运行时必须基于持久 WorkflowRun、NodeRun、Attempt、ProviderInvocationAttempt、任务绑定和 outbox，而不是内存队列或前端状态。任何 provider 网络副作用都必须发生在可恢复的本地提交之后。

## 3. 目标与非目标

目标：

- Foundation 冻结运行、attempt、fencing 和事件合同。
- V0 支持模板所需的最小持久 DAG、取消、重试和重启恢复。
- V1 完成 lease、epoch、task binding、outbox 和复杂恢复。
- 保证每个产物只由当前有效 attempt 发布。
- 保证 provider 提交在进程崩溃、响应丢失和重复投递下不会被盲目重提或重复计费。

非目标：

- 不在运行中读取 WorkflowDraft 或 latest Resource。
- 不保证外部 Provider 一定能硬取消。
- 不实现任意循环或无界 Agent 自迭代。

## 4. 用户与权限

- 项目 owner 可以启动、查看、取消和按策略重试运行。
- Worker 只能领取允许的 NodeRunAttempt，并以服务身份提交心跳和结果。
- Provider callback 只能更新已绑定的 task/attempt。
- 运维人员可诊断和安全重放 outbox，不能伪造业务成功。
- 所有操作按 owner_scope、workflow revision 和 actor 审计。

## 5. 用户场景与主流程

1. 用户以已编译 WorkflowRevision 创建 WorkflowRun。
2. 调度器持久化 NodeRun 并把 ready 节点放入任务队列。
3. Worker 领取 lease，创建或绑定 attempt，固定输入 refs。
4. 同步节点直接提交结果；异步 provider 节点在任何网络调用前，于同一数据库事务原子持久化 `NodeRunAttempt`、关联的 `ProviderInvocationAttempt` 与 `purpose=provider_dispatch` 的 `OutboxEvent`，提交事务后才发送请求。
5. dispatcher 使用稳定 provider idempotency key；响应确认后保存 task binding。提交结果不确定时进入 `unknown` 并只查询/对账，不重新发送生成请求。
6. provider 可在一次请求中返回多个输出；结果发布以 execution epoch/fencing token 条件更新，并在同一事务写入输出 ArtifactVersion、最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent。
7. 刷新或重启后从数据库快照、attempt、task binding 和 outbox 继续。

## 6. 功能需求

- FR-1：WorkflowRun 必须固定 workflow_revision_id、compiled_plan_id、owner_scope 和输入快照。
- FR-2：每个节点执行使用 NodeRun 与一个或多个 NodeRunAttempt。
- FR-3：Attempt 必须记录 attempt_id、attempt_number、execution_epoch、lease 和固定输入。
- FR-4：发布结果必须以 run_id、node_run_id、attempt_id 和 execution_epoch 条件更新。
- FR-5：取消或新 attempt 必须使旧 epoch 失效。
- FR-6：异步 Provider 任务必须通过 WorkflowTaskBinding 关联 attempt 和 provider_task_id。
- FR-7：业务状态、ArtifactVersion 和 outbox 必须在可证明的一致事务边界写入。
- FR-8：重试固定原 attempt 输入；使用最新输入必须创建新 NodeRun 或运行切片。
- FR-9：服务重启后必须恢复 queued、running、waiting_user 和 cancelling 状态。
- FR-10：重复回调、重复调度和重复结果发布必须幂等。
- FR-11：取消停止新调度，并尽力取消活动 worker/Provider。
- FR-12：运行与节点状态严格使用主表第 8.6 节状态族。
- FR-13：NodeRunAttempt 必须使用独立 `AttemptStatus = pending | leased | running | waiting_external | completed | failed | cancelled | superseded | unknown`，不得用 NodeRunStatus 或 provider task 状态代替。
- FR-14：每个外部 provider 请求在网络副作用前必须在同一数据库事务中提交 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent；dispatcher 不得在该事务提交前调用 provider。
- FR-15：运行时必须生成稳定 provider idempotency key 并在 provider 支持时传递；提交是否成功无法确认时进入 unknown 并执行查询/对账，禁止按超时直接重提。
- FR-16：一个 ProviderInvocationAttempt 最多形成一条 ProviderInvocationRecord；一条 ProviderInvocationRecord 可通过一个或多个 ProviderOutputBinding 关联多个 ArtifactVersion，只有真实发起多个外部请求时才创建多组 Attempt/Record。
- FR-17：结果发布必须以 execution epoch/fencing token 条件验证当前 Attempt 仍可发布，并在一个事务内写入输出 ArtifactVersion、最多一条 ProviderInvocationRecord、多条 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得留下部分结果或部分计费。
- FR-18：fallback 必须从原始固定输入重新获取 CapabilitySnapshot、重新编译、重新执行授权决策和成本估算；确认需要新外部请求后创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，不得沿用原 attempt 或编译报告。

### 逐版本切片矩阵

| 能力 | Foundation | V0 | V1 Core |
| --- | --- | --- | --- |
| 合同 | Run/NodeRun/Attempt/fence schema | 持久线性与简单 DAG | 完整控制流与局部运行 |
| 异步 | invocation attempt、dispatch outbox、task binding 与回调 contract | 一个真实 Provider 的幂等提交/unknown 对账 | 多 Provider、fallback、多输出与重复回调 |
| 调度 | 状态转换和幂等规则 | 最小队列、取消、重试 | lease、heartbeat、epoch 和抢占恢复 |
| 一致性 | Artifact/outbox 事务 ADR | 重启不丢状态 | 晚到 worker 与 outbox 重放 |
| 用户证据 | 运行快照 schema | 刷新后可见成本/失败 | 完整 trace 和节点级恢复 |

## 7. 交互与展示

- 运行页展示 RunStatus、节点状态、开始/结束时间、成本和安全错误。
- 用户只能看到当前状态允许的取消、重试或重新生成动作。
- waiting_user 节点链接到对应 Human Gate、RequestInput 或 WorkbenchTask；只要仍有其他 required 节点可推进，运行级状态继续显示 running。
- cancelling 明确表示已请求但外部任务可能仍在终止。
- 历史 attempt 可下钻查看输入版本、错误和 Provider request ID。

## 8. 数据、类型与公共接口

核心对象为 WorkflowRun、NodeRun、NodeRunAttempt、ProviderInvocationAttempt、WorkflowTaskBinding、ProviderInvocationRecord、ProviderOutputBinding、OutboxEvent 和运行快照。ProviderInvocationAttempt 的 `attempt_status` 复用关联 NodeRunAttempt 的公共 AttemptStatus；provider 内部 submitted/queued/processing 等阶段只能记录为 task binding 事件或明细，不能形成平行公共状态枚举。

输入输出使用 ArtifactRef/ResourceRef；ArtifactRef 只允许同一 owner_scope，跨 owner 内容必须按 TF-WF-005 提升后使用固定 ResourceRef。运行只消费固定 Revision，Provider 实际调用引用 ProviderInvocationRecord。

OutboxEvent 包含 aggregate、purpose、event_type、payload、dedupe_key 和 publish status，但业务数据库记录仍是真相。`provider_dispatch` outbox 的 dedupe_key 固定 invocation_attempt_id；发送成功、未知与对账结果都关联同一 ProviderInvocationAttempt，不通过创建新业务 attempt 掩盖不确定提交。`result_publish` outbox 与 ArtifactVersion、Record、OutputBinding 和成本同事务提交，事件投递失败只重放该已提交事件。

## 9. 状态机与业务规则

RunStatus：pending -> queued -> running，可进入 waiting_user、completed、failed、cancelling、cancelled。

NodeRunStatus：pending -> ready -> queued -> running，可进入 waiting_user、completed、failed、skipped、cancelled、stale。

AttemptStatus：pending -> leased -> running，可进入 waiting_external、unknown、completed、failed、cancelled、superseded；unknown 对账后只能收敛到 waiting_external、completed、failed 或 cancelled。completed、failed、cancelled 和 superseded 为 attempt 终态；unknown 在对账完成前不是可重试终态。只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord。新 epoch 生效后，旧 attempt 必须转为 superseded，晚到结果不可发布。

RunStatus 按以下优先级聚合：显式取消为 cancelling/cancelled；存在 ready/queued/running 的 required NodeRun、处于 waiting_external/unknown 的有效 attempt 或其他可推进的 required 工作时为 running；只有不存在其他 required 工作可推进且至少一个 required 节点等待 Human Gate、RequestInput 或 WorkbenchTask 时才为 waiting_user；必需路径终结且存在未被 Fallback 消费的失败时为 failed；全部必需输出完成时才为 completed。NodeRunStatus 与 AttemptStatus 必须独立保存和展示。

## 10. 失败、降级与恢复

- Worker 失联后 lease 到期，调度器创建新 attempt 或按节点策略失败。
- 晚到旧 worker 的条件更新失败，不能发布 ArtifactVersion。
- Provider 回调早于本地轮询时按 task binding 幂等合并。
- dispatch outbox 可重复投递，但只能重放同一 ProviderInvocationAttempt：支持幂等键的 provider 返回既有请求；不支持或提交结果未知时先查询/对账，禁止盲目发起第二次请求。
- 进程在网络发送前崩溃时由未消费 dispatch outbox 恢复；进程在发送后、保存响应前崩溃时将提交置为 unknown，并以 idempotency key、provider request 查询或运营对账收敛。
- fallback 仅在 unknown 已对账收敛且策略允许新请求后执行；新请求重新快照、编译、授权、估算并创建新 attempt/outbox，原 Attempt/Record 保留且不得被覆盖。
- 结果事务的 fencing 条件失败时将晚到 attempt 标记 superseded 或隔离审计，不写 Artifact、InvocationRecord、OutputBinding、成本或 result_publish outbox。
- 队列不可用时 ready 状态保留，恢复后重新投递。
- outbox 投递失败不回滚已提交业务结果，后台持续重试。
- 无法硬取消的 Provider 结果到达时记录 discarded，不被当前 run 采用。

## 11. 安全、隐私、内容与授权

- 启动、取消和重试均验证 owner_scope 与运行权限。
- Worker lease token、Provider callback secret 和凭证不进入用户日志。
- Attempt 只获得节点声明的最小输入。
- 安全 Gate 失败不能通过 retry 参数绕过。
- 运行 trace 展示时按权限裁剪上游内容。

## 12. 观测与运营

- 指标包括运行成功率、队列等待、节点时长、lease 过期、重试、取消、重复回调、unknown 提交、对账时长和重复收费拦截。
- 记录每次状态转换的 actor、原因、previous/new state 和 correlation ID。
- 监控 stuck running、长期 cancelling、outbox 积压和孤儿 task binding。
- 提供按 provider_request_id、run_id 和 attempt_id 关联诊断。

## 13. 验收标准

- AC-1：V0 运行在浏览器刷新和 API 服务重启后继续，最终状态与 ArtifactVersion 可读。
- AC-2：创建新 attempt 后，旧 worker 晚到提交结果被 fencing 拒绝。
- AC-3：同一 Provider 回调重复十次只产生一个有效状态转换和一组输出。
- AC-4：取消运行后不再调度新节点，活动任务进入取消流程，晚到输出不被采用。
- AC-5：retry 使用原固定输入；选择最新输入会生成新的运行切片和 lineage。
- AC-6：模拟 outbox 投递失败后恢复，事件最终送达且业务记录不重复。
- AC-7：模拟 provider 已接收请求但客户端超时，Then attempt 进入 unknown、系统只执行查询/对账；恢复后不存在盲目重提、重复任务或重复费用。
- AC-8：同一 provider 请求返回三个候选时，Then 只存在一个 ProviderInvocationAttempt/ProviderInvocationRecord、三个 ProviderOutputBinding 和三个独立 ArtifactVersion。
- AC-9：模拟结果发布事务在任一写入点失败，Then ArtifactVersion、InvocationRecord、OutputBinding、实际成本与 result_publish OutboxEvent 全部不提交；重试后只形成一组完整结果。
- AC-10：Given 主 provider 已明确失败且允许 fallback，When 发起新请求，Then 系统重新固定能力快照、编译报告、授权决策和成本估算，并创建新的 NodeRunAttempt/ProviderInvocationAttempt/dispatch OutboxEvent；原 attempt 的数据不被改写。

## 14. 测试场景

- 正常：同步节点、异步 Provider、多个节点依赖和完成。
- 边界：零节点、50 节点、长等待、最大重试、并行 required 工作与 waiting_user、多输出响应。
- 失败：worker 崩溃、发送前/发送后进程崩溃、队列中断、Provider 超时/unknown、重复/乱序回调和 outbox 故障。
- 权限：跨 owner 取消、伪造 worker、伪造 callback 和读取敏感 trace。
- 并发/恢复：两个 worker 争抢、lease 过期、服务重启和晚到结果。

## 15. 交付与回退

- Foundation 交付状态合同、持久 schema、fencing 与故障 contract tests。
- V0 交付一个真实 Provider 的最小持久运行。
- V1 逐步启用 lease、完整异步恢复和复杂 DAG，使用功能开关控制调度器。
- 回退调度器时不回退运行数据；不支持的新状态保持只读并由兼容 worker 收敛。

## 16. 已决策事项与开放问题

已决策：数据库运行记录是真相；前端、内存队列和 Pub/Sub 只是视图或投递。ProviderInvocationAttempt 与 dispatch outbox 先于网络副作用持久化，unknown 只对账不盲重提；AttemptStatus 独立聚合，waiting_user 仅在无其他 required 工作可推进时成立。

开放问题：具体队列和 worker 框架由运行时 ADR 固定，不得改变 attempt fencing 与持久恢复语义。
