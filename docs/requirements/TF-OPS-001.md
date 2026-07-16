# Provider、模型与密钥管理

## 1. 元数据

- ID：TF-OPS-001
- 标题：Provider、模型与密钥管理
- 状态：in_delivery
- 目标版本：Foundation -> V1 Core
- 优先级：P0
- 全局位置：设置/平台内核
- 直接依赖：TF-ARC-001、TF-GOV-001
- 责任域：Provider 平台/安全
- 个人 DRI：待指派

## 2. 背景与问题

图片、视频、文本和音频 Provider 的模型、控制能力、价格、限额和异步协议持续变化。若工作流只保存模型名称或运行时动态选择 latest，就无法解释控制降级、fallback、成本和结果来源。

平台必须分离 Provider 定义、能力快照、选择策略、CredentialBinding、提交尝试和已对账实际调用记录，并让所有网络副作用都可从持久状态恢复。

## 3. 目标与非目标

目标：

- Foundation 完成真实 Provider spike、能力合同和密钥隔离。
- V1 Core 支持多 Provider、版本化能力、健康检查和受控 fallback。
- 每次实际调用保存模型版本、参考顺序、编译报告、用量和成本。
- 在网络调用前持久化提交意图；对不确定提交执行对账，避免盲重试和重复计费。
- 密钥不进入图、Artifact、日志或社区包。

非目标：

- 不建设模型交易市场。
- 不保证不同 Provider 参数一一等价。
- 不把 Provider 响应当作 Workflow 状态真相。

## 4. 用户与权限

- 账户 owner 可以配置自己有权使用的 Provider CredentialBinding。
- V1 私有项目只有项目 owner 可以选择允许的 Provider policy，且不能读取明文密钥；共享项目角色后置到 TF-TEAM-001。
- 平台管理员管理平台级 Provider 定义和能力 revision。
- Worker 只在执行时获得短期最小凭证。
- 支持人员只能查看净化健康与调用摘要。

## 5. 用户场景与主流程

1. 管理员登记 Provider、endpoint、协议、模型和能力 schema。
2. 用户创建或选择 CredentialBinding，系统验证但不回显 secret。
3. 工作流编译固定 ProviderSelectionPolicyRef 与 CapabilitySnapshotRef。
4. 执行器按策略选择模型并编译控制参数，在任何网络调用前于同一数据库事务原子写入 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent。
5. dispatcher 使用稳定 provider idempotency key 发起请求；提交结果不确定时进入 unknown 并查询/对账，禁止直接重提。
6. 回调或响应完成后，以 execution epoch/fencing token 条件验证 Attempt 仍可发布，并在同一事务写入输出 ArtifactVersion、最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent。
7. fallback 从原始固定输入重新固定能力快照、编译选择与控制、验证授权并估算成本；真实发起新外部请求前创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent。

## 6. 功能需求

- FR-1：ProviderDefinition 和 ModelDefinition 必须使用稳定 ID 与不可变 capability revision。
- FR-2：CapabilitySnapshot 必须描述输入输出、参考数量、控制项、限制和异步协议。
- FR-3：ProviderSelectionPolicy 必须版本化并声明候选、顺序、成本/质量约束和 fallback。
- FR-4：CredentialBinding 只能保存 secret provider 引用和元数据，不能保存进 Workflow。
- FR-5：编译必须产生 ProviderCompilationReport，且每个 requested control/fragment_path 恰好使用 applied、transformed、degraded、ignored_with_warning 或 blocked 之一作为最终 outcome。
- FR-6：每个拟发起的真实外部请求必须在网络副作用前，于同一数据库事务提交 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent；dispatcher 只能在事务提交后发送。
- FR-7：ProviderInvocationRecord 必须记录 invocation_attempt_id、provider、model、model_version、capability snapshot、request fingerprint、provider request ID、参考顺序、output_bindings、usage 和 actual cost；一个 ProviderInvocationAttempt 最多形成一条 ProviderInvocationRecord，一条 ProviderInvocationRecord 可有一个或多个 ProviderOutputBinding。
- FR-8：fallback 必须从原始固定输入重新获取 CapabilitySnapshot、重新编译、重新验证授权并重新估算成本；只有确认需要真实新外部请求时才创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，新 record 的 fallback_from 指向原调用，原 attempt/record 不得覆盖。
- FR-9：Provider 健康检查必须区分配置错误、认证失败、限流、模型不可用和网络故障。
- FR-10：模型或能力变化创建新 revision，不覆盖旧快照。
- FR-11：密钥轮换和撤销不得修改历史调用记录。
- FR-12：运行中不得通过显示名或 latest 动态替换已编译策略。
- FR-13：平台必须为每次提交生成稳定 provider idempotency key，并在 provider 支持时传递；dispatch 重放只能重放同一 invocation_attempt_id。
- FR-14：发送后响应丢失或无法判断 provider 是否接收时，ProviderInvocationAttempt 的公共 attempt_status 必须进入 unknown，并通过 provider 查询、回调、账单或人工对账收敛，禁止按超时盲目重提。
- FR-15：一次外部请求可返回多个候选；每个候选建立 ProviderOutputBinding 并保存独立 ArtifactVersion，不得为同一请求的每个候选伪造一条 InvocationRecord。
- FR-16：ProviderInvocationAttempt 的 `attempt_status` 必须复用关联 NodeRunAttempt 的公共 AttemptStatus；submitted、queued、processing 等 provider task 阶段只允许作为 task binding 事件或明细，不得建立任何 provider 专用公共状态枚举。
- FR-17：结果发布必须以 execution epoch/fencing token 条件验证当前 Attempt，并在一个事务内写入输出 ArtifactVersion、最多一条 ProviderInvocationRecord、多条 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得发布部分结果。

### 逐版本切片矩阵

| 能力 | Foundation | V1 Core |
| --- | --- | --- |
| Provider | 至少一个真实图片 Provider spike | 图片、视频及可扩展多 Provider |
| 能力 | schema 与首个 CapabilitySnapshot | 版本矩阵、控制编译和退役 |
| 策略 | 固定单 Provider policy | 成本/能力路由与 fallback |
| 密钥 | 安全 CredentialBinding | 用户级、平台级轮换与撤销 |
| 记录 | ProviderInvocationAttempt、dispatch outbox、请求/模型/用量最小记录 | 完整 InvocationRecord、多 OutputBinding、unknown 对账和关联诊断 |

## 7. 交互与展示

- 设置页展示 Provider 状态、可用模型、绑定状态和最近验证时间。
- 密钥创建后只显示掩码、创建者、scope 和轮换动作。
- 节点配置选择 Provider policy 或能力要求，不直接填 secret。
- 编译详情展示控制应用、降级和阻断。
- 运行详情展示实际模型、fallback、用量和成本，不显示原始敏感响应。

## 8. 数据、类型与公共接口

严格使用主表第 8.4 节 ProviderSelectionPolicyRef、CapabilitySnapshotRef、ProviderCompilationReport、ProviderInvocationAttempt、OutboxEvent、ProviderInvocationRecord 和 ProviderOutputBinding。

补充 ProviderDefinition、ModelDefinition、ProviderPolicyRevision、CredentialBinding 和 ProviderHealthSnapshot。

CredentialBinding 只由 binding_id 在计划中引用。ProviderInvocationAttempt 至少包含 invocation_attempt_id、node_run_attempt_id、request_fingerprint、provider_idempotency_key、attempt_status、prepared_request_ref、compilation_report_ref、provider_request_id/task_binding 可选引用和 reconciliation_state；`attempt_status` 只取主表 8.6 的 AttemptStatus。

ProviderInvocationRecord 以 invocation_attempt_id 固定已知实际调用事实，不被后续能力 revision 改写；`output_bindings[]` 中每个 ProviderOutputBinding 记录 output_index、artifact_ref 和可选 provider_output_id。数据库约束保证一个 invocation_attempt_id 最多一条 Record；候选数与 Record 数不存在一对一关系。

## 9. 状态机与业务规则

Provider/Model revision 使用 RevisionStatus。CredentialBinding 可为 active、invalid、revoked。

健康状态只用于调度判断，不改变历史 InvocationRecord。运行启动前重新检查当前可用性。

fallback 只有在计划允许的错误类别、预算和能力范围内发生；不得跨越安全或授权阻断。

ProviderInvocationAttempt 不拥有独立状态机，复用关联 NodeRunAttempt 的 `AttemptStatus = pending | leased | running | waiting_external | completed | failed | cancelled | superseded | unknown`。provider task 的 submitted/queued/processing 等阶段只作为事件或 task binding 明细；unknown 在完成对账前不是可重试终态，只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord。晚到或被新 execution epoch 取代的 attempt 使用 superseded 且不得发布结果。

## 10. 失败、降级与恢复

- 认证失败立即标记绑定 invalid，并停止使用该绑定的新请求。
- provider 明确返回限流且确认未接收业务请求时，才可按 Provider policy 延迟或 fallback，并记录原错误和额外成本；响应不确定仍进入 unknown 对账。
- dispatch outbox 在事务提交后才可发送；发送前崩溃可安全重放同一 attempt，发送后响应丢失则进入 unknown，并以 idempotency key、provider request ID、回调或账单查询对账。
- unknown 未收敛前禁止创建等价新 attempt；只有确认原请求未被接收或由用户显式创建新业务请求后才允许新提交。
- fallback 创建新请求前必须重新快照、编译、授权和估算；不得沿用旧 CompilationReport、Grant/EntitlementDecision 或成本预留。
- 结果发布的 fencing 条件失败时，晚到结果只进入隔离审计；不得写 ArtifactVersion、InvocationRecord、OutputBinding、实际成本或 result_publish OutboxEvent。
- Provider 返回未知字段时保存净化原始引用并按 schema 失败处理。
- 能力服务不可用时不得猜测支持项；使用固定快照并执行启动前健康复核。

## 11. 安全、隐私、内容与授权

- secret 加密存储，通过短期注入提供给 worker。
- API、日志、trace、Artifact 和包均不得包含明文密钥。
- CredentialBinding 按 owner_scope、Provider 和用途限制。
- Provider 请求仅发送已授权且最小必要的输入。
- 密钥读取、验证、轮换和撤销全部审计。

## 12. 观测与运营

- 记录 Provider 可用率、延迟、限流、认证失败、fallback 和模型退役。
- 监控无 dispatch outbox 的 invocation attempt、InvocationRecord 缺失/重复、长期 unknown、对账积压、编译报告不一致、request ID 重复、output binding 缺口、result_publish outbox 积压和 fencing 拒绝。
- 按模型统计成功率、控制降级、usage 与实际成本。
- 对密钥泄漏特征和异常调用量告警。

## 13. 验收标准

- AC-1：Foundation 使用一个真实 Provider 完成请求，并保存能力快照、request ID、模型版本、usage 和成本。
- AC-2：Workflow 与日志扫描找不到明文 Provider secret。
- AC-3：required 控制不支持时编译阻断；可选控制降级进入报告。
- AC-4：主 Provider 明确失败并触发允许的 fallback 后，存在两组关联 NodeRunAttempt/ProviderInvocationAttempt/InvocationRecord；fallback 具有新的 CapabilitySnapshot、CompilationReport、授权决策、成本估算与 dispatch OutboxEvent，成本分别记录。
- AC-5：轮换或撤销密钥后历史调用仍可审计，新请求不再使用旧绑定。
- AC-6：模型显示名相同但 capability revision 不同时，计划和调用可明确区分。
- AC-7：模拟 provider 已接收请求但客户端在保存响应前崩溃，恢复后该 attempt 为 unknown 并通过对账收敛，网络抓包和账单证明未盲目重提或重复计费。
- AC-8：一个真实请求返回三个候选时，只产生一条 InvocationRecord、三个 ProviderOutputBinding 和三个独立 ArtifactVersion；拆成三个真实请求时才产生三条记录。
- AC-9：模拟结果事务在 Artifact、Record、OutputBinding、成本或 result_publish OutboxEvent 任一写入点失败，Then 全部不提交；恢复后只形成一组完整结果且过期 epoch 无法发布。

## 14. 测试场景

- 正常：登记、绑定、验证、编译、调用、回调和轮换。
- 边界：多参考上限、单请求多输出、长任务、相同显示名模型和 retired revision。
- 失败：认证、限流、发送前/发送后崩溃、unknown 提交、对账超时、未知模型、重复请求和 malformed response。
- 权限：跨 owner binding、无权平台密钥和日志 secret 注入。
- 并发/恢复：同时轮换、重复回调、查询恢复和 fallback 竞争。

## 15. 交付与回退

- Foundation 交付 Provider adapter 合同、真实 spike、CredentialBinding、ProviderInvocationAttempt、dispatch outbox、unknown 对账和最小 InvocationRecord/OutputBinding。
- V1 Core 逐个 Provider 功能开关接入，先 shadow 健康与能力比较。
- adapter 回退时固定旧能力 snapshot；不支持的新模型停止新运行。
- 发布证据包括 secret 扫描、真实调用、fallback 和轮换演练。
- 2026-07-16 Foundation 验收：批次 C 的持久 Provider 调用事实链已独立验证，包括提交 intent、stable idempotency key、unknown 对账、结果 fencing、单 record/多 output binding 与 dispatch/result outbox dedupe_key；`8c9d0e1f2a3b` 增加部分唯一 `(purpose, dedupe_key)` 约束。真实 Provider spike 未配置凭证，未作为通过证据；能力版本矩阵、健康、fallback、轮换与完整真实调用仍在交付中，状态为 `in_delivery`。

## 16. 已决策事项与开放问题

已决策：能力、选择策略、提交尝试和实际调用分别版本化；fallback 不能静默改变模型或控制。网络副作用前必须持久化 attempt/outbox，unknown 只对账不盲重提，一次调用允许多个输出绑定。

开放问题：首批具体 Provider 与模型由 spike、许可、成本和质量评测裁决，不在本 PRD 锁定品牌。
