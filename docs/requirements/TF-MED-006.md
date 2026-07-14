# TF-MED-006 ShotSpec、控制层与 Provider 编译

## 1. 元数据

- ID：TF-MED-006
- 标题：ShotSpec、控制层与 Provider 编译
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：分镜控制/平台内核
- 直接依赖：TF-MED-003、TF-MR-001、TF-OPS-001
- 责任域：媒体平台/分镜产品
- 个人 DRI：待指派

## 2. 背景与问题

不同图片/视频 provider 对 pose、深度、关键帧、相机轨迹、多参考和负面约束的支持差异巨大。若平台把 ShotSpec 直接拼成提示词，不支持的控制会被静默丢弃，用户无法判断输出为何偏离。

本需求建立从固定 ShotSpecRevision 到具体 provider 请求的确定性编译层，以公共 ControlLayer、CapabilitySnapshotRef 和 ProviderCompilationReport 解释每一项控制的应用、转换、降级、忽略或阻断。

## 3. 目标与非目标

- 目标：同一 ShotSpec 可面向不同 provider 编译，并得到可比较、可重放的不可变报告。
- 目标：支持主表第 8.3 节所有 V1 ControlLayer 类型、Beat/Keyframe 时域和冲突诊断。
- 目标：以 TF-QLT-001 固定样本验证控制遵循度、编译一致性和 provider 间回归。
- 非目标：不承诺所有 provider 支持所有控制，也不以提示词文本伪装结构化能力。
- 非目标：Media Recipe 不能嵌套 Agent/Workflow/Recipe、Human Gate 或任意代码。

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可选择 provider policy、调整非安全控制并确认允许的降级/忽略。
- 非 owner 不获得项目报告读取、策略修改或调用能力；平台管理员只维护能力快照/转换器，平台审核员按审核授权读取必要证据。
- 平台管理员维护 provider capability snapshot 和已审核转换器；项目用户不能伪造能力声明。
- 编译与调用分别重算资源授权、provider entitlement、预算和 TF-SEC-001 安全策略。

## 5. 用户场景与主流程

1. 用户提交固定 ShotSpecRevision、目标输出类型和 ProviderSelectionPolicyRef；来自工作台的请求以 requested_input_refs 携带固定输入、以 DraftInputSelector 声明需从 snapshot 冻结的 Draft，并携带 expected versions，必须先由运行时冻结所需 Revision 并生成 InputFreezeManifest。
2. 编译器解析 beats、control_layers、generation_policy、资源/授权和目标 capability snapshot。
3. 编译器验证类型、来源选择器、源时域、坐标/投影、像素对齐、冲突和 provider 上限，对每个可独立裁决的控制片段形成 applied/transformed/degraded/ignored_with_warning/blocked 唯一结果。
4. 工作台展示 ProviderCompilationReport；blocked items 必须修复，其他语义损失按策略确认。
5. 系统冻结编译输入指纹，并在任何网络调用前同一事务持久化 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 outbox；实际完成记录固定报告、能力快照、参考顺序和全部 OutputBinding。
6. unknown submission 只进入 reconciliation；fallback 时从原始 ShotSpec 对新 provider 完整重编译、重估成本并创建新 Attempt，禁止盲重试或复用旧请求。

## 6. 功能需求

- FR-1：编译输入必须固定 ShotSpecRevision、所有 ControlLayer.source_ref、类型化 ControlSourceSelector、ShotTemporalAnchorRef、ControlFrameManifest、Beat.frame_ref、DirectorSceneRevision、DirectorSceneControlExport ArtifactVersion、SourceVideoControlSpec、recipe revision、provider policy 和 capability snapshot；来自工作台时 actual Revision refs 必须全部来自服务端 InputFreezeManifest，不接受客户端伪造的 frozen/actual refs。
- FR-2：V1 支持 storyboard、action board、composition markup、pose、depth、normal、segmentation、edge、clay、mask/region、3D director scene、camera、lighting、首/尾/中间关键帧、motion/performance/style reference、source video 和 negative constraint；ControlLayer 必须保存必填 target_scope，并按控制语义条件要求 target_anchor_ref/source_selector：关键帧/动作/锚点局部控制必须使用 ShotTemporalAnchorRef，多对象/多片段来源必须使用类型化 selector，整 Shot 的单一 Artifact 控制可省略二者。camera/lighting 层只引用固定 DirectorScene 或 export Artifact/item，source-video 层引用固定 SourceVideoControlSpec/fragment，不复制权威参数或 Beat 时间。
- FR-3：逐层按 type schema 验证 identity/version、target_scope、target_anchor_ref、source_selector、coordinate_space、source_time_range、条件 strength、required 和 unsupported_policy；source-video layer 禁止顶层 strength，并验证 Spec 的分片强度。pose/depth/normal/segmentation/edge/clay/mask 还必须验证 ControlFrameManifest 的骨骼 convention、坐标/单位、投影、near/far、标签、画布、裁切、resize policy 及跨层像素对齐。
- FR-4：BeatOrKeyframe 与 ShotTemporalAnchorRef 必须解析到合法 concrete time；shot_start=0、shot_end=duration_ms，shot_default 不解析时间且禁止用于 action/keyframe/CameraTimingBinding。关键帧顺序、范围和 provider 数量上限必须验证。
- FR-5：`unsupported_policy=block` 遇不支持或不安全控制时为对应 item 写唯一 outcome=`blocked`、增加 `summary_counts.blocked` 并禁止调用；公共合同不另建平行阻断错误数组。
- FR-6：`unsupported_policy=degrade` 仅在存在已版本化转换器及语义损失说明时允许，并形成 `transformed` 或 `degraded` 唯一 outcome；无合法转换则升级为 blocked。
- FR-7：`ignore_with_warning` 只允许 `required=false` 的控制，写 ignored_with_warning 并要求用户/策略显式接受。
- FR-8：每个 requested control 及其可独立裁决的 `fragment_path` 必须恰好有一个最终 outcome：applied、transformed、degraded、ignored_with_warning 或 blocked；报告 summary_counts 必须可与 items 完整对账，任何片段缺项均视为编译失败。
- FR-9：控制冲突按版本化规则集检测；不得以数组后项隐式覆盖前项，解决结果需列出胜出规则和受影响层。director_component selector 必须以 DirectorComponentRef[] 逐项固定 scene revision、component_kind、component_id，并在需要时固定 fragment_path/anchor/path_t range；camera、camera_motion、camera_framing_constraint、staging_geometry、exposure、environment_lighting、light_emitter、lighting_role_assignment、light_modifier、actor_instance、static_pose 各用独立 kind/ID 合同，kind-ID 不匹配必须阻断。translation、orientation/roll、framing、focus/focal/aperture、exposure、environment/emitter/role 等片段必须分别裁决，不能以整层 outcome 掩盖局部丢失。
- FR-10：参考素材保持 ordered_reference_refs 和 role；去重不得改变顺序或把同一内容的不同用途合并。
- FR-11：编译输出包含 provider payload Artifact、request_fingerprint、估算成本/用量和报告 ArtifactRef；每个 item 必须记录 `application_mode`（native field、derived control、reference media、prompt approximation、omitted 或 blocked），secret 不进入任何产物。
- FR-12：网络调用前必须在同一数据库事务中持久化 NodeRunAttempt、ProviderInvocationAttempt、`purpose=provider_dispatch` 的 OutboxEvent 和 provider idempotency key；一次 Attempt 可通过多个 OutputBinding 返回多个候选，unknown submission 必须 reconcile；fallback 从原始固定输入重编译并新建 Attempt/outbox，记录 capability、成本、控制差异和 fallback_from。
- FR-13：provider 结果必须以 execution epoch/fencing token 条件更新；同一事务验证 Attempt 可发布、写输出 ArtifactVersion、最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际成本/用量和 `purpose=result_publish` 的 OutboxEvent。unknown/waiting_external 未对账收敛前不得形成 Record，事务失败不得发布部分结果。
- FR-14：TF-QLT-001 保存每类控制固定样本、期望可测结果、人工 rubric、自动指标和容差，编译器版本参与回归键。
- FR-15：Grid composite、独立 cells、有序关键帧和多 reference 是不同 application_mode；Provider 仅支持独立参考时必须按能力上限拆分或阻断/降级，不得把复合宫格静默标记为原生多角度身份控制。
- FR-16：source video 控制必须先保存不可变 SourceVideoControlSpec，固定 operation、source/target time range 与 retime policy；其 motion、structure、body_pose、face_performance、mask_region、style 和 appearance_edit fragment 分别保存 preserve|transfer|replace|relax、normalized strength、required、unsupported_policy 及可选 mask manifest/tracking/prompt ref，并各自形成 ControlCompilationItem。Provider 只有单一 video-to-video strength 时，编译报告必须说明合并转换及语义损失。

## 7. 交互与展示

- 工作台以“已应用、已转换、已降级、带警告忽略、阻断”五组展示，默认先显示影响结果的项目。
- 每项显示原始控制、target scope/anchor、source_selector、fragment_path、application_mode、目标 provider 能力、转换规则、语义损失、修复命令和证据，不暴露凭证或内部敏感 payload。
- provider 比较视图并列显示支持率、阻断数、降级项、估算成本和预计时延，不能只给模糊推荐分。
- 用户确认降级时显示作用范围，可仅本次、该 Shot 草稿或版本化策略；不得全局静默记忆。
- 移动端只读报告并可确认低风险 `ignored_with_warning` 项，复杂冲突编辑回到桌面工作台。

## 8. 数据、类型与公共接口

- 核心沿用公共 `ShotSpecRevision`、`BeatOrKeyframe`、`ShotTemporalAnchorRef`、`ControlLayer`、`ControlSourceSelector`、`DirectorComponentRef`、`DirectorSceneControlExport`、`SourceVideoControlSpec`、`VideoControlMaskManifest`、`ControlFrameManifest`、`InputFreezeManifest`、`ProviderCompilationReport`、`CapabilitySnapshotRef`、`ProviderInvocationAttempt`、`ProviderInvocationRecord` 与 `ProviderOutputBinding`。
- `ControlCompilationItem` 严格含 control_layer_id、可选 source_selector/fragment_path、requested_type、outcome、application_mode、可选 provider_field/transformer_revision_id/semantic_loss、reason_code、evidence_refs；semantic_loss 在 transformed/degraded 或其他发生语义损失的 application_mode 下条件必填，无损 native applied 可省略。
- `CompiledMediaRequest` 为 ArtifactVersion 内容，含 input_fingerprint、provider/model、capability_ref、normalized_controls、payload_ref 和 estimated_usage；payload_ref 必须加密/受限。
- ProviderCompilationReport 本身保存为 ArtifactVersion，其 ref 进入 Attempt/InvocationRecord.compilation_report_ref；OutputBinding 将一个外部响应的多个候选分别绑定到输出 ArtifactVersion。
- ProviderInvocationAttempt 必须关联 node_run_attempt_id 并复用公共 AttemptStatus；provider submitted/queued/processing 仅为 task-binding 事件。一个 Attempt 最多一条 InvocationRecord，一次响应的多个候选共享该 Record，只有多个真实外部请求才创建多组 Attempt/Record。
- 报告中每个 requested control fragment 必须恰好有一个最终 outcome；transformed 可说明转换后的 application_mode 和语义损失，但不得同时占多个主分类。
- 公共类型仅扩展明细，不更改 `unsupported_policy = block | degrade | ignore_with_warning` 或状态族。

## 9. 状态机与业务规则

- 编译 child run 使用公共 RunStatus/NodeRunStatus；pending、validating、compiled、blocked 只作为编译事件 phase/结果，不建立平行运行状态族。只有编译结果可调用且授权/预算仍有效才能 invoke。
- compilation report 对输入指纹不可变；ShotSpec、能力快照、配方或策略任一改变都必须重新编译。
- capability snapshot 过期不修改旧报告；新调用必须按 provider 政策取得当前快照并比较差异。
- 相同 input_fingerprint/compiler_revision/provider capability 的请求可复用编译 Artifact，但调用仍重新鉴权、预算并创建唯一 Attempt/outbox；不得因缓存复用外部提交状态。
- 编译器规则由服务端确定，LLM 或前端只能提出结构化建议，不能决定状态或绕过 outcome=`blocked` item。

## 10. 失败、降级与恢复

- schema 不兼容、引用缺失、时域越界、转换器失败、授权或安全阻断均返回字段级 reason_code 和 correlation_id。
- 任何 requested control 未出现在报告分类中视为编译失败，禁止调用。
- provider 能力在编译后调用前变化时，拒绝旧计划并以新 snapshot 重编译，不尝试盲发。
- fallback 必须从原始固定输入开始；若新 provider 产生更多阻断，保持原失败记录并等待选择。
- 服务重启后从编译 Artifact、Attempt、outbox 与运行状态恢复；AttemptStatus=`unknown` 时只能经 provider 查询、回调、账单或人工对账收敛，相同 client_request_id/provider idempotency key 不重复调用或重复预留成本。

## 11. 安全、隐私、内容与授权

- 编译前和调用前分别执行 EntitlementDecision、内容 Gate、真人/未成年人/冒充策略及最小披露检查。
- provider payload、私有提示、参考 URL 和身份控制图仅按任务范围加密存取，普通日志保存指纹与字段统计。
- `ignore_with_warning` 不得用于安全、授权、同意、未成年人或政策强制控制；这些始终 block。
- 转换器和 Media Recipe 仅使用审批算子，不执行任意代码或外部未登记网络请求。

## 12. 观测与运营

- 事件：media_compile_started/completed/blocked/failed、degradation_confirmed、provider_attempt_persisted/dispatched/reconciling、provider_invoked、fallback_recompiled。
- 指标：按控制类型/provider 的 applied/transformed/degraded/ignored_with_warning/blocked 率、编译时延、fallback 率、报告/实际请求不一致数。
- 报告/InvocationRecord 对账不一致必须为零；发现差异触发告警并停止相关 provider/model 新流量。
- 质量看板引用 TF-QLT-001 样本、compiler/capability/transformer revision、模型版本和回归结果。

## 13. 验收标准

- AC-1：Given 含全部 V1 控制类型且 camera/light 含多片段的 ShotSpec，When 对两个能力不同的 provider 编译，Then 每个 fragment_path 有唯一 outcome/application_mode 且报告可解释差异。
- AC-2：Given required pose 不受支持且策略为 block，When 编译，Then 对应 item outcome=`blocked`、summary_counts.blocked=1 且零 provider 调用，不产生平行阻断错误字段。
- AC-3：Given camera layer 的 path 可降级而 roll/focus 可原生应用，When 编译为首尾机位，Then path fragment 记录 transformed/degraded，roll/focus 分别记录 native applied，且转换器版本、application_mode 和语义损失可审计。
- AC-4：Given optional style reference 策略为 ignore_with_warning，When 调用，Then 警告被审计且 InvocationRecord 固定该报告。
- AC-5：Given 提交响应未知或 fallback 到新 provider，When 恢复/重试，Then 前者只 reconcile 且不重复外部提交，后者创建新能力快照、报告、估算和 Attempt；一次响应的多个候选由 OutputBinding 对账，旧记录不变。
- AC-6：Given TF-QLT-001 固定控制集，When 编译与真实生成回归，Then 控制遵循度达到批准阈值且无静默丢层。
- AC-7：Given provider 重复/晚到回调或旧 execution epoch，When 发布结果，Then fencing 条件只允许一个事务形成最多一条 InvocationRecord、全部 OutputBinding 与 result_publish outbox；失配事务零部分发布。
- AC-8：Given SourceVideoControlSpec 分别以 preserve/relax/preserve/transfer 控制 camera motion、scene structure、body pose 和 face performance，并带时空 mask/retime，但 Provider 只有单一 strength，When 编译，Then四个 fragment、mask 与 retime 均有唯一 outcome/application_mode，合并转换的损失可见且 required 冲突可阻断。

## 14. 测试场景

- 正常：全部控制类型、关键帧、参考顺序、两个 provider 编译、调用和报告对账。
- 边界：零控制、最大允许控制数、重叠源时域、混合坐标、同源多用途、控制稿尺寸/投影不一致、provider 关键帧上限。
- 失败：未知 schema、引用损坏、转换器异常、能力快照变化、provider 拒绝、报告分类缺项。
- 权限：非 owner 调用、无 grant、撤权后重跑、私有控制图泄漏、恶意 payload、平台审核员越界和将安全 Gate 设为 ignore。
- 并发/恢复：重复编译、相同幂等键、调用前策略变化、取消晚到、worker 重启和 fallback 竞态。

## 15. 交付与回退

- 编译器、转换器和 provider adapter 分别版本化并可按模型功能开关；紧急回退默认把未知控制改为 block。
- 新 ControlLayer 类型必须向旧 reader 保留原文；旧编译器遇未知 required 类型必须阻断而非丢弃。
- 发布证据包括 capability matrix、全部控制 contract tests、真实 provider E2E、fallback 对账和 TF-QLT-001 报告。
- 回退不能删除报告或 InvocationRecord；旧编译产物只可审计，不自动用于新调用。

## 16. 已决策事项与开放问题

- 已决策：unsupported_policy 只允许 block、degrade、ignore_with_warning；最终 outcome 统一为 applied/transformed/degraded/ignored_with_warning/blocked，并对每个 fragment_path 给出 application_mode，禁止静默处理。
- 已决策：fallback 是一次新的完整编译和实际调用记录，不是旧请求换 URL。
- 开放问题：Foundation provider spike 后冻结首批 capability matrix 和转换器白名单；未验证转换保持 block。
