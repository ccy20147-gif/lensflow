# LensFlow 创作外壳、个人创作资产与项目关联

## 1. 元数据

- ID：TF-PLT-003
- 标题：LensFlow 创作外壳、个人创作资产与项目关联
- 状态：reviewed
- 目标版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：产品外壳/项目工作室/创作资产库
- 直接依赖：TF-ARC-002、TF-PLT-001、TF-PLT-002、TF-WF-004、TF-WF-005、TF-WF-006、TF-WF-009、TF-OPS-004、TF-NFR-001
- 责任域：核心产品/前端平台/工作流平台/资源平台
- 个人 DRI：main-agent

## 2. 背景与问题

当前产品已经具备 Vue Flow 动态画布、WorkflowDraft/Revision、编译、持久运行、Artifact/Resource 和官方模板实例化等底层原语，但前端仍接近合同验证控制台：根路由直接进入项目列表，Project 与 Workflow/Resource/Run 没有稳定关联，Workflow 缺少面向用户的资产元数据，节点产物不能在运行上下文中预览和保存，模板页仍暴露 JSON、revision ID 和内部依赖术语。

LensFlow 需要同时服务两种创作方式：非技术创作者从目标和官方模板获得首个可编辑结果，专业创作者从空白画布、世界观、角色 OC 或私有剧本开始自由编排。画布、世界观、角色和剧本应在用户浏览心智上平级，但底层必须继续保持 Workflow、Resource、Artifact、Run 和 Template 的单一真相边界。

本需求负责建立“创作资产库 + 项目工作室”双轴体验，补齐 Project 关联、私人 Workflow 资产、运行产物检查和 Artifact 提升闭环；它不复制各领域 PRD 的内容 schema、运行时或社区发布合同。

## 3. 目标与非目标

目标：

- 所有用户可见产品名称统一为 LensFlow，历史 TF 需求 ID、数据库证据和不可变旧 schema 保持兼容。
- 建立模板引导与专业画布双模式；新手无需打开画布，专家可以自由组合强类型业务节点。
- 让 Workflow 成为可命名、检索、克隆、归档和版本化的私有创作对象。
- 建立 owner 级创作资产库、项目内容视图和手工集合，不制造新的内容真相源。
- 建立节点 OutputBinding/SelectionRecord 到固定 ResourceRevision 的显式资产化闭环。
- 统一 Run、HumanTask、节点产物和历史节点深链，刷新或断线后可恢复。
- 为 World、Character OC、Screenplay 和媒体资产提供固定版本的“使用此版本创作”入口。
- V0/V1 Core 提供轻量时间线入口；WebAV 有限导出继续遵守 TF-MED-012 的 V1.5 边界。

非目标：

- 本切片不交付消费社区、用户公开 Listing、推荐、交易、收益分成或多人协作。
- V1 不允许 Screenplay 独立上架、搜索、引用、派生，或包装为只读 CreativeWork 公开。
- 不建设递归文件夹、完整 NLE、长片浏览器编码、任意代码节点或 Agent 嵌套。
- 不把 Workflow 改造成 Resource，不把 Artifact 自动批量提升，不把前端状态作为运行或版本真相。
- 不在认证产品基础包同时引入 GSAP、Anime.js、Three.js 和 WebAV。

## 4. 用户与权限

- 非技术创作者从一句话、目标和官方模板开始，不必理解 DAG、Agent、Recipe、schema 或 revision ID。
- 专业创作者可以从空白画布或固定 World/OC/Screenplay Revision 开始，管理 Draft、编译、激活和运行。
- 工作流作者可以搜索、连接和配置强类型业务节点，查看依赖、成本、运行来源和失败诊断。
- 资产持有者可以把准确节点产物保存为新 Resource，或作为已有 Resource 的新 Revision。
- V1 私有项目仍为单 owner；Project 是生产上下文，不是内容 owner，也不授予团队角色。
- 一个可变 WorkflowDraft 只能有一个 `owned_editable` Project 关联；跨项目复用 Workflow 必须固定引用 Revision 或显式克隆。
- Resource 可以关联多个 Project，但运行、导出和发布只消费固定 ResourceRevision。
- archived 或 deletion_pending Project 禁止新 Draft mutation、运行、上传、Artifact promotion 和导出。
- 所有读取和写入均由后端验证 owner_scope、Project 状态和稳定对象关联；URL 中的 project_id 不能替代授权事实。

## 5. 用户场景与主流程

1. 引导创作：用户在“创作”输入一句话并选择已启用目标，选择官方模板，完成依赖、Provider、授权和成本预检，选择或创建项目；系统以可恢复事务创建 WorkflowDraft，启动运行并打开首个领域工作台结果，画布仅作为次级入口。
2. 自由画布：用户选择项目并命名 Workflow，选择空白画布、起步模板或 Architect 提案；编辑后执行“检查并运行新版本”，系统依次保存 Draft、编译、激活不可变 Revision 和启动 Run。
3. 从资产开始：用户在 World、OC 或私有 Screenplay 的固定 Revision 详情选择“使用此版本创作”，选择目标、模板和项目；系统写入固定 ResourceRef，展示版本、来源、权限与 stale 影响后开始创作。
4. 产物资产化：用户从 Run 中准确节点的非 superseded output/candidate 打开 Artifact Inspector，查看预览、schema、模型、成本和 lineage，选择新建 Resource 或更新已有 Resource，新建或冻结固定 ResourceRevision 后进入个人资产库。
5. 项目生产：用户在项目工作室查看项目资产、Workflow、Run、waiting_user、失败任务和交付物；所有深链刷新后恢复相同 project/workflow/run/node/task 上下文。
6. 轻量时间线：用户从固定 ShotPlanRevision 创建 TimelineDraft，预览代理、排序和替换单镜，保存 TimelineRevision 和片段包；V1.5 通过设备预检后才启用 WebAV 有限导出。

## 6. 功能需求

- FR-1：用户可见产品名称、页面标题、元数据和产品文案必须使用 LensFlow；历史 `TF-*` ID 和已冻结 `toonflow.*` schema 不改写，新 schema 使用 `lensflow.*` 并提供只读兼容映射。
- FR-2：首发一级导航必须为“创作、项目、创作资产、任务与运行”；设置和高级工具进入账户菜单。社区保留 feature flag 和 IA 位置，但在没有真实只读内容闭环时完全隐藏。
- FR-3：“创作”首页必须以一句话、目标和“开始创作”为主动作，并展示继续创作、官方模板、最近资产和待处理任务；只展示已通过对应版本 Gate 的目标。
- FR-4：Project 必须通过稳定 link 关联 Workflow 和 Resource；一个 Workflow 仅允许一个可编辑项目归属，Resource 允许多项目关联。新建 Workflow 必须显式选择项目，不能创建隐形默认项目。
- FR-5：Workflow 必须提供独立于执行图的名称、描述、封面、标签、归档状态、metadata version、created_at 和 updated_at；metadata 不进入 graph/execution hash，并使用独立 ETag/CAS。
- FR-6：遗留无可靠 Project 关联的 Workflow/Resource 必须进入 owner 级 `legacy_unassigned` 投影，不创建虚假项目；对象可读、可导出，但修改或运行前必须显式关联 active Project。
- FR-7：专业画布必须使用独立 CanvasShell：顶部版本/运行工具栏、左侧节点与资产库、中央 Vue Flow、右侧配置/输入/输出/lineage Inspector、底部 RunDock。服务器 Draft envelope 与 Vue Flow 交互图不得在多个 store 重复维护。
- FR-8：“检查并运行新版本”必须是带顶层 command/idempotency record 的可恢复操作，固定 expected draft version/full hash；步骤为保存 Draft、编译、激活 Revision、启动 Run。编译失败不创建 Revision；Run 启动失败不回滚已激活 Revision，重试不得再创建 Revision。
- FR-9：Run 列表必须支持 project、workflow、status 和 cursor；RunEvent 使用 `(run_id, seq)` 单调游标，snapshot 返回 current_seq，事件流支持 after_seq、gap detection、重连和重复抑制。Outbox 只负责投递，不代替事件时间线。
- FR-10：运行深链中的 node ID 必须是固定 WorkflowRevision 的 node_instance_id；节点后来从 Draft 删除时仍可只读查看历史。Provider、Agent、业务节点和 WebAV 必须统一写 node output binding，旧 epoch 或取消后输出只能 quarantined。
- FR-11：`/assets` 必须是 owner 级创作资产投影，`/projects/:id/assets` 必须是项目引用视图；两者返回 Workflow/Resource/外部固定 ResourceRef 的 typed locator，不复制 graph、content_json 或 latest。
- FR-12：个人资产支持类型、项目、标签、来源、状态和更新时间筛选，以及 owner 级手工集合。对象可属于多个集合；删除集合只删除组织关系，不删除或移动资产。V1 不支持嵌套目录和持久化智能筛选。
- FR-13：Artifact promotion 必须绑定准确非 superseded OutputBinding 或 SelectionRecord，验证 owner、schema revision、Blob durability、lineage 和 Project；新建模式在一个事务内创建 Resource、初始 Draft、Revision 1、Project link 和 outbox，并返回固定 ResourceRef。
- FR-14：更新已有 Resource 必须提交 target_resource_id、base_revision_id、expected_draft_version、准确 artifact_version_id 和 idempotency key，显示 base/current/proposed diff，并创建新 Revision，不覆盖历史。相同幂等键返回原结果；从同一 Artifact 分叉必须是显式用户动作。
- FR-15：Resource 类型能力必须由不可变 ResourceTypeDefinitionRevision/registry snapshot 裁决，固定 content schema、可提升 Artifact schema、兼容规则和 publishability；Screenplay 的 publishability 必须为 `private_only`。
- FR-16：模板详情必须展示目标、输入输出、只读阶段图、Provider、人工确认、依赖、replacement slot、成本和时长范围；preview 只作建议，instantiate 必须重新预检。新项目和现有项目实例化均须原子创建 Workflow、Draft、link、lineage 和 attribution，并通过可恢复 CreationSession 防止刷新产生孤儿对象。
- FR-17：用户私人模板只能保持 private；官方目录与未来社区 Listing 分离。模板打包必须扫描 graph、config、prompt、示例输入、source span、ArtifactRef、lineage、CredentialBinding 和签名 URL，私有 Screenplay 依赖必须删除或转为 typed replacement slot。
- FR-18：World、OC 和 Screenplay 详情必须使用“使用此版本创作”，持续展示固定 Revision、来源、权限和 stale；升级版本前展示 diff 和受影响节点。Screenplay 不得出现社区发布入口。
- FR-19：Timeline 必须复用 ResourceDraft/Revision；patch 在同一事务创建 Composition ArtifactVersion、执行 Draft CAS 并写事件。V0/V1 Core 只承诺轻量时间线预览、代理、基础音轨/字幕、单镜替换和片段包；WebAV 导出保持 V1.5 feature flag。
- FR-20：移动端 P0 只允许项目/资产查看、运行已激活 Revision、处理 HumanTask、从已有候选单镜替换、播放和保存 TimelineDraft；不提供完整画布连线、复杂 3D、精细时间线、冻结 TimelineRevision 或 WebAV 导出。

## 7. 交互与展示

- 全局采用 PublicLayout、ProductShell、ProjectShell 和 CanvasShell 四层布局；项目 overview 不得与子路由内容叠加在同一 dashboard 容器。
- 稳定路由至少覆盖 `/create`、`/projects/:projectId/workflows/:workflowId`、Run/Node 深链、Project/全局资产、Template detail/use、Task detail 和 Timeline detail；旧 query URL 使用 replace redirect 保留一个发布周期。
- 首页目标矩阵至少包含：V0 故事/剧本到 ShotPlan/Timeline JSON、产品 Brief 到广告候选；V1 Core 再按 Gate 开启 World、OC、Screenplay 和真实视频路径。
- Workflow、Resource、Run 和 Task 以名称、缩略图和业务状态为主识别，UUID、schema 和 trace 只在展开技术详情时出现。
- 界面术语必须区分“保存草稿、检查流程、激活版本、运行版本、发布/上架”；内部 Revision 不得使用 community 的 published/listed 文案。
- Artifact Inspector 必须显示真实 typed preview、生产节点、Run/Attempt、模型、成本、lineage、引用去向，以及“作为下游输入、保存到创作资产、设为交付物”等可用动作。
- promotion 期间只显示“正在保存到创作资产”；只有服务端返回固定 ResourceRef 后显示成功，并提供“打开资产”和“在当前流程使用此版本”。
- 错误必须说明发生了什么、受影响对象、工作是否已保存、下一动作和 correlation ID；stale、缺失、无权、未审查和解码失败不得统一为“加载失败”。
- CAS 冲突必须保留本地 patch 并进入三方 diff；空画布提供首节点、起步模板和 Architect 三种入口。
- 视觉方向为安静、密集、专业的电影创作与知识工作室。普通产品交互使用 CSS/Vue Transition 150–220ms；Flow-to-File 仅为内部视觉签名，不作为按钮或导航术语。
- GSAP 仅按需加载于产物资产化连续动效或独立活动页；Anime.js 不进入认证产品基础设施；Three.js 只进入 3D 导演台 lazy chunk；WebAV 只进入时间线导出 chunk。所有动效提供 reduced-motion、文字和非颜色表达。
- 默认主题跟随系统并支持持久 light/dark；统一 semantic tokens、Lucide 图标、4px spacing grid 和 6–14px 圆角，禁止玻璃拟态、渐变文字、卡片套卡片和非真实工具表面的装饰网格。

## 8. 数据、类型与公共接口

新增或补齐的稳定模型：

- `project_workflow_links(project_id, workflow_id, role=owned_editable, created_at)`，workflow_id 唯一。
- `project_resource_links(project_id, resource_id, role=reference|working|delivery, created_at)`。
- Workflow identity metadata/version、WorkflowRevision.layout_hash 和 `workflow_runs.project_context_id`。
- `durable_sessions`，只存 token hash、TTL 和 revoke 状态，不保存明文 token。
- `idempotency_records(owner_scope, operation, idempotency_key, request_hash, status, response_ref)`。
- `run_events(run_id, seq, event_id, event_type, payload_ref, occurred_at)`。
- `node_output_bindings(attempt_id, execution_epoch, output_port, ordinal, artifact_version_id, publish_state)`。
- `resource_type_definition_revisions`、`asset_collections`、`asset_collection_entries`。
- ArtifactVersion 内不可变 lineage snapshot 为真相；`lineage_edges` 和 `resource_revision_dependency_edges` 为可重建查询投影。
- `creation_sessions` 负责模板/引导创建恢复；V1.5 的 `client_execution_bindings` 由 TF-MED-012 管理。

公共 API 至少提供：

- `GET/POST /projects/{project_id}/workflows`、`GET/PATCH /workflows/{workflow_id}`。
- `POST /workflows/{workflow_id}/check-and-run-commands`。
- `GET /runtime/workflow-runs`、Run snapshot/events、node outputs 和带 project/workflow/status/cursor 的 HumanTask 列表。
- `GET /me/creative-assets`、Project assets、集合 CRUD 与 stable typed locator。
- `POST /artifact-promotions` 与 promotion request/result 查询。
- Template preview、CreationSession create/read/patch/commit 和 target new/existing project instantiate。
- Timeline from-shot-plan、Draft patch/CAS、Revision freeze、preview manifest 和 clip package API；WebAV export API 保持 V1.5。

所有列表统一 cursor/page info；所有状态变更统一 idempotency；所有 Draft mutation 使用 expected version/full hash CAS；错误统一返回 stable code、safe message、details、correlation_id 和 retryable。

## 9. 状态机与业务规则

- RequirementStatus、RevisionStatus、RunStatus、NodeRunStatus、AttemptStatus、HumanTaskStatus、ListingStatus 和 ModerationStatus 必须保持独立，公共 enum 先与 Master 对齐再迁移。
- Workflow metadata 更新不得改变 execution hash；Draft graph/config/layout 的 full CAS 与 metadata ETag 分开。
- `check-and-run` 顶层 command 固定同一 Draft snapshot。激活事务写 WorkflowRevision、CompiledPlan、Draft.base_revision_id、layout_hash 和 outbox；Run 在后续事务幂等创建。
- Run 启动失败返回 `revision_activated + run_start_failed/retryable`，不得显示为保存失败；同一 command 重试使用原 revision/run slot。
- CreationSession 使用独立状态 `collecting_inputs | preflight_blocked | ready | committing | committed | cancelled`，不得代替 RunStatus 或 Template 状态。
- 未关联遗留对象只存在于 owner 投影；新 Workflow 写入必须同时建立 Project link，不能仅靠异步索引补偿。
- promotion 成功后新 ResourceDraft 必须以 Revision 1 为 base 并推进 draft_version；相同 operation key 不重复创建 Resource/Revision。
- 收藏、手工集合、Project link、外部 LibraryEntry 和 Artifact promotion 是不同关系，不能共享一个“加入资源库”状态。
- 索引和投影延迟不影响 canonical 成功；提交成功后必须可按稳定 ID 直接打开并由 outbox 重建视图。

## 10. 失败、降级与恢复

- 页面刷新从 URL、WorkflowDraft/Revision、Run snapshot、HumanTask、ResourceRevision 和 CreationSession 恢复，不以本地 toast 为成功事实。
- SSE 断线使用 after_seq 回放；检测 seq gap 时重新加载 snapshot；重复、晚到或 superseded 事件不能重复触发 UI 动作。
- 离线画布只显示本地待同步；恢复后以 full Draft CAS 提交，冲突不得自动覆盖。
- 模板预检、依赖或 Provider 失败时保持 CreationSession，不创建半项目；提交阶段失败按同一 idempotency key恢复或返回可审计补偿状态。
- Artifact durability、schema、owner、lineage 或 Project 验证失败时 promotion 整体回滚，不留下无 Revision 的半资源。
- canonical promotion 成功而投影失败时返回“资产已创建、索引同步中”，可按 ResourceRef 打开，不重复创建。
- Provider、媒体或缩略图部分失败保留成功候选；单个 preview 失败不得导致画布、资产库或时间线白屏。
- WebAV 不可用、超限、取消或页面离开时保留 TimelineRevision 和片段包；客户端 phase 不得直接标记 Export/Run completed。
- 应用回退不得删除新建不可变 Artifact、Revision、RunEvent 或审计证据；新表优先保留并由旧应用忽略。

## 11. 安全、隐私、内容与授权

- 客户端不得提交或伪造 created_by_run_id、Attempt、execution epoch 或权威 lineage；运行产物只由 runtime/internal service 创建绑定。
- ArtifactRef 只允许同 owner_scope；跨 owner 必须先形成 ResourceRevision，再通过当前 EntitlementDecision 和带证据 ResourceRef 使用。
- Project link 不授予跨 owner 权限；所有 mutation 同时验证 owner、Project status、对象关联和当前授权。
- Screenplay 在社区搜索、Listing、LibraryEntry、CreativeWork 包装和模板内嵌上全部 fail-closed。
- 模板打包必须递归检查 graph/config、prompt、示例输入、source span、ArtifactRef、lineage、CredentialBinding、secret 和签名 URL；不可公开依赖只能移除或转 typed replacement slot。
- 用户私人模板不得通过 `visibility=public` 绕过 ListingRevision、Moderation 和 LicenseOffer；官方 TemplateRecord 与未来社区 Listing 分开。
- 私有缩略图、媒体代理和 WebAV 输入使用短期签名访问，浏览器缓存不得跨账户或共享设备泄漏。
- 每次编译、运行、promotion、模板实例化、导出和未来发布都重新计算当前权限；历史 GrantSnapshot 只用于审计。

## 12. 观测与运营

- 记录 creation_started/preflight_blocked/committed、project_linked、workflow_draft_saved/compile_failed/revision_activated、run_started/waiting_user/completed/failed。
- 记录 node_output_published/quarantined、resource_promotion_started/completed/failed、template_previewed/instantiated、timeline_created/patched/clip_replaced。
- 监控首次模板创作成功率、首个可编辑结果时间、CAS 冲突率、RunEvent gap/reconnect、promotion 成功/重放率和 projection rebuild。
- 监控 legacy_unassigned 数量、旧路由命中、跨项目缓存污染、无权访问拒绝和私有 Screenplay 泄漏告警。
- 前端采集 LCP、INP、CLS、画布 FPS、Inspector 延迟、100 缩略图错误率和 WebGL/context loss；不得记录私有 prompt、剧本文本、Blob URL 或 secret。
- 旧路由兼容至少保留一个发布周期，只有连续 14 天命中率低于 1% 且无关键外部链接后才能移除。
- 任何 canonical/投影差异、promotion 半事务、重复 Revision 或跨 owner 输出绑定必须产生高优先级审计事件和恢复 runbook。

## 13. 验收标准

- AC-1：至少 8 名首次用户中 7 人能在 5 秒内指出“创作”首页主动作，且默认模板表单首屏必填决策不超过 4 项。
- AC-2：至少 85% 首次用户在不打开画布的情况下完成模板选择、运行启动并打开首个可编辑结果；不含 Provider 等待的中位操作时间不超过 120 秒。
- AC-3：一个项目创建多个命名 Workflow 和多类 Resource 后，刷新、重新登录和项目切换均恢复相同对象、版本和关联；跨项目缓存不残留。
- AC-4：遗留无关联 Workflow 可读、可导出但不能修改或运行；显式关联 active Project 后历史 Revision/Artifact ID 不变。
- AC-5：50 节点/100 边下画布平移缩放 P5 不低于 45 fps，Inspector 打开 P95 不超过 100 ms；键盘可完成添加、配置、连接、保存、检查和运行。
- AC-6：Draft 编译失败时保留保存内容且不创建 active Revision；Run 启动失败后相同 command 重试只使用原 Revision 和 run idempotency slot。
- AC-7：SSE 断线期间产生 20 个 RunEvent，重连后按 seq 完整恢复且不重复 UI 动作；历史已删除节点仍可从 Run 深链只读查看。
- AC-8：节点产物 promotion 后原 Artifact hash 不变，返回可直接打开的固定 ResourceRef，ResourceRevision 可追溯准确 OutputBinding、Attempt、模型、成本和 lineage。
- AC-9：相同 idempotency key 重试不重复创建 Project、Workflow、Run、Resource 或 Revision；同一 Artifact 只有在用户显式选择“创建副本”时才允许分叉。
- AC-10：跨 owner Artifact、未 durable Blob、不兼容 schema、quarantined/late output 和无权 Project 的 promotion 全部 fail-closed，且不留下 Draft/Revision/link。
- AC-11：官方模板在新项目和现有项目中实例化均原子产生 WorkflowDraft、Project link、固定依赖与 attribution；刷新 CreationSession 不产生孤儿项目。
- AC-12：私有 Screenplay 在社区搜索、Listing、CreativeWork 包装、模板包、日志和错误 payload 中零暴露；私有模板依赖被阻断或转 replacement slot。
- AC-13：V0/V1 Core Timeline 刷新后顺序、时长、片段选择和固定媒体引用完整恢复；单镜替换只影响目标 clip、相关 EditDecision 和 stale 下游。
- AC-14：核心页面 P75 满足 LCP <= 2.5s、INP <= 200ms、CLS <= 0.1；正文对比不低于 4.5:1，控件/大文本不低于 3:1。
- AC-15：360px、1024px、桌面、200% zoom、最长测试字符串和 reduced-motion 下无不可达主动作、重叠或仅靠颜色传达状态；移动端不渲染完整画布、3D、精细时间线或 WebAV 导出。

## 14. 测试场景

- 正常：一句话/模板到首个结果、空白画布、从固定 World/OC/Screenplay 开始、Project/Workflow 创建与切换、Run/Task 深链、promotion、TimelineDraft。
- 边界：50 Workflow、100 Resource/缩略图、50 节点/100 边、51 Timeline clips、长名称、多集合、同一 Artifact 显式分叉和遗留未关联对象。
- 失败：Draft/metadata CAS、编译失败、Run 启动失败、SSE seq gap、Provider unknown、缩略图/代理失败、Projection 延迟、CreationSession 提交中断和 WebAV 不可用。
- 权限：跨 owner Project/Artifact/Resource、归档项目 mutation、Screenplay 模板泄漏、签名 URL 重放、伪造 Run/Attempt/lineage 和撤权竞态。
- 并发/幂等：两个标签页纯 layout/metadata 编辑、重复 check-and-run、重复模板实例化、promotion response-loss、重复 RunEvent 和旧 execution epoch 晚到。
- 迁移：空库升级、当前 Alembic head 升级、legacy fixture/backfill、强制 session 重新登录、应用回退和 canonical hash/ID 不变。
- 前端：Public/Product/Project/Canvas 四层 layout、新旧 URL replace redirect、前进后退/刷新、AbortController、normalized cache、两主题视觉回归、axe、键盘和读屏。
- 后端：PostgreSQL integration、JSON Schema/Pydantic/TypeScript contract、事务故障注入、outbox/replay、索引删除后投影重建和 SafeError 不泄漏。
- 核心 E2E 不得依赖 `page.evaluate` 预造业务事实，也不得 mock 生产不存在的 API；真实 Provider smoke 继续遵守显式 opt-in 与成本 Gate。

## 15. 交付与回退

- S0 合同冻结：新增本 PRD并同步 Master、Tracker、开发顺序及受影响 PRD；冻结 Project link、metadata CAS、event merge、promotion、CreationSession 和兼容迁移合同。
- S1 平台完整性：交付 durable session、Project links、Workflow metadata/full CAS、idempotency、RunEvent、node output binding、Resource type registry 和 legacy backfill。
- S2 产品壳与设计系统：交付 LensFlow 品牌、semantic tokens、四层 layout、新路由与旧路由兼容；NFR、a11y 和性能从本切片起成为每个切片的 Definition of Done。
- S3 私人 Workflow/CanvasShell：交付命名、检索、克隆、显式项目归属、全屏画布和 check-and-run command。
- S4 Run/Task/Artifact Inspector：交付运行列表、事件恢复、历史节点深链、RunDock、HumanTask 和真实 typed output preview。
- S5 创作资产：交付全局/项目资产投影、手工集合、promotion、新/已有 Resource Revision 和固定引用。
- S6 引导模板：交付 `/create` 目标矩阵、模板预览、依赖/成本预检、CreationSession、新/现有项目实例化和无画布运行。
- S7 World/OC/Screenplay：交付详情、版本、diff、固定引用、从资产开始创作和 Screenplay 私有阻断。
- S8 镜头/3D/Timeline：在相应 MED 依赖关闭后交付镜头工作台、Three.js 导演台 lazy chunk 和 V0/V1 Core 轻量时间线；WebAV 导出仍关闭到 V1.5。
- 每个切片必须具备迁移、自动测试、浏览器 E2E、监控和 feature flag；不能以已有页面或 CRUD 代替对应 AC 证据。
- 回退优先关闭 feature flag 和回退应用，保留新表、新列和不可变证据；不得删除 ArtifactVersion、ResourceRevision、WorkflowRevision、RunEvent 或审计记录。

## 16. 已决策事项与开放问题

已决策：

- 产品用户可见名称为 LensFlow；历史 TF ID、旧 schema 和审计证据保持兼容。
- 创作资产库和项目工作室是正交视图；画布在浏览心智上与 World/OC/Screenplay 平级，但 canonical 类型不合并。
- 首发只交付创作闭环，社区导航在无真实内容时隐藏。
- 资产首版使用手工集合、标签和筛选，不建设目录树。
- Screenplay 完全私有，不能通过 CreativeWork 或 Template 绕过。
- 新 Workflow 显式选择项目；遗留无关联对象不创建虚假项目。
- 专业画布提供“检查并运行新版本”，失败状态按保存、编译、激活和运行分别解释。
- 移动端只运行已激活 Revision，从已有候选进行有限替换。
- Flow-to-File 是内部视觉原则；GSAP、Three.js 和 WebAV 按功能 lazy load，Anime.js 不进入产品基础。
- WebAV 有限导出继续属于 V1.5，不提前作为 V1 Core 产品承诺。

开放问题：无。任何改变 Project/Workflow 基数、Screenplay 私有边界、版本状态族、WebAV 里程碑或社区开放范围的提案必须先走 TF-GOV-001 变更控制。
