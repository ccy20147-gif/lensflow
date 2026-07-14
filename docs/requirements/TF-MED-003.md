# TF-MED-003 镜头级分镜控制工作台

## 1. 元数据

- ID：TF-MED-003
- 标题：镜头级分镜控制工作台
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：影视工作区
- 直接依赖：TF-MED-002、TF-WF-005、TF-WF-008、TF-WF-010
- 责任域：分镜产品
- 个人 DRI：待指派

## 2. 背景与问题

镜头规划输出有序 ShotPlan，但专业创作还需逐镜管理构图、表演、摄影、灯光、关键帧、参考和连续性。把这些字段铺到主画布会造成节点爆炸，把它们分散到互不相干页面又会产生多个真相源。

本工作台不设笼统的“全部镜头真相”。计划草稿拥有顺序、sequence/coverage 与计划 CutRelation；ShotSpecDraft 拥有单镜抽象意图；Beat 拥有镜内时间/表演；可共享的 DirectorSceneDraft 拥有精确空间、姿势、相机路径与灯具参数。`ShotPlanEditSession` 只用 `EditContextSnapshot` 协调这些独立草稿，故事板、动作板、宫格和检查结果均为固定来源的派生产物。

## 3. 目标与非目标

- 目标：在 51 镜头项目内完成快速定位、逐镜精修、批量操作、单镜重跑、版本冻结和恢复。
- 目标：覆盖故事板、动作板、9/16/25 宫格、构图标注、pose/depth/normal/segmentation/edge/clay/mask、3D、相机、灯光、关键帧、source video、运动/表演/风格参考和连续性/身份审查入口。
- 目标：所有 AI/媒体结果按 TF-QLT-001 固定样本、rubric 和回归容差展示质量证据。
- 非目标：不在工作台直接改写 Run/NodeRun，不绕过编译器调用 provider。
- 非目标：不提供完整 DCC、骨骼动画或无限镜头量产；长内容由 TF-LNG-001 处理。

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可创建/修改 ShotSpec 草稿、请求编译运行、接受候选和冻结修订。
- 非 owner 不获得项目内查看、Human Task 决策或生成能力；平台审核员只按审核授权读取必要证据。
- 移动端是同一 owner 的有限操作界面，不产生独立只读角色或共享项目权限。
- 跨 owner 资产、世界或 OC 引用需固定 revision/grant；工作台不能用显示名称替代授权引用。

## 5. 用户场景与主流程

1. 用户从主画布 `WorkbenchTask` 打开固定 ShotPlan 快照，系统创建或载入 `ShotPlanEditSession`、计划草稿、逐镜 ShotSpecDraft、所引用的共享 DirectorSceneDraft 及当前 `EditContextSnapshot`。
2. 用户从镜头条带选择 shot，在属性面板编辑叙事、CompositionIntent、beats、control layers 和 generation policy；顶部持续显示 project/sequence/coverage/shot/temporal-anchor 作用域。
3. 用户在生成结果、故事板、动作板、宫格、3D 导演台和对比六个主视图间切换；所有视图读取同一 `EditContextSnapshot` 的 session_version 和多资源 draft-version 向量，而非假定只有一个 draft_version。
4. 用户请求预编译、生成、控制稿导出或检查时，以 requested_input_refs 提交已固定输入、以 draft_input_selectors 声明需从 EditContextSnapshot 冻结的 ShotSpec/DirectorScene Draft，并提交完整 expected_draft_versions；运行时原子验证版本并冻结所需 Revision，生成 InputFreezeManifest 后才创建可审计 child run，工作台不直接生成 actual/frozen refs 或调用 provider。
5. 用户处理 blocked/degraded/ignored_with_warning，比较输出与 TF-QLT-001/连续性/身份报告，并把选择或修订提案显式回写到字段权威所属的 ResourceDraft。
6. 用户提交 `ShotPlanCommitManifest`；同一数据库事务验证全部 expected plan/shot/director-scene draft versions，为 RevisionCandidate 预分配 ID、按 CandidateRevisionRef 的 expected type/resource 解析所有跨候选引用并验证 Beat 归属，随后同时冻结受影响 ShotSpecRevision/DirectorSceneRevision 和引用它们及计划级关系的新 ShotPlanRevision，输出 CandidateResolutionMap 与固定 ResourceRef。

## 6. 功能需求

- FR-1：载入时固定 base ShotPlanRevision、ordered ShotSpecRevision 及其共享 DirectorSceneRevision，并创建非 canonical ShotPlanEditSession 与 EditContextSnapshot；会话不追随 latest，snapshot 保存 session_version、base_revision_refs 和完整 draft_version_vector。
- FR-2：支持 51 镜头列表的搜索、筛选、分组、虚拟滚动、键盘导航和 stale 定位。
- FR-3：按字段级权威编辑：计划层覆盖 ordered_shot_revision_refs、ShotSequenceGroup、CoverageGroup、CutRelation；ShotSpec 覆盖 narrative、duration_ms、CompositionIntent、continuity/camera/lighting/look intent、control_layers、generation_policy、selected_output_refs；Beat 只覆盖镜内时间/表演/frame_ref；DirectorScene 只覆盖世界空间 StagingGeometrySpec、精确 transform/姿势、相机/构图约束、路径、曝光、环境与灯具参数。画面左右/屏幕方向等只能成为固定 camera/anchor/canvas 的派生 ScreenGeometryObservation。
- FR-4：V1 ControlLayer 创建入口覆盖主表第 8.3 节全部类型，并明确 source_ref、必填 target_scope、条件必填 ShotTemporalAnchorRef/ControlSourceSelector/strength、coordinate_space、source_time_range、required 与 unsupported_policy；关键帧/动作/锚点局部控制必须有 target_anchor_ref，多对象/多片段 source_ref 必须有 selector，source-video layer 的强度只存在于 SourceVideoControlSpec fragments。整 Shot 的单一 Artifact 控制可省略 anchor/selector。共享 DirectorScene 组件必须由 selector 精确定位，不得使用无 shot 命名空间的裸 beat_id。
- FR-5：故事板与动作板先形成不可变 `BoardArtifact`；`GridArtifact` 只负责 9/16/25 排版、生成、切分与逐格结果，并以 GridCellMapping 显式映射 BoardPanelRef、Shot、CoverageMemberRef、ShotTemporalAnchorRef 或 Variant 及 CanonicalWritebackTarget；3D 使用可由 sequence、coverage 或 shot 共享的 DirectorSceneRevision，导出另存 DirectorSceneControlExport ArtifactVersion。上述对象只映射或引用权威草稿/Revision，不建立第二套镜头、故事 Beat 或镜内时间真相。
- FR-6：支持单镜、多选和计划级批量修改；每个 ResourceDraft 独立 CAS，EditContextSnapshot 保存多资源版本向量，批量提交使用原子 CommitManifest 与 RevisionCandidate。候选暂存内容可在通常要求固定 ResourceRevisionRef 的跨资源字段中使用 CandidateRevisionRef；事务必须预分配 candidate Revision ID、校验 expected resource type/id，为每项创建重写全部候选引用的新 resolved content ArtifactVersion，并以 CandidateResolutionEntry 对账 source/resolved Artifact 与 assigned Revision ID，再解析 ShotTemporalAnchorRef 和验证 Beat 归属。已激活 Revision 只指向 resolved Artifact，写入前显示受影响资源、字段 diff 和不可覆盖冲突。
- FR-7：支持单镜生成、从本镜下游重跑及候选替换；每次操作使用公共 WorkbenchActionType、WorkbenchScopeRef、requested_input_refs、draft_input_selectors、edit_context_snapshot_ref、expected_draft_versions、expected schema 和 idempotency key 的 WorkbenchActionRequest；固定输入与 Draft selector 禁止混装，运行时生成 InputFreezeManifest 后才创建 child run，禁止工作台直调 provider、伪造 actual refs 或隐式重跑 51 镜头。
- FR-8：生成前显示 ProviderCompilationReport；`summary_counts.blocked` 非零及对应 item `outcome=blocked` 必须解决，`degraded`/`ignored_with_warning` 必须显式确认，每个可独立控制片段均须展示 application_mode，禁止静默省略。
- FR-9：候选选择只以 CAS 更新对应 ShotSpecDraft.selected_output_refs 并保存新内容 ArtifactVersion；候选所属的执行 Revision 保持不变，最终计划提交再原子冻结当前草稿。
- FR-10：上游资产、Board/Grid 来源、DirectorSceneRevision 或 DirectorSceneControlExport 变化时按受影响 scope/shot 标记 stale，并提供继续旧版、替换引用或重算三种显式选择；SourceSpanCoverageReport、CoverageGroup 与 ShotSetupVariant 分别展示，禁止概念混用。
- FR-11：关键操作可由 workflow 编译计划建立 TF-WF-008 Human Gate；工作台和 Agent 均不能自行创建 Gate，决定、超时、撤销与恢复由运行时持久化。
- FR-12：身份、连续性和镜头控制质量报告必须引用输入/输出修订及 TF-QLT-001 rubric 版本。
- FR-13：任何 WorkbenchActionResult 的自动修订建议必须先形成 `WorkbenchWritebackProposal`，固定 base refs、expected draft versions、TypedWorkbenchPatchSet、字段权威、lineage、impact 与 stale effects；用户通过 ApplyWorkbenchProposalRequest 选择接受 patch set 后才 CAS。默认 apply_mode=atomic，任一目标冲突则全部不应用；显式 per_target 才允许部分成功并由 ApplyWorkbenchProposalResult 返回逐项状态，禁止结果直接改写草稿。

## 7. 交互与展示

- 桌面布局为镜头条带、主视图、属性/检查面板三域；面板可收起但不使用嵌套卡片堆叠。
- 镜头条带稳定显示序号、缩略图、时长、状态、stale、质量和警告，不因动态文字改变尺寸。
- 控制项按“叙事、表演、构图、摄影、灯光、参考、生成、审查”渐进披露；默认只显示景别、角度、构图、运镜、灯光意图、时长和参考，高级模式再显示毫米/FOV、坐标、屏幕几何、路径、曝光和灯具参数。
- 主视图支持“生成结果、故事板、动作板、宫格、3D 导演台、对比”切换；底部 Beat/镜头时域锚点带可叠加 actor blocking、camera path progress、orientation、optical 和只读灯光提示轨，V1 不宣称完整动画曲线编辑器。
- provider 兼容性使用 `blocked`/`degraded`/`ignored_with_warning` 明文与图标，不能只用颜色；每个 control fragment 可展开查看 application_mode 与转换结果。
- 移动端支持同一 owner 查看、筛选、私人批注、候选选择和有限字段调整；批注不形成协作审阅语义，也不承诺完整构图/3D 编辑。

## 8. 数据、类型与公共接口

- 工作台输入：WorkbenchTask 的固定输入快照、target_workbench、expected typed output、base revision，以及 ShotPlanEditSession 和 `EditContextSnapshot`；不得用单一逐镜 draft_version 代表计划、51 个 ShotSpec 与共享场景的并发状态。
- 核心数据沿用公共 `ShotPlanRevision`、`StoryBeatRef`、`ShotSequenceGroup`、`CoverageGroup`、`CutRelation`、`ShotSpecRevision`、`BeatOrKeyframe`、`ShotTemporalAnchorRef`、`CompositionIntent`、`ShotSetupVariant`、`SourceSpanCoverageReport`、`BoardArtifact`、`ControlLayer`、`ControlSourceSelector`、`GridArtifact`、`GridCellMapping`、`CanonicalWritebackTarget`、`StagingGeometrySpec`、`ScreenGeometryObservation`、`DirectorSceneRevision`、`DirectorSceneControlExport`、`ShotPlanEditSession`、`EditContextSnapshot`、`DraftInputSelector`、`RevisionCandidate`、`CandidateRevisionRef`、`CandidateResolutionMap` 与 `ShotPlanCommitManifest`。
- `EditContextSnapshot` 含 snapshot_id、session_id、session_version、base_revision_refs 和 draft_version_vector；每个 DraftVersionVectorEntry 固定 resource_type/resource_id/base_revision_id/draft_version/content_artifact_version_id。
- `WorkbenchActionRequest` 含 workbench_task_id、公共 WorkbenchActionType、WorkbenchScopeRef[]、固定 requested_input_refs、DraftInputSelector[]、edit_context_snapshot_ref、expected_draft_versions、config_ref、expected_output_schema 和 idempotency_key；服务端 `InputFreezeManifest` 固定 requested/actual refs、draft-resolution entries 与 snapshot fingerprint；`WorkbenchActionResult` 固定 manifest ref、actual_input_revision_refs、child_run_id、output_refs、usage、actual_cost、可选 provider_compilation_report_ref 及 WorkbenchWritebackProposal ref。
- `ShotReviewReport` 为 ArtifactVersion，包含 continuity_findings、identity_findings、control_adherence、rubric_ref 和 evidence_refs。
- 所有派生视图固定 source_revision_refs/draft content artifact version；不得以浏览器临时状态作为回写依据。
- 输出只能是冻结 `ResourceRef`/`ArtifactRef` 和 WorkbenchTask 完成状态，不能直接修改 NodeRun 状态。

## 9. 状态机与业务规则

- WorkbenchTask 使用 HumanTaskStatus；镜头生成使用 RunStatus/NodeRunStatus；资源使用 RevisionStatus，三者禁止混用。
- 每个 ResourceDraft patch 以 client_request_id 幂等并以自身 draft_version compare-and-swap；冲突返回字段级三方 diff，ShotPlanEditSession/EditContextSnapshot 只协调引用和版本向量而不成为真相源。
- 一个 shot 可有多个候选 ArtifactVersion，但 selected_output_refs 的每个角色只能有一个当前选中项。
- child run 可逐镜独立完成；结果回写逐 shot 幂等。最终 CommitManifest 是单个数据库事务，任一 expected version 失配时不得激活部分 ShotSpecRevision 或 ShotPlanRevision。
- 操作性单镜执行 Revision 可独立保留证据；只有最终 CommitManifest 产生的新 ShotPlanRevision 决定计划当前 ordered refs，旧计划保持不变。

## 10. 失败、降级与恢复

- base revision 缺失、授权失效或 draft_version 过期时禁止编辑，提供只读历史与重新基线入口。
- provider 不支持 required 控制时 `blocked`；可转换但有损时 `degraded`；可选控制允许忽略时必须为 `ignored_with_warning` 并由用户确认，不得静默丢弃。
- 批量运行部分失败时保留逐镜成功结果，失败镜头可重试；选择结果不因其他镜头失败回滚。
- 刷新、断网和服务重启后从 ShotPlanEditSession、各 ResourceDraft、RunEvent、WorkbenchActionResult 和 WorkbenchTask 恢复视图与任务状态。
- 取消后晚到结果作为隔离候选可审计，不能自动写入 selected_output_refs。

## 11. 安全、隐私、内容与授权

- 每次生成、重跑、冻结和导出重新计算当前 EntitlementDecision；历史 GrantSnapshot 不授权新行为。
- 私有缩略图和控制图使用短期签名访问，禁止在日志、URL 查询或剪贴板提示中泄露。
- 真人肖像/声音、未成年人、冒充和无权素材按 TF-SEC-001 阻断或人工审核。
- 批量操作需显示将披露给 provider 的引用数量和类型，并遵守最小披露。

## 12. 观测与运营

- 事件：workbench_opened、shot_patch_applied/conflicted、view_changed、compile_reviewed、shot_run_started、output_selected、revision_submitted。
- 指标：工作台首可交互时延、51 镜头导航延迟、保存冲突率、单镜成功率、批量失败率、降级确认率、恢复成功率。
- 质量指标引用 TF-QLT-001 的镜头控制、身份、连续性与交互回归，不创建孤立分数。
- 审计链从 WorkbenchTask 到 base revision、每次 patch、编译报告、provider 调用、选择和最终 Revision。

## 13. 验收标准

- AC-1：Given 51 镜头计划及共享 sequence/coverage DirectorScene，When 用户在生成结果、故事板、动作板、宫格、3D 导演台和对比六个视图切换并编辑，Then 所有视图读取同一 EditContextSnapshot 版本向量，计划、逐镜与场景字段按各自权威保存且无丢失。
- AC-2：Given 全部 V1 ControlLayer 类型、共享 DirectorScene 中两个同名 beat_id 及一个无 Beat 镜头，When 保存并刷新，Then source_ref、必填 target_scope、ShotTemporalAnchorRef、类型化 source_selector、coordinate_space、source_time_range、strength、required 和 unsupported_policy 完整恢复，三个目标均由 shot revision 命名空间无歧义定位。
- AC-3：Given required pose 控制不受 provider 支持，When 编译，Then outcome 为 blocked 且零调用；改用允许的 degrade 策略后 outcome 为 transformed/degraded，并展示 application_mode、语义损失和确认要求。
- AC-4：Given 单镜输出失败，When 通过 WorkbenchActionRequest 重试并替换，Then child run 可审计且其他 50 镜头草稿、修订和选中输出不改变。
- AC-5：Given 两端并发修改同字段或批量提交时一镜版本失配，When 保存/提交，Then 返回三方 diff，原子 CommitManifest 不产生任何部分激活 Revision。
- AC-6：Given TF-QLT-001 固定任务，When 完成镜头控制回归，Then 控制遵循与交互指标达到批准阈值和容差。
- AC-7：Given child run 返回含三个资源 patch 的 WorkbenchWritebackProposal，When 用户以 per_target 只接受两个 patch set，Then 仅对应草稿按 expected version 更新，第三项 skipped，ApplyWorkbenchProposalResult 逐项返回且原不可变输出保持不变；若使用默认 atomic 且任一项冲突，则全部不应用。
- AC-8：Given Request 以两个 DraftInputSelector 选择 Draft，并以 requested_input_refs 携带一个已固定 ResourceRevision，When 运行时冻结，Then 客户端请求不含 actual refs，服务端 InputFreezeManifest 唯一记录三项实际 Revision；版本失配时 child run 与 Manifest 均不创建。
- AC-9：Given 新 ShotSpecCandidate 与共享 DirectorSceneCandidate 通过 CandidateRevisionRef 在 anchor、ControlLayer 与 scope 中互引，When 原子提交，Then CandidateResolutionMap 按 expected type/resource 为每项记录 source candidate Artifact、新 resolved Artifact 和预分配 Revision ID，原暂存 Artifact 不变且隔离不可运行，已激活 Revision 只指向无 candidate ref 的 resolved Artifact；canonical/active/downstream API 不暴露 local candidate ID，也无部分 Revision 可见。

## 14. 测试场景

- 正常：51 镜头载入、逐镜编辑、批量时长、视图切换、编译、生成、选择、冻结和任务完成。
- 边界：1/51 镜头、100 缩略图、无 beat、共享场景同名 beat_id、多关键帧、全部控制层、长中文字段、窄桌面与移动查看。
- 失败：授权过期、编译阻断、部分 provider 失败、Artifact 损坏、草稿冲突、事件流断开。
- 权限：非 owner 触发生成、跨项目引用、撤权后重跑、私有缩略图访问、平台审核员越界编辑和未成年人内容。
- 并发/恢复：批量与单镜同时写、重复提交、取消晚到、浏览器刷新、断网重连和服务重启。

## 15. 交付与回退

- 各高级视图和批量运行独立功能开关；关闭时保留只读数据与基础 ShotSpec 编辑。
- 数据升级只能添加可选 ControlLayer/Beat 字段或新 schema version，未知类型以只读原文保留。
- 发布证据含 51 镜头桌面/移动视觉回归、全部控制层 round-trip、真实 provider、并发与恢复 E2E。
- 回退后不得删除新 Revision；不支持的视图显示兼容提示并禁止有损保存。

## 16. 已决策事项与开放问题

- 已决策：V1 Core 交付镜头级可展开工作台；ShotPlan、ShotSpec、Beat 与 DirectorScene 按字段各自唯一权威，EditContextSnapshot 只协调多资源版本向量。
- 已决策：复杂控制通过视图和领域任务组织，不展开成主业务画布节点。
- 已决策：V1 Core 提供动作板翻页/基础时间预览，不建立新的时间线真相；带精确切点、音轨和完整 timing 的 Animatic 后置 V1.5，并只引用 Board/Shot/Beat/Timeline 固定来源。
- 开放问题：可用性测试后冻结默认面板顺序和批量选择上限；不得削减 51 镜头或控制层范围。
