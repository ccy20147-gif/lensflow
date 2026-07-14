# Media Recipe 定义、实验室与调用

## 1. 元数据

| 字段 | 值 |
| --- | --- |
| ID | TF-MR-001 |
| 标题 | Media Recipe 定义、实验室与调用 |
| 状态 | in_delivery |
| 版本 | V1 Core |
| 优先级 | P0 |
| 全局位置 | Media Recipe Lab/主画布 |
| 直接依赖 | TF-WF-002、TF-WF-003、TF-WF-005、TF-WF-006、TF-OPS-001、TF-OPS-002、TF-OPS-003 |
| 责任域 | 媒体平台 |
| 个人 DRI | main-agent |

## 2. 背景与问题

专业 AIGC 流程需要 ComfyUI 粒度的模型、预处理、控制图、采样、转换和评分组合，但把这些原子铺在主业务画布会破坏创作抽象。底层图应在实验室编辑，并以固定修订的业务节点调用。

## 3. 目标与非目标

- 提供有限 DAG 的 Media Recipe 编辑、试跑、版本化、typed I/O 和主画布调用。
- 完整记录算子、provider 能力、控制降级、成本和失败映射。
- 非目标：V1 不支持任意代码、Recipe 嵌套、Agent/Workflow、Human Gate 或 RequestInput；独立社区 listing 后置 V1.5。

## 4. 用户与权限

- 配方作者编辑自己 owner_scope 的草稿并提交 revision。
- 工作流作者仅可调用可访问的固定 MediaRecipeRevision。
- provider、模型、输入素材和输出用途分别按当前权限校验。
- 跨 owner 使用需 GrantSnapshot 记录历史动作，并在每次新编译/运行重新计算 EntitlementDecision。

## 5. 用户场景与主流程

1. 专业用户在 Lab 从算子目录构建多参考图生图配方。
2. 连接 typed ports，声明公开输入、输出、参数范围和 provider capability 要求。
3. 用固定样例试跑，查看逐算子预览、能力编译报告、成本和质量评分；真实 provider 算子在网络前先原子提交 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent。
4. 提交不可变 MediaRecipeRevision。
5. 主业务画布以一个 Recipe 节点固定调用；运行失败映射回配方算子但不展开主画布。

## 6. 功能需求

- FR-1：Lab 必须使用动态算子注册表、schema identity/version 和有限无环图。
- FR-2：RecipeDraft 必须声明 typed public inputs/outputs、参数 schema、默认值、允许范围和 capability requirements。
- FR-3：允许媒体算子、provider、预/后处理、格式转换、评分和有限分支；禁止 Agent、Workflow、Recipe、Human Gate、RequestInput 和任意代码。
- FR-4：提交前由编译器冻结算子 revision、provider policy、模型 capability、输入 schema 和执行计划。
- FR-5：试跑与正式运行均记录逐算子 trace、NodeRunAttempt、ProviderInvocationAttempt、dispatch/result OutboxEvent、ProviderInvocationRecord、ProviderOutputBinding、输入输出 refs、控制降级、用量和实际成本。
- FR-6：主画布仅显示 `MediaRecipeInvoke` 的业务级 I/O、配置、状态和结果，不复制内部算子节点。
- FR-7：每个 requested control/fragment_path 必须恰好产生 applied、transformed、degraded、ignored_with_warning 或 blocked 之一；provider 不支持控制项时按 unsupported_policy 裁决，禁止静默丢失。
- FR-8：单算子重试、缓存和失败必须遵循固定计划；非确定 provider 输出创建新 ArtifactVersion。
- FR-9：修订 diff 必须覆盖图结构、算子/模型版本、参数、I/O 与 capability 变化。
- FR-10：ProviderInvocationAttempt 的 attempt_status 必须复用关联 NodeRunAttempt 的公共 AttemptStatus；provider task 内部 submitted/queued/processing 等阶段只能作为事件或 binding 明细，不建立平行公共状态枚举。
- FR-11：每个真实外部请求在网络副作用前必须于同一数据库事务提交 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent；事务提交后 dispatcher 才可发送。
- FR-12：结果发布必须以 execution epoch/fencing token 条件更新，并在一个事务内写入输出 ArtifactVersion、每 Attempt 最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得形成部分算子输出。
- FR-13：发送后无法确认 provider 是否接收时 AttemptStatus 进入 unknown，只能查询、回调、账单或人工对账，禁止超时盲重提或直接 fallback。
- FR-14：fallback 必须从该算子的原始固定输入重新获取 CapabilitySnapshot、重新编译、重新验证授权并重新估算成本；确认需要新请求后创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，原 attempt/record 不覆盖。

## 7. 交互与展示

- Lab 提供节点图、参数检查器、输入输出面板、逐算子预览、运行时间线和版本页。
- 主画布 Recipe 节点保持单节点抽象，可从失败详情深链到只读 Lab trace。
- 提交前展示公开合同、依赖、估算成本、控制降级和不可移植 provider 条件。
- 高级参数渐进披露；危险或不兼容组合在连线/保存时即时标注。

## 8. 数据、类型与公共接口

- `MediaRecipe` 是 Resource；`MediaRecipeRevision` 是专用 ResourceRevision；草稿保存新的内容 ArtifactVersion。
- 修订内容含 `operator_graph`、`public_input_schema_refs[]`、`public_output_schema_refs[]`、`parameter_schema`、`capability_requirements[]`。
- 主画布调用使用 `MediaRecipeInvoke(media_recipe_revision_id, typed_inputs, config)`。
- provider 记录严格使用主表 8.4 的 CapabilitySnapshotRef、ProviderCompilationReport、ProviderInvocationAttempt、OutboxEvent、ProviderInvocationRecord 和 ProviderOutputBinding。一个 ProviderInvocationAttempt 最多一条 ProviderInvocationRecord；一条 ProviderInvocationRecord 可通过一个或多个 ProviderOutputBinding 关联多个算子输出 ArtifactVersion，只有真实多次外部请求才创建多组 Attempt/Record。

## 9. 状态机与业务规则

- Recipe revision 使用 RevisionStatus；Lab 试跑使用 RunStatus/NodeRunStatus；未来 listing 使用 ListingStatus。
- 图必须无环且深度、算子数、并发、重试、时长、显存/成本有硬上限。
- 相同 revision 与输入 fingerprint 可命中显式缓存；缓存命中仍生成可审计引用记录。
- 提交和运行均固定依赖，禁止读取 latest 算子、模型或资源。
- ProviderInvocationAttempt 直接复用公共 AttemptStatus；unknown 在对账前不是可重试终态，只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord；晚到或过期 execution epoch 的 attempt 使用 superseded 且不能发布算子结果。

## 10. 失败、降级与恢复

- 编译失败定位算子、端口、capability 或预算；不得创建半运行计划。
- provider 异步任务由 WF-006 从 ProviderInvocationAttempt、dispatch OutboxEvent 与 task binding 恢复；unknown 只对账，重复回调与晚到 attempt 受 fencing 保护。
- 可降级控制必须在运行前及结果页展示；blocking control 不可自动改成忽略。
- Lab 崩溃后恢复草稿与运行；已完成算子依据 checkpoint/缓存避免重复付费。
- fallback 重新快照、编译、授权和估算并创建新 attempt/outbox；两次真实调用不得合并成一条 Record，一次多输出调用不得拆成多条 Record。

## 11. 安全、隐私、内容与授权

- Recipe 包不含 secret、CredentialBinding 或未授权模型/素材。
- 算子只能访问显式连线输入，provider 调用经过 TF-OPS-001 和 TF-SEC-001 Gate。
- 任意代码、文件系统、系统命令和任意网络算子在 V1 注册与编译两层阻断。
- 试跑媒体按项目私有，除非用户另行发布，不自动作为公共示例。

## 12. 观测与运营

- 指标包括编译成功率、算子失败率、provider 降级率、缓存命中、端到端时长和估算/实际成本偏差。
- trace 关联 recipe revision、compiled plan、operator/NodeRunAttempt、ProviderInvocationAttempt、dispatch/result OutboxEvent、capability snapshot、InvocationRecord/OutputBinding 和 artifact lineage。
- 运营可暂停有安全问题的算子/provider 组合，并明确影响的新运行范围。

## 13. 验收标准

- AC-1：Given 一个合法多参考配方，When 试跑并提交，Then 主画布可用单节点固定 revision 生成 typed 媒体输出。
- AC-2：Given 配方包含 Agent/Workflow/Recipe/Human Gate/任意代码，When 编译，Then 在运行前阻断并定位算子。
- AC-3：Given provider 不支持 required 控制，When 编译，Then 按 policy 阻断或明确降级，报告与实际调用一致。
- AC-4：Given provider 重复回调和 worker 重启，When 恢复，Then 只接受当前 attempt，已完成算子不重复计费。
- AC-5：Given revision B 已激活，When 重放 revision A，Then 使用 A 的图、算子和 capability 条件且 lineage 可复核。
- AC-6：Given 单个 provider 算子请求返回三个输出，When 当前 epoch 发布，Then 一个 ProviderInvocationAttempt、最多一条 InvocationRecord、三个 OutputBinding/ArtifactVersion、一笔实际成本和一个同事务 result_publish OutboxEvent。
- AC-7：Given provider 已接收请求但响应丢失，When 重启恢复，Then attempt 为 unknown 并只对账；没有盲目重提、重复输出或重复计费。
- AC-8：Given 结果事务故障或旧 epoch 晚到，When 发布算子结果，Then Artifact/Record/OutputBinding/成本/result_publish OutboxEvent 全部不提交；fallback 若执行则具有新快照、编译、授权、估算和新 attempt/outbox。

## 14. 测试场景

- 正常：创建、连线、试跑、提交、主画布调用、逐算子 trace 和修订 diff。
- 边界：最大算子数、复杂 typed I/O、大媒体、缓存命中、费用上限临界点。
- 失败：图环、端口不兼容、provider 超时、控制不支持、坏输出和存储失败。
- 权限：跨 owner recipe、无权模型/素材、撤权后重跑、secret 包扫描。
- 并发/恢复：草稿 CAS、重复回调、lease 过期、单算子重试、刷新和服务重启。

## 15. 交付与回退

- 先开放内部算子与官方 recipe，再按 allowlist 开放用户编辑；任意代码始终关闭。
- 可停用新 recipe 编译或特定算子，既有修订及历史运行保持只读和可审计。
- 发布证据包括 typed graph contract tests、真实 provider E2E、降级一致性、成本和恢复报告。

## 16. 已决策事项与开放问题

- 已决策：底层媒体图只在 Media Recipe Lab，主画布以固定 revision 单节点调用。
- 已决策：V1 Recipe 不嵌套 Recipe、Agent 或 Workflow，不含人工等待和任意代码。
- 已决策：独立 Recipe listing 与安装由 TF-COM-007 在 V1.5 交付。
- 开放问题：无阻塞 V1 Core 的开放问题。
