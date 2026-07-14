# TF-MED-010 视频生成与镜头控制

## 1. 元数据

- ID：TF-MED-010
- 标题：视频生成与镜头控制
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：影视工作区
- 直接依赖：TF-MED-003、TF-MED-006、TF-MED-008、TF-MED-009、TF-WF-006、TF-OPS-001、TF-OPS-002、TF-OPS-003、TF-OPS-004
- 责任域：媒体平台/影视产品
- 个人 DRI：待指派

## 2. 背景与问题

视频 provider 的时长、关键帧、相机运动、参考视频和异步协议差异更大，且调用昂贵、耗时长。平台需要把 ShotSpec 的首尾帧、中间关键帧、轨迹、表演和身份锚点编译成真实 provider 能力范围内的请求，并支持单镜替换与可靠恢复。

只返回一个 URL 无法满足版本、成本、控制降级、内容安全和 51 镜头追踪要求。

## 3. 目标与非目标

- 目标：V1 Core 接入至少一个真实视频 provider，完成单镜生成、候选、审查、替换和时间线交付。
- 目标：支持能力范围内的首帧、尾帧、中间关键帧、相机/构图约束、运动/视频参考、source-video modify、风格参考和负面约束。
- 目标：以 TF-QLT-001 评测运动遵循、身份、时序稳定、闪烁、构图和连续性。
- 非目标：不保证所有 provider 支持所有控制，也不提供无限长视频或浏览器端长片编码。
- 非目标：V1 不是长剧集量产调度；范围为有界 51 镜头项目和逐镜片段。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可在授权、预算和安全策略内编译、生成、取消、重试、提交创作 Gate 并选择镜头片段；共享编辑/审阅/只读角色后置到 TF-TEAM-001。
- 平台受控审核员仅处理获分配的安全/合规 Gate，不能替项目 owner 选择创作候选。
- provider credential、实际 payload 和私有参考只对运行时最小权限服务可见。
- 真人影像、声音、未成年人、冒充、版权视频与动作参考进入 TF-SEC-001 Gate。

## 5. 用户场景与主流程

1. 用户从镜头工作台选择固定 ShotSpecRevision 和已审查的首帧/关键帧/身份锚点；source-video 模式还必须选择固定源视频、裁切范围和 reference/modify 操作。
2. 系统验证 duration、画幅、关键帧时序、控制层、授权、安全和预算，选择 capability snapshot。
3. TF-MED-006 生成 ProviderCompilationReport；用户处理 blocked 项或确认允许的降级。
4. 系统预留成本，并在网络调用前于同一数据库事务原子持久化 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent；dispatcher 使用稳定 provider idempotency key 提交异步任务。
5. 提交结果不确定时进入 unknown 并查询/对账；完成后以 execution epoch/fencing token 条件验证 Attempt，并在同一事务写入一个或多个原始片段 ArtifactVersion、最多一条 InvocationRecord、多条 OutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent。
6. 用户选择候选写入 ShotSpec 草稿，并可在时间线单镜替换；其他镜头和历史片段不变。

## 6. 功能需求

- FR-1：请求固定 ShotSpecRevision、duration_ms、输出规格、ShotTemporalAnchorRef/keyframes、ControlLayer、身份锚点、provider policy 与预算。
- FR-2：关键帧支持 first、last 和有限 intermediate；time 必须单调且落在片段时长内，数量由 capability snapshot 验证。
- FR-3：相机控制支持静态参数、translation/orientation/optical、CameraFramingConstraint、timing/speed profile 与 motion quality；无法原生支持时只可逐 fragment 进入 blocked、transformed/degraded 或 ignored_with_warning。
- FR-4：运动参考、source video 与其他视频参考固定版本、时间裁切、operation、semantic roles、顺序、强度和授权，不以外部可变 URL 执行；同 owner 可使用 ArtifactRef，跨 owner 必须先提升为 ResourceRevision 并使用带 GrantSnapshot 的 ResourceRef。
- FR-5：每个候选保存独立视频 ArtifactVersion、ProviderOutputBinding 与 poster/preview ArtifactRef；一个外部请求的多个候选共享 ProviderInvocationRecord、实际用量与成本，只有真实多次请求才有多条记录。
- FR-6：媒体元数据至少含 codec、container、width、height、fps、duration_ms、audio_presence、checksum 和 moderation_ref。
- FR-7：异步 adapter 必须在网络前原子提交 NodeRunAttempt、ProviderInvocationAttempt 与 provider_dispatch OutboxEvent，并支持幂等提交、轮询/签名回调、取消、限流退避、unknown 对账和 provider task binding。
- FR-8：生成后运行内容安全、损坏/黑帧检测、身份一致性和 TF-QLT-001 视频质量评测，未完成前不可选中。
- FR-9：候选选择只更新 ShotSpec ResourceDraft.selected_output_refs；冻结产生新 Revision，不覆盖旧片段。
- FR-10：单镜重跑/替换不得触发其他 50 镜头重跑；时间线按 shot_id/selected ref 检测 stale。
- FR-11：支持有界批量提交 51 镜头，但并发由预算、provider 配额和运行策略控制，逐镜状态/成本独立。
- FR-12：fallback 必须从原始固定输入重新获取 CapabilitySnapshot、重新编译关键帧/轨迹/参考、重新验证授权并重新估算成本；仅在确认需要新外部请求时创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，结果 Record 保留 fallback_from，原 attempt/record 不覆盖。
- FR-13：提交必须使用稳定 provider idempotency key（provider 支持时传递）；发送后无法确认是否接收时进入 unknown，只允许查询/回调/账单对账，禁止盲目重提或直接 fallback。
- FR-14：ProviderInvocationAttempt 的 attempt_status 必须复用关联 NodeRunAttempt 的公共 AttemptStatus；provider task 内部 submitted/queued/processing/downloading/postprocessing/scanning 只作为事件或绑定明细，不建立任何 provider 专用公共状态枚举。
- FR-15：结果发布必须以 execution epoch/fencing token 条件更新，并在一个事务内写入视频 ArtifactVersion、每 Attempt 最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent；事务失败不得留下部分媒体、绑定或计费。
- FR-16：source-video `operation=reference|modify` 时必须引用固定 SourceVideoControlSpec；Spec 固定 source/target time range 与 retime policy，motion、structure、body_pose、face_performance、mask_region、style 和 appearance_edit fragment 分别声明 preserve|transfer|replace|relax、normalized strength、required/policy 及可选 VideoControlMaskManifest/tracking/prompt ref。VideoReferenceBinding 不复制这些强度。Provider 只有单一 strength 时必须显示合并转换与语义损失；clip extend、智能 reframe/outpaint、多轮会话式编辑和跨片段连接后置 V1.5。
- FR-17：V1 Core 的 source-video modify 只有在至少一个真实 Provider 完成 capability、编译、调用、异步恢复、控制遵循和安全 E2E 后才能启用；未通过时功能保持关闭并标为 compile-only，不得用 mock 或 prompt-only 近似宣称已交付 modify。

## 7. 交互与展示

- 生成前显示关键帧带、轨迹/构图约束预览、source-video 裁切、reference/modify 操作、语义控制强度、参考顺序、目标时长/分辨率、预计成本和编译报告。
- 状态按排队、provider 处理、下载、后处理、扫描、评测分段显示，不用单一无限进度条伪造精度。
- 候选播放器支持逐帧/倍速、首尾对比、关键帧定位、身份/闪烁证据叠加和静音控制。
- 单镜替换明确展示受影响时间线片段、时长差和下游 stale，不把整项目标成失败。
- 移动端支持播放、比较和 Gate；关键帧/轨迹精细编辑保留桌面端。

## 8. 数据、类型与公共接口

- `VideoGenerationRequest` Artifact 内容含 shot_revision_ref、output_spec、temporal_anchor_refs、control_layers、anchor_set_ref、provider_policy_ref 和 candidate_count。
- `VideoReferenceBinding` 含 reference_ref、binding_role、trim_range、order、可选 reference_strength、可选 source_video_control_spec_ref 和 grant_snapshot_id；operation=modify 时必须引用 SourceVideoControlSpec，分片模式/强度、retime 和 VideoControlMaskManifest 只存在于该 Spec。ArtifactRef 仅限同 owner_scope，跨 owner reference_ref 只能是固定 ResourceRef。
- `VideoMediaMetadata` 含 container、video/audio codec、dimensions、fps、duration、color metadata、bytes、checksum、moderation_ref。
- 输出均为 ArtifactVersion；poster、proxy 和质量报告通过 lineage 指向 master video Artifact。
- ProviderCompilationReport、ProviderInvocationAttempt、OutboxEvent、ProviderInvocationRecord 与 ProviderOutputBinding 沿用第 8.4 节；actual request 的 reference order 和每个 output binding 必须可与 ShotSpec 对账。一个 ProviderInvocationAttempt 最多一条 ProviderInvocationRecord，一条 ProviderInvocationRecord 可通过一个或多个 ProviderOutputBinding 关联多个候选。
- 事件/状态使用 TF-WF-006/TF-OPS-004 的 Run/NodeRun/RunEvent，不创建前端真相状态。

## 9. 状态机与业务规则

- ProviderInvocationAttempt 直接复用关联 NodeRunAttempt 的 AttemptStatus；provider task 可内部细分 submitted/queued/processing/downloading/postprocessing/scanning，但这些只能作为 task binding 事件或明细，不能映射成第二套公共状态或替代 NodeRunStatus。只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord。
- invocation_attempt_id、provider_request_id、attempt_id、lease epoch 和 request_fingerprint 确保 dispatch/回调幂等、OutputBinding 去重与晚到隔离。
- 扫描或质量 Gate 未通过的 Artifact 保持受限，不得进入 selected_output_refs 或成片导出。
- 修改 ShotSpec、关键帧、身份锚点、能力快照或策略必须新编译/新调用，不复用旧成功结果冒充。
- 时长变化时只将引用该片段的 timeline draft 标 stale；历史 Timeline/CreativeWork Revision 不变。

## 10. 失败、降级与恢复

- provider 明确拒绝且确认未接收的限流请求可按版本化策略退避重试；提交超时或结果不确定进入 unknown 对账，达到截止时间后可继续对账、停止采用或人工处置，不能盲目重提/fallback。
- 关键帧/轨迹/参考不受支持时按各 ControlLayer policy 报告；required 安全/身份控制不可 ignore。
- 下载中断支持受控续传与 checksum；损坏、零时长或黑帧片段不标 completed。
- 服务重启从 ProviderInvocationAttempt、dispatch outbox、provider task binding 和 RunEvent 恢复；重复回调不得重复 OutputBinding、Blob、Artifact 或实际成本。
- 取消后晚到结果隔离；若 provider 无取消能力，明确“停止采用但可能产生费用”并最终对账。
- execution epoch/fencing 条件失败时，晚到结果不得写 ArtifactVersion、InvocationRecord、OutputBinding、实际成本或 result_publish OutboxEvent。

## 11. 安全、隐私、内容与授权

- 输入关键帧、参考视频和输出逐项执行权利/内容 Gate；跨 owner 裸 ArtifactRef 禁止，必须先提升为 ResourceRevision，并具备当前 entitlement 和历史 grant snapshot。
- 真人数字替身、换脸、声音联动、未成年人或冒充请求需同意证据、风险披露和人工/策略阻断。
- provider 披露最小化；私有视频、脸部裁剪、提示和签名 URL 不进入普通日志。
- 撤回同意或素材权利后阻断新生成/重跑/导出，按策略清理敏感派生数据并保留隔离审计。

## 12. 观测与运营

- 事件：video_compile_completed、video_prepared/dispatched/submitted/unknown/reconciled/progress/completed/failed/cancelled、video_scanned/reviewed/selected、fallback_started。
- 指标：成功率、排队/生成/下载 P50/P95、每秒视频成本、unknown 数与对账时长、取消费用、callback/output duplication、黑帧/损坏率、fallback 率。
- 质量按 TF-QLT-001 的运动、身份、闪烁、构图、连续性分项及 provider/model/version 记录。
- 支持信息含 shot/run/attempt、provider task、capability/report、视频 checksum、成本和 correlation_id。

## 13. 验收标准

- AC-1：Given 固定 ShotSpec 和真实 provider，When 单个请求生成两个 5 秒候选，Then 一个 ProviderInvocationAttempt、最多一条 InvocationRecord、两个视频 Artifact/OutputBinding 及各自 proxy/poster、一次实际成本和一个同事务 result_publish OutboxEvent。
- AC-2：Given 首尾帧及两中间关键帧，When provider 仅支持首尾，Then 按策略阻断或报告中间帧降级，绝不显示全部 applied。
- AC-3：Given 51 镜头项目中第 17 镜重跑，When 选择新片段，Then 仅该 Shot 草稿和相应 timeline draft stale，其他 50 镜不变。
- AC-4：Given provider 已接收请求但响应丢失、回调重复且服务重启，When unknown 对账收敛，Then 不盲目重提，只保存该真实请求的一组 OutputBinding、一笔实际费用和一条有效完成转换。
- AC-5：Given 真人同意撤回，When 请求 fallback 或导出，Then 服务端阻断且不向新 provider 披露参考。
- AC-6：Given TF-QLT-001 视频固定集，When 回归，Then 运动/身份/闪烁/控制遵循达到批准阈值和容差。
- AC-7：Given 结果事务中注入故障或 execution epoch 已过期，When 发布视频候选，Then Artifact/Record/OutputBinding/成本/result_publish OutboxEvent 全部不产生；当前 attempt 恢复后只形成一组完整结果。
- AC-8：Given SourceVideoControlSpec 以 preserve/relax/preserve/transfer 控制 camera motion、structure、body pose 和 face performance，并带 retime/时空 mask，而 Provider 只有单一 modify strength，When 编译并生成，Then每个语义 fragment、retime 和 mask 均有唯一 outcome/application_mode，合并损失经确认且 InvocationRecord 固定实际参数。
- AC-9：Given source-video modify 功能开关待启用，When 对至少一个真实 Provider 完成固定视频 E2E，Then输出真实调用记录、异步恢复、控制遵循/安全报告和单镜替换证据；任一门失败时开关保持关闭且 UI 标为不可用而非已交付。

## 14. 测试场景

- 正常：文/图生视频、首尾/中间关键帧、轨迹/构图约束、运动参考、source-video reference/modify、候选、审查、选择和时间线替换。
- 边界：provider 最短/最长时长、最大关键帧、纵向画幅、无音频、51 镜头有界批量、极低运动。
- 失败：限流、超时、回调丢失/重复、下载损坏、黑帧、内容拒绝、fallback 能力更低。
- 权限：跨项目视频、跨 owner 裸 ArtifactRef、撤权重跑、无同意真人、未成年人、非 owner 高成本调用、私有 URL。
- 并发/恢复：取消与完成竞态、重复幂等请求、worker epoch 过期、刷新、断网和服务重启。

## 15. 交付与回退

- provider、关键帧、运动参考、source-video modify、批量和 fallback 独立功能开关；关闭新调用不影响历史片段播放。
- adapter/capability/request schema 版本化；旧 Artifact/InvocationRecord 永久按原版本解释。
- 发布证据包括真实 provider、关键帧降级、51 镜单镜替换、TF-QLT-001、安全、成本与恢复 E2E。
- 回退 adapter 时未完成任务由兼容 worker 收尾或安全取消，不自动重提到其他 provider。

## 16. 已决策事项与开放问题

- 已决策：V1 Core 必须有真实视频 provider 和镜头控制；能力不足通过编译报告显式处理。
- 已决策：V1 Core 的 source video 范围为有界 reference/modify；extend、智能 reframe/outpaint、多轮视频编辑和跨片段连接后置 V1.5。
- 已决策：视频是逐镜不可变 Artifact，单镜替换不重写其他镜头或历史时间线。
- 已决策：provider 网络调用前先持久化 attempt/outbox，unknown 只对账不盲重提；一次请求可通过多个 OutputBinding 产生多个候选，跨 owner 控制内容必须经 ResourceRevision。
- 开放问题：provider spike 后冻结默认片段时长、候选数、轮询截止时间和并发策略。
