# TF-MED-004 多宫格与动作板

## 1. 元数据

- ID：TF-MED-004
- 标题：多宫格与动作板
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：分镜控制工作台
- 直接依赖：TF-MED-003、TF-MED-006
- 责任域：分镜产品/媒体 AI
- 个人 DRI：待指派

## 2. 背景与问题

9/16/25 宫格和动作板是 AIGC 制作中常用的覆盖、节奏与候选方法，但若把单元格当成镜头真相，会在切分、重排和局部修复后失去与 ShotSpec/Beat 的关系。

本需求将故事板/动作板语义与宫格排版分离：前者先形成固定来源的 `BoardArtifact`，后者才可按 9/16/25 布局生成 `GridArtifact`。逐格结果只形成显式回写提案，不建立第二套镜头、故事 Beat 或镜内时间模型。

## 3. 目标与非目标

- 目标：支持 9、16、25 宫格及 sequence、coverage、action、variation 四种模式。
- 目标：支持从 Shot/ShotTemporalAnchorRef 生成故事板/动作板，也支持按同一 BoardArtifact schema 手工组板和重新排序。
- 目标：故事板/动作板可形成语义面板序列；宫格可对 BoardPanelRef、Shot、CoverageMemberRef、ShotTemporalAnchorRef 或 Variant 进行排版、生成、切分、校验、逐格修复并安全回写到对应草稿。
- 目标：用 TF-QLT-001 固定样本评测格位映射、角色/风格一致性、动作可读性和切分完整性。
- 非目标：GridArtifact 不是 ResourceRevision，也不是 ShotPlan 或 ShotSpec 的 canonical 替代品。
- 非目标：不允许用户把 25 个格子自动当成 25 个新镜头而不经显式映射确认。

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可创建宫格任务、修复单元并向 ShotPlanEditSession 提交回写。
- 非 owner 不获得项目宫格查看、下载、重做或回写能力；平台审核员只按审核授权读取必要证据。
- 对跨 owner 的人物、场景、风格和镜头引用，必须先引用或提升为固定 ResourceRevision 并携带有效 GrantSnapshot；禁止用跨 owner 裸 ArtifactRef 生成或回写。
- 自动切分和建议不能替代用户的镜头新增、删除或顺序确认。
- 导出包含真人、未成年人或受权利限制素材的宫格时经过 TF-SEC-001 Gate。

## 5. 用户场景与主流程

1. 用户选择有序 Shot/ShotTemporalAnchorRef，填写 BoardGenerationSpec 后执行 `board.generate`，或在相同 schema 下手工添加/排序 panel；完成后保存固定 BoardArtifact。
2. 用户按兼容矩阵选择来源：sequence 使用 Shot 或 storyboard BoardPanelRef；coverage 使用 CoverageMemberRef，或使用其 Shot 属于该 member 的 storyboard BoardPanelRef；action 使用 ShotTemporalAnchorRef 或 anchor 一致的 action BoardPanelRef；variation 使用 ShotSetupVariantRef。随后选择 9/16/25 布局和固定 GridTemplateRef。
3. 系统生成 cell_mappings/cell_specs 预览，检查数量、顺序、rect、参考、provider 容量和 unsupported_policy。
4. `grid.generate` WorkbenchActionRequest 携带类型化 scope refs、edit_context_snapshot_ref 与完整 expected_draft_versions，运行时先验证版本向量并冻结作用域内所需 Revision，再由 child run 根据 Provider 能力生成原生整图、独立 cells 或分批合成宫格；系统始终保存逐格 Artifact、复合原图（若有）、模板几何、编译报告和映射。
5. 用户检查角色、动作、构图和连续性，单独重做失败格或修改映射。
6. 回写前系统比较 source revision、当前草稿和单元修改，展示三方 diff。
7. 用户确认后按每个目标 ResourceDraft 的 expected draft_version CAS 更新并生成新内容 ArtifactVersion；原 BoardArtifact/GridArtifact 保持不可变，最终修订由 ShotPlanCommitManifest 统一冻结。

## 6. 功能需求

- FR-1：`layout` 支持 9、16、25；custom-approved 仅由受控配置开放，不能接受任意超容量数字。
- FR-2：`mode` 为 sequence、coverage、action、variation，兼容矩阵固定为 sequence=shot|storyboard board_panel、coverage=coverage_member|经成员归属验证的 storyboard board_panel、action=temporal_anchor|anchor 匹配的 action board_panel、variation=variant；不在矩阵内的 branch 必须校验失败。storyboard/action 的语义面板必须先保存为 BoardArtifact，GridArtifact 不拥有语义顺序或时间。
- FR-3：GridCellMapping 是 tagged union：board_panel 映射固定 BoardPanelRef，sequence 映射 ShotPlan 中的有序 shot revision，coverage 映射同一 StoryBeatRef 下 CoverageMemberRef，action 映射 ShotTemporalAnchorRef，variation 映射单 Shot 的 ShotSetupVariantRef；每格按 mapping_kind 恰好保存一个类型化来源 payload，并保存 CanonicalWritebackTarget（目标 Resource、base revision、owned field path 及可选 anchor/member）。不得另存与类型化 payload 平行的泛型 source_ref；coverage 不得表示“同一镜头不同机位候选”。
- FR-4：生成前通过 `grid.generate` WorkbenchActionRequest 固定 WorkbenchScopeRef、已固定 requested_input_refs、需冻结草稿的 DraftInputSelector[]、edit_context_snapshot_ref、完整 expected_draft_versions 和 idempotency key；`config_ref` 指向不可变 GridGenerationSpec，由该配置保存 cell_mappings、参考顺序、布局模板版本、目标 provider policy/capability 要求和 control layers，不在 Request 上私加平行字段；实际冻结 refs 仅由服务端 InputFreezeManifest 产生。
- FR-5：每个成功 cell 必须保存独立 ArtifactVersion；Provider 原生支持或配方需要时可另存 composite_output_ref。GridArtifact 的 cell_results 必须与 cell_mappings 一一对应，失败格记录 failure_reason，不得伪造 output_ref，也不得仅保存复合图而丢失逐格来源。
- FR-6：自动切分须检测边界、空格、重复格和尺寸；失败单元不得用邻格复制冒充。
- FR-7：支持逐格重生成、编辑、替换和拒绝；每次操作保留 parent grid/cell lineage 与实际成本。
- FR-8：动作板以 `BoardArtifact(board_kind=action)` 保存 ordered_panels，每格仅通过 ShotTemporalAnchorRef/frame_ref 指向指定 Shot 的 Beat、shot_start 或 shot_end；shot_default 是非时间化静态锚点，禁止用于 action panel。pose_intent、performance、time_ms/normalized_time 和选中 frame_ref 的权威修改只能通过 WorkbenchWritebackProposal 回写 Beat 草稿，Board/Grid 不复制时间值。
- FR-9：回写只写 ShotPlanEditSession 中映射到的独立 ResourceDraft，并逐镜 CAS；来源变化时必须三方 diff 和用户合并，最终 CommitManifest 原子冻结，禁止最后写入覆盖或部分提交。
- FR-10：variation 模式使用不可变、非 canonical 的 `ShotSetupVariant` proposal overlay 表达单 Shot 候选设置；typed override 必须固定 owner domain、base ShotSpecRevision、owned field path/schema，涉及精确相机/路径/姿势/灯光时还必须固定 base DirectorSceneRevision 并写入 director_scene domain。Variant 不得自有 Beat ID/time 或复制精确 DirectorScene 参数；采纳时每个 override 必须生成 target_kind=shot_field 或 director_scene_field 的 WorkbenchWritebackProposal/patch set，Variant 仅作为来源 lineage，禁止把 Variant 作为 target_kind。由宫格提议新增 ShotSpec 时先形成明确 draft proposal，用户确认后才创建新 Resource 身份并显式更新计划，禁止把候选或 25 个格子自动转成 coverage/新镜头。
- FR-11：provider 控制项按 TF-MED-006 编译，`blocked`/`degraded`/`ignored_with_warning` 及每个 fragment 的 application_mode 在生成前可见。
- FR-12：质量报告引用 TF-QLT-001 版本，逐格记录映射正确性、身份、风格、动作与构图分项。
- FR-13：不得假定复合宫格天然适合作为多角度身份或镜头控制输入；编译器必须根据 CapabilitySnapshot 将 cell 处理为独立 reference、有序关键帧、明确支持的 composite 或 blocked/degraded，并逐 fragment 记录 application_mode。
- FR-14：`BoardGenerationSpec` 必须固定 board_kind、ordered source mappings、panel role、reference/control refs、canvas、panel count policy 和 provider policy；手工组板使用同一 mapping schema。`board.generate` 结果保存 BoardArtifact、逐 panel output/失败原因和成本，不得直接修改 Shot/Beat。

## 7. 交互与展示

- 布局使用 9/16/25 分段控件，mode 使用带说明的选项菜单；显示预计单元数、成本和分辨率。
- 宫格保持固定 aspect-ratio；单元状态、序号和映射标签不得改变格子尺寸或遮挡内容。
- 点击单元进入并列检查：来源 BoardPanelRef/Shot/ShotTemporalAnchorRef/Variant、切分图、当前草稿、质量问题、重做与回写命令。
- 动作板提供时间轴和翻页播放；不以宫格顺序暗示精确帧时长，时间来自 BeatOrKeyframe。
- 移动端可由同一 owner 查看、逐格选择和添加私人批注；批注不形成协作审阅语义，批量映射与三方合并保留桌面端。

## 8. 数据、类型与公共接口

- 核心沿用 `BoardGenerationSpec`、`BoardSourceMapping` 与 `BoardArtifact`：BoardSourceMapping 按 source_kind 恰好固定 shot_revision_ref 或 ShotTemporalAnchorRef，并保存 order/panel_role；BoardArtifact 固定 board_kind、source_revision_refs、ordered_panels。`GridArtifact` 固定 layout、mode、GridTemplateRef、rows/columns、reading_order、composite_canvas/gutter、GridCellRect[]、source_revision_refs、cell_mappings、composite_output_ref、cell_results；Board/Grid 均是不可变派生 Artifact。
- `GridGenerationSpec` 为 ArtifactVersion 内容，固定 grid_template_ref、ordered_reference_refs、provider_policy_ref、按 cell_index 键控的 cell_specs 和 control_layers；每个 cell spec 保存 prompt/config/mapping，不使用无法对账的平行 cell_prompts 数组。
- `GridCellMapping` 至少含 cell_index、mapping_kind、canonical_writeback_target、expected_role，以及按 mode/mapping 兼容矩阵恰好一个 BoardPanelRef、shot_revision_ref、CoverageMemberRef、ShotTemporalAnchorRef 或 ShotSetupVariantRef；ShotSetupVariantRef 固定 variant ArtifactVersion、variant_id 和 base Shot revision，该类型化 payload 就是唯一来源身份。CanonicalWritebackTarget 必须固定 target resource type/id、base revision、owned_field_path 及必要子对象引用，target_kind/field path 必须与 mode/branch 相容，动作目标不复制 Beat 时间。
- `GridCellReview` 保存 owner_actor_id、quality_report_ref、decision、replacement_artifact_ref 和 reason；owner_actor_id 只是审计主体，不创建项目审阅角色。
- 逐格修订先形成公共 WorkbenchWritebackProposal，TypedWorkbenchPatchSet 固定 session/edit snapshot、grid/cell、CanonicalWritebackTarget、base revision、expected draft version、字段权威和 lineage；ApplyWorkbenchProposalRequest 默认 atomic，显式 per_target 才允许逐格部分成功并返回 ApplyWorkbenchProposalResult，跨镜结果最终进入 ShotPlanCommitManifest。
- BoardArtifact/GridArtifact 固定来源 Revision，不读取 latest；动作板 frame_ref 仍是 ArtifactRef，不创建独立版本或时间语义。

## 9. 状态机与业务规则

- 宫格 child run 使用公共 RunStatus/NodeRunStatus/AttemptStatus；draft、compiled、generating、split_ready、review_ready 只作为事件 phase，不建立平行运行状态族。
- `GridCellResult.status` 是该不可变 GridArtifact 内的 cell 生成/切分结果，不复用 RevisionStatus；至少区分 pending、generated、split_failed。reviewed/rejected/replaced/writeback 决定分别保存为新的 GridCellReview 或 applied-patch Artifact，不回改原 cell_results。
- 同一 grid spec/client_request_id 重试幂等；provider 返回重复结果不得产生双份 split refs 或计费。
- 回写以每个 target expected_draft_version compare-and-swap；完成后记录 applied patch artifact，不修改 GridArtifact；跨镜批量冻结由原子 CommitManifest 完成。
- action 模式的 normalized_time 必须在每个 Shot 内 0..1 且单调；跨 Shot 顺序由 ShotPlan 与 BoardPanel order 决定，不比较不同 Shot 的局部时间。time_ms 必须落在所属 ShotSpec duration_ms 内。

## 10. 失败、降级与恢复

- provider 不支持一次生成 25 格时，可按明确编译报告降级为分批生成后合成；不得声称是单次一致生成。
- required 布局/参考无法应用时 `blocked`；可降低分辨率时 `degraded` 并展示预计影响；可选片段忽略只能是 `ignored_with_warning`。
- 切分失败只重跑切分或目标格，保留整图原件和其他成功单元。
- 回写发现来源修订变化时停止，生成三方 diff；用户可重基线、手工合并或放弃。
- 刷新、断网或 worker 重启后从持久任务、GridArtifact 和 RunEvent 恢复，不重新计费已完成格。

## 11. 安全、隐私、内容与授权

- 宫格合成前对每个 source revision 做 EntitlementDecision；一项失权不得通过拼图隐藏后继续生成。
- 单元下载和整图导出分别鉴权，签名 URL 不得跨项目复用。
- 真人肖像、声音关联动作、未成年人和冒充场景执行 TF-SEC-001 审核与披露。
- 回写保留授权快照和来源署名；删除预览不能删除历史合法运行的隔离审计证据。

## 12. 观测与运营

- 事件：grid_spec_created、grid_compiled、grid_generated、grid_split、cell_regenerated、writeback_conflicted/applied。
- 指标：各布局成功率、单格成本、切分错误率、逐格重做率、身份/动作 rubric、回写冲突率、恢复成功率。
- 运营看板按 provider/model/capability revision 区分原生宫格与分批合成，避免错误质量归因。
- 支持信息含 grid artifact、cell index、source revisions、compilation report、run/attempt 和 correlation_id。

## 13. 验收标准

- AC-1：Given 固定镜头来源和三个 GridTemplateRef，When 分别生成 9/16/25 宫格，Then GridArtifact 固定模板 revision、行列/阅读顺序/composite canvas/gutter/cell rect，cell_mappings 与 cell_results 完整一一对应，历史结果可按原 rect 确定性重切。
- AC-2：Given action Board 映射共享 DirectorScene 中两个同名 beat_id 和一个无 Beat Shot，When 生成宫格并播放动作板，Then 每格通过 ShotTemporalAnchorRef 无歧义定位，时间顺序由各 Shot 的 Beat 约束，Board/Grid 均不复制 Beat 时间且不会修改既有 ShotSpecRevision。
- AC-3：Given provider 不支持 25 格，When unsupported_policy 为 block/degrade/ignore_with_warning，Then 三种路径分别产生 blocked、报告分批 transformed/degraded、或 ignored_with_warning 且留痕。
- AC-4：Given 来源 revision 在生成后变化，When 用户回写，Then 必须出现三方 diff，未经确认不能写草稿。
- AC-5：Given 第 17 格切分失败，When 单格修复，Then 其余 24 格 ArtifactRef 与映射不变且只产生一次新增成本。
- AC-6：Given TF-QLT-001 宫格固定集，When 回归，Then 映射、切分、身份和动作分项达到批准阈值。
- AC-7：Given 多角度角色参考的 9 格 composite 但目标 Provider 只支持独立 references，When 编译，Then 系统发送有序 cells 或按数量上限阻断/降级，绝不把 composite 静默冒充已原生应用的身份控制。
- AC-8：Given 三个 Shot 和每镜两个动作锚点，When 分别通过 `board.generate` 与手工组板创建 storyboard/action Board，Then ordered mapping、panel role、frame/anchor、失败项和成本可恢复，且任何方式都不直接修改 ShotSpec/Beat。

## 14. 测试场景

- 正常：四种 mode、三种布局、生成、切分、逐格评审、修复、回写和新镜头提案。
- 边界：单镜覆盖 25 格、25 镜 sequence、空 frame_ref、共享场景同名 beat_id、无 Beat 镜头、极端画幅和长标签。
- 失败：provider 容量不足、合成失败、边界检测错误、split Blob 丢失、质量报告缺失、来源 stale。
- 权限：一格来源失权、跨项目回写、非 owner 重做、撤权后导出、私有格 URL 复用和平台审核员越界写入。
- 并发/恢复：两端改同格、重复回调、取消后晚到、切分 worker 重启、断网续传和三方合并。

## 15. 交付与回退

- 布局和 mode 分别受功能开关控制；关闭生成仍允许只读历史 GridArtifact 的 composite_output_ref 与 cell_results。
- GridArtifact schema 扩展需保留未知 cell mapping 字段；回退客户端不得重排或有损重存。
- 发布证据包括 9/16/25 真实生成、四模式映射、TF-QLT-001 报告、冲突回写与恢复 E2E。
- provider 降级策略可单独回退为 block，不改变已存编译报告和调用记录。

## 16. 已决策事项与开放问题

- 已决策：故事板/动作板语义属于 BoardArtifact，宫格只负责布局/生成/切分；ShotPlan、ShotSpec 与 Beat 仍按字段权威，逐格修订必须通过显式回写。
- 已决策：V1 支持 9/16/25 和四种 mode，provider 不支持时不能静默忽略。
- 已决策：复合宫格是展示/批处理产物，不默认是 Provider 的多角度身份控制格式；逐格 Artifact 与语义映射必须永久保留。
- 开放问题：真实 provider spike 后冻结 custom-approved 白名单和各布局最低输出分辨率。
