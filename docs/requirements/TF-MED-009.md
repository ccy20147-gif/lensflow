# TF-MED-009 图片生成、编辑与变体

## 1. 元数据

- ID：TF-MED-009
- 标题：图片生成、编辑与变体
- 状态：defined
- 版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：图片/影视工作区
- 直接依赖：TF-WF-006、TF-OPS-001、TF-OPS-002、TF-OPS-003、TF-OPS-004、TF-SEC-001
- 责任域：媒体平台
- 个人 DRI：待指派

## 2. 背景与问题

图片生成是广告图、视觉资产、故事板和视频关键帧的共同基础。平台必须接入真实 provider，并统一处理文本生成、多参考、局部编辑、异步任务、取消、成本、历史和安全，而不是为每个工作区复制一套调用代码。

provider 能力差异与回调不可靠会造成静默丢参考、重复计费和结果丢失，因此每次调用必须固定能力、输入、编译报告、提交 attempt 和实际记录；候选输出与真实外部请求不得被错误建模为一一对应。

## 3. 目标与非目标

- 目标：提供工作区可复用的 typed 图片生成/编辑接口，V0 即由至少一个真实 provider 完成闭环。
- 目标：支持候选、历史、重试、取消、fallback、实际成本和不可变 ArtifactVersion。
- 目标：通过网络调用前持久化、provider 幂等键和 unknown 对账阻止盲目重提，并支持一次请求返回多个候选。
- 目标：用 TF-QLT-001 固定集评测文本/参考/编辑遵循度、产品与角色身份、图像完整性和交互回归。
- 非目标：本需求不决定资产选择、广告版式或 ShotSpec 业务语义。
- 非目标：不提供任意代码、未审核模型上传或“保证不变脸”承诺。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V0 | 一个真实 provider、文生图、受控参考/编辑、候选历史、重试取消、成本与 Blob | 使用公共 ArtifactVersion、ProviderInvocationAttempt/Record/OutputBinding；V1 原样读取 | 三候选、局部编辑、提交崩溃恢复和成本对账 E2E |
| V1 Core | 多 provider、多参考/蒙版编辑、批量变体、capability/fallback 与完整控制编译 | V0 请求/结果保持可读；新增字段可选且按 schema version | 多参考、fallback、权限撤回和质量回归 E2E |

## 4. 用户与权限

- V1 私有项目只有项目 owner 可在授权、预算和内容策略允许范围触发生成/编辑；共享编辑/审阅/只读角色后置到 TF-TEAM-001。
- ProviderCredential 由 owner/tenant 绑定，工作流、前端、Artifact 和日志均不得读取明文。
- 同 owner 固定 ArtifactRef 可直接作为参考；跨 owner 参考必须先提升为 ResourceRevision，再以固定 revision、grant snapshot 使用，并在每次新生成/导出重算 EntitlementDecision。
- 运营人员只通过受控支持工具查看指纹、状态和安全脱敏信息，不默认查看私有图片或提示。

## 5. 用户场景与主流程

1. 上游工作区提交 typed request：提示、尺寸、候选数、固定参考、ControlLayer、选择策略和预算。
2. 系统校验 schema、授权、安全、文件、预算并按 policy 选择 provider/model capability snapshot。
3. 控制经编译后形成不可变请求指纹，预留成本，并在任何网络调用前于同一事务原子持久化 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent。
4. dispatcher 使用稳定 provider idempotency key 提交；响应不确定时将 attempt 置为 unknown 并查询/对账，禁止直接重提。
5. 回调/轮询结果经幂等校验、内容扫描和 Blob 持久化后，以 execution epoch/fencing token 条件验证当前 Attempt，并在同一事务为每个输出写入 ArtifactVersion/ProviderOutputBinding、最多一条 ProviderInvocationRecord、总用量/实际成本与 `purpose=result_publish` 的 OutboxEvent。
6. 用户比较候选，选择、局部编辑或创建变体；每次新动作产生新的业务请求/Artifact，不覆盖原图。
7. 取消、失败、刷新或服务重启后从持久 Run/attempt/event/outbox 恢复，历史始终可追踪。

## 6. 功能需求

- FR-1：typed 请求支持 text-to-image、image/reference-to-image、masked edit 和 variation 四类 operation。
- FR-2：请求固定 prompt 内容、negative constraints、尺寸/画幅、候选数、seed 可选值和 ordered_reference_refs；跨 owner prompt/知识内容同样必须先提升为 ResourceRevision。
- FR-3：参考必须携带 role；同 owner 可使用固定 ArtifactRef，跨 owner 只接受带 GrantSnapshot 的固定 ResourceRef。V1 Core 支持多参考并由 capability snapshot 限制数量、格式、大小和权重。
- FR-4：蒙版编辑保存 source image、mask、区域语义、边缘/羽化参数和保护区域，不以临时 canvas 为真相。
- FR-5：每个外部请求在网络副作用前固定 ProviderCompilationReport、request_fingerprint 和 provider idempotency key，并在同一数据库事务提交 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent；dispatcher 只可在事务提交后发送。
- FR-6：一个结果图片对应一个 ArtifactVersion 和 ProviderOutputBinding，含 content URI、媒体元数据、内容安全结果、lineage 和校验和；同一请求的多个结果共享 InvocationRecord。
- FR-7：支持 1 至 8 候选的受控批次；provider 原生多输出使用一条调用记录与多个 OutputBinding，超出 provider 上限时只有真实分批提交才产生多条调用记录，并由编译器明确报告。
- FR-8：取消通过运行时 fencing 生效；晚到结果隔离审计，不能自动加入已取消批次或重复计费。
- FR-9：fallback 由 policy 触发，并从原始固定输入重新获取 CapabilitySnapshot、重新编译参考/控制、重新验证授权和重新估算成本；只有确认需要新外部请求时才创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，原提交 unknown 时必须先对账。
- FR-10：结果选择由上游工作台管理；本服务只保存候选与状态，不自行修改 Character/Shot/Ad ResourceDraft。
- FR-11：真实 provider 健康、速率限制、异步回调签名、重试和幂等遵循 TF-OPS-001、TF-OPS-004 与 TF-WF-006 合同。
- FR-12：TF-QLT-001 按 operation/provider/model/version 保存固定输入、自动指标、人工 rubric 和回归容差。
- FR-13：provider 提交必须使用稳定 idempotency key（provider 支持时传递）；发送后状态不确定只允许查询、回调或账单对账，禁止盲目重试生成请求。
- FR-14：ProviderInvocationAttempt 的 attempt_status 必须复用关联 NodeRunAttempt 的公共 AttemptStatus；provider task 的 submitted/queued/processing 等阶段只作为 task binding 事件或明细，不建立任何 provider 专用公共状态枚举。
- FR-15：结果发布必须以 execution epoch/fencing token 条件更新，并在一个事务内写入输出 ArtifactVersion、每 Attempt 最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得发布部分图片或部分计费。

## 7. 交互与展示

- 调用前显示 provider/模型、尺寸、候选数、预计成本、参考支持和所有降级/阻断。
- 候选网格稳定呈现生成中、完成、失败、受限和已取消状态；失败占位不得伪装图片。
- 图片详情展示来源、参考顺序、模型版本、seed、成本、安全/质量状态与 lineage；敏感字段脱敏。
- 编辑器用画笔/擦除图标、蒙版叠加、前后对比和撤销/重做；服务端保存的 mask 才是执行输入。
- 移动端支持查看、候选选择和有限重试，不承诺精细蒙版编辑。

## 8. 数据、类型与公共接口

- `ImageGenerationRequest` Artifact 内容含 operation、prompt_ref、negative_constraints、output_spec、candidate_count、seed、ordered_references、control_layers、provider_policy_ref。
- `ImageReferenceBinding` 含 ArtifactRef/ResourceRef、role、order、strength、region_ref 可选值和 grant_snapshot_id；ArtifactRef 仅限同 owner_scope，跨 owner 必须为 ResourceRef。
- `ImageEditSpec` 含 source_image_ref、mask_artifact_ref、protected_regions、feather_px 和 edit_intent。
- `ImageMediaMetadata` 含 mime、width、height、color_space、alpha、bytes、checksum 和 moderation_ref。
- 输出沿用 ArtifactVersion；ProviderInvocationAttempt 记录 prepared_request_ref、idempotency key、公共 attempt_status 与 reconciliation_state，OutboxEvent 区分 provider_dispatch/result_publish，ProviderInvocationRecord 固定 capability snapshot、compilation report、ordered refs、output_bindings、usage/cost。一个 ProviderInvocationAttempt 最多一条 ProviderInvocationRecord，一条 ProviderInvocationRecord 可有一个或多个 ProviderOutputBinding。
- 文件/Blob、签名访问与生命周期由 TF-OPS-003 管理，不把二进制塞入数据库 JSON。

## 9. 状态机与业务规则

- 每个生成请求 NodeRun 随公共状态；候选输出使用 OutputBinding/选择状态，不为 provider 原生多输出伪造多个 NodeRun。ProviderInvocationAttempt 直接复用关联 NodeRunAttempt 的 AttemptStatus，provider 内部处理阶段不是公共状态。
- invocation_attempt_id、provider_request_id、request_fingerprint 和 attempt epoch 共同防止重复回调、晚到 worker、重复 OutputBinding 和重复记账。
- 相同幂等键重复 dispatch 返回/查询既有任务；变更任一生成字段必须产生新指纹和新的业务调用。unknown 不等于可重试失败，只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord。
- 内容扫描未完成前结果不可被选中/导出；拒绝结果保留受限审计但不向普通用户暴露内容。
- 修改或变体永远创建新 ArtifactVersion，原始输入与候选不可原地覆盖。

## 10. 失败、降级与恢复

- provider 明确拒绝且确认未接收的限流请求可按 Retry-After 与退避重试；提交结果不确定进入 unknown 对账，超过预算/截止时间则安全失败或人工处置，不无限轮询、不盲重提。
- 多参考、蒙版或尺寸不受支持时严格执行 blocked/degraded/ignored_with_warning，并展示具体影响。
- 上传/结果 Blob 校验失败时不创建成功 Artifact；已有 provider_output_id 时可重新拉取同一输出。需要重新生成单候选时必须由用户/策略创建显式新业务请求和新 attempt，不能复用或盲重放原提交。
- 回调丢失时由受控轮询/对账恢复；重复回调幂等，服务重启不丢 provider task binding。
- fallback 后旧 attempt/record 保留，新的 capability、成本和控制报告独立；不得把两次真实调用合并成一条成功记录，也不得把一次多输出调用拆成多条记录。
- fencing 条件失败的晚到结果只进入隔离审计，不写 ArtifactVersion、InvocationRecord、OutputBinding、实际成本或 result_publish OutboxEvent。

## 11. 安全、隐私、内容与授权

- 所有输入/输出执行 TF-SEC-001 Gate，覆盖真人肖像、未成年人、冒充、裸露暴力、无权品牌/素材和政策披露。
- 跨 owner 裸 ArtifactRef 在 provider 披露前拒绝；只有已提升 ResourceRevision、GrantSnapshot 与当前 entitlement 能授权跨 owner 参考。
- 真人编辑需要同意及允许用途；撤回后阻断新生成/编辑/导出并触发敏感派生数据清理。
- provider 披露遵守最小必要原则；私有参考、提示和签名 URL 不进入普通日志或错误消息。
- 回调验证签名、防重放和租户绑定；用户提供 URL 需防 SSRF，并优先经受控上传转存。

## 12. 观测与运营

- 事件：image_request_compiled/prepared/dispatched/submitted/unknown/reconciled/completed/failed/cancelled、image_result_scanned、image_fallback_started、image_cost_recorded。
- 指标：成功率、P50/P95 时延、每调用/每候选成本、unknown 数与对账时长、取消生效率、重复回调/OutputBinding 数、fallback 率、内容拒绝率和 Blob 失败率。
- 质量看板按 TF-QLT-001 的 provider/model/version/operation 分层，禁止用跨模型混合均值掩盖回归。
- 支持链路含 run/node/attempt、request fingerprint、provider request、capability/report、Blob checksum 和 correlation_id。

## 13. 验收标准

- AC-1：Given V0 合法请求，When 一个真实 provider 请求原生返回三候选，Then 一个 ProviderInvocationAttempt、最多一条 ProviderInvocationRecord、三个 ProviderOutputBinding、三个 ArtifactVersion、一次实际成本和一个同事务 result_publish OutboxEvent，且可刷新历史完整。
- AC-2：Given 固定源图与蒙版，When 局部编辑，Then 保护区域像素差异按 TF-QLT-001 指标在批准容差内且 lineage 完整。
- AC-3：Given provider 无法应用 required 参考，When 编译，Then outcome 为 blocked；允许 degraded 后报告具体转换并需确认。
- AC-4：Given provider 已接收请求但响应丢失、回调重复三次并服务重启，When 对账完成，Then unknown attempt 收敛且只生成一组 OutputBinding/结果和一笔实际计费，没有盲目重提。
- AC-5：Given 真人同意撤回，When 请求新变体或导出，Then 服务端阻断且不向 provider 发送素材。
- AC-6：Given TF-QLT-001 固定图片集，When 发布候选模型，Then 各 rubric 达批准阈值且相对基线无超容差回归。
- AC-7：Given 结果事务中注入故障或 execution epoch 已过期，When 发布候选，Then Artifact/Record/OutputBinding/成本/result_publish OutboxEvent 全部不产生；恢复后当前 attempt 只发布一组完整结果。

## 14. 测试场景

- 正常：文生图、单/多参考、蒙版编辑、变体、1/8 候选、选择、取消、fallback 和成本展示。
- 边界：透明图、极端画幅、最大文件/分辨率、重复引用、同图多 role、provider 候选上限。
- 失败：超时、限流、损坏 Blob、回调丢失/重复/伪造、预算不足、内容拒绝、能力变化。
- 权限：跨租户/跨 owner 裸 ArtifactRef、撤权重跑、非 owner 触发、私有 URL 泄漏、无同意真人和未成年人。
- 并发/恢复：重复幂等请求、取消与回调竞态、worker epoch 过期、刷新、断网和服务重启。

## 15. 交付与回退

- provider、operation、多参考、编辑和 fallback 分别受功能开关控制；紧急关闭调用不影响历史结果读取。
- V0 schema 可由 V1 reader 原样读取；新 reference role/control 字段可选或使用新 schema version。
- 发布证据包括真实 provider、三候选/编辑、TF-QLT-001、成本对账、内容 Gate、回调与恢复测试。
- 回退 adapter 时固定旧 capability/InvocationRecord；未完成任务可取消或由兼容 worker 收尾，不能盲重提。

## 16. 已决策事项与开放问题

- 已决策：V0 即接真实图片 provider，图片结果是不可变 ArtifactVersion，业务选择由上游工作台完成。
- 已决策：参考与编辑能力以实际 capability/编译报告为准，不能静默丢失。
- 已决策：provider 网络调用前先持久化 attempt/outbox，unknown 只对账不盲重提；一次调用可通过多个 OutputBinding 产生多个候选。跨 owner 图片/控制内容必须经 ResourceRevision。
- 开放问题：Foundation spike 后冻结首个 provider、单图大小/分辨率硬上限和 operation 默认超时。
