# TF-MED-012 时间线、预览、片段包与有限导出

## 1. 元数据

- ID：TF-MED-012
- 标题：时间线、预览、片段包与有限导出
- 状态：defined
- 版本：V0 -> V1 Core -> V1.5
- 优先级：P0
- 全局位置：成片工作台
- 直接依赖：V0：TF-MED-009、TF-OPS-003、TF-NFR-001；V1 Core 增加 TF-MED-010、TF-MED-011；V1.5 无新增强制需求依赖
- 责任域：成片产品/前端媒体
- 个人 DRI：待指派

## 2. 背景与问题

生成的图片、视频、对白、音乐、音效和字幕只有进入可恢复时间线，创作者才能验证顺序、时长和替换影响。V0 先交付 timeline JSON 与图片/可用代理预览；V1 Core 交付真实视频代理、基础音频/字幕轨和单镜替换；V1.5 才承诺 WebAV 在受限设备/时长/格式内导出。

浏览器时间线不是 DAG 或运行时，前端预览状态也不能成为成片真相。每个片段必须引用不可变 ArtifactVersion，替换产生新组合修订；ShotPlan 的 `CutRelation` 只表达计划切镜意图，时间线的实际排序、切点和转场必须另存 `EditDecision`。

## 3. 目标与非目标

- 目标：V0 按 ShotPlan 顺序形成可恢复组合；V1 Core 组装真实视频代理、基础音频和字幕轨，支持播放、单镜替换、stale 传播、保存恢复和交付包。
- 目标：V1.5 在明确兼容矩阵内用 WebAV 完成有限短片导出，并提供失败诊断和片段包回退。
- 目标：用 TF-QLT-001 固定时间线评测 A/V 同步、黑帧、顺序、替换、字幕和预览/导出一致性。
- 非目标：WebAV 不作为 DAG 或后端运行时，不承诺浏览器稳定编码长片、长剧集或任意 codec。
- 非目标：不建设完整 NLE、多机位专业剪辑、调色、插件或电影级最终混音。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V0 | timeline JSON、图片/已有代理预览、排序、基础 visual clip 替换、保存恢复和片段包 | 固定 ArtifactRef/ResourceRef；后续切片原样读取 | 刷新恢复、非空预览、替换与 package manifest E2E |
| V1 Core | 真实视频代理播放，基础 dialogue/narration/music/sfx/subtitle 轨，单镜替换及 A/V/字幕 stale 传播 | V0 clip/track ID 保持稳定；新增媒体字段可选 | 视频代理、基础音轨/字幕、单镜替换和同步恢复 E2E |
| V1.5 | 有限转场、WebAV 受限导出与设备预检 | V0/V1 Core composition 原样读取；导出字段独立版本化 | 目标浏览器 1080p 短片导出、同步与失败回退 E2E |

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可组装、调整、替换、预览、创建修订和导出。
- 非 owner 不获得项目时间线读取、播放、替换或导出能力；平台审核员只按审核授权读取必要证据。
- 导出者需对每个源修订拥有当前 reference/derivative/commercial 权限及内容安全通过状态。
- 私有片段和 waveform/poster 使用短期签名访问；浏览器缓存不得跨账户共享。
- 移动端支持同一 owner 播放、私人批注和有限片段选择；批注不形成协作审阅语义，也不提供完整时间线编辑或 WebAV 导出承诺。

## 5. 用户场景与主流程

1. 用户从固定 ShotPlanRevision 创建时间线草稿，系统按 ordered shots 加入选中图片/视频占位。
2. V0 用户只使用可用图片/代理与时长；V1 Core 用户添加/对齐基础对白、旁白、音乐、SFX 和字幕轨。
3. 播放器解码代理媒体并检查缺失、stale、黑帧、时长、授权、安全和设备能力。
4. 用户替换单镜片段、调整 in/out、顺序和转场；系统将实际相邻关系与切点保存为 EditDecision，显示相对 ShotPlan CutRelation 的偏差，并使依赖旧剪辑关系的连续性报告 stale。
5. V0/V1 Core 导出 timeline JSON 与片段包；V1.5 通过预检后启动 WebAV 短片编码。
6. 导出结果、manifest、实际参数、失败/警告与来源署名保存为不可变 ArtifactVersion。

## 6. 功能需求

- FR-1：V0 至少支持 visual 轨；V1 Core 增加 dialogue、narration、music、sfx、subtitle 基础轨，所有切片使用稳定 track_id/clip_id。
- FR-2：每个 clip 固定 media ArtifactRef、source Shot/track ref、timeline start/end、source in/out、layer/order 和 enabled。
- FR-3：V0 可用图片按 duration_ms 预览并播放已有可解码代理；V1 Core 必须为选中视频生成/使用真实代理并支持连续播放；缺失媒体显示明确状态而非黑色成功片段。
- FR-4：排序默认来自 ShotPlanRevision，但计划 CutRelation 仅作为参考；手工重排、实际相邻 clip、切点与转场写入时间线草稿的 `EditDecision`，不得反向改写或冒充 ShotPlan CutRelation。
- FR-5：V0 支持基础 visual clip 替换；V1 Core 单镜替换按 clip_id/shot_id 保持 timing policy，显示时长差、关联 A/V/字幕与实际 EditDecision 影响并传播 stale 下游；重排、替换或改变切点后，依赖旧关系的 ContinuityReport 必须 stale。
- FR-6：时间线保存写新 ArtifactVersion，并通过 ResourceDraft/Revision 固定组合；已导出组合不可原地修改。
- FR-7：V0 播放器支持播放/暂停、seek 和错误定位；V1 Core 增加帧步进、音量/静音、字幕开关和 stale 定位。
- FR-8：V0 片段包含 timeline JSON、固定媒体 manifest、checksums、授权/署名摘要和缺失项，不含 secret/签名 URL。
- FR-9：V1.5 WebAV 预检浏览器、WASM/codec、内存、磁盘配额、分辨率、时长和预计导出大小。
- FR-10：V1.5 初始导出限定最高 1920x1080、30 fps、总时长不超过 5 分钟、已批准 H.264/AAC 或兼容 fallback preset。
- FR-11：导出输出保存 actual codec/container/dimensions/fps/duration/checksum、来源 manifest 和安全/授权决策。
- FR-12：TF-QLT-001 评测顺序、帧时长、A/V/字幕同步、黑帧/静音、预览/导出差异和交互视觉回归。

## 7. 交互与展示

- 工作台使用稳定轨道高度、时间尺、playhead、缩放控件和图标工具；片段标签溢出时换行/省略，不挤压轨道。
- 预览是主内容区域而非卡片；播放器明确显示代理/原片、分辨率、当前时间和性能降级。
- stale、缺失、无权、未审查和解码失败分别展示，不能统一成“加载失败”。
- 导出对话框只展示真实支持的 preset、预计耗时/大小、设备风险和片段包替代方案。
- 移动端为同一 owner 提供单列片段、播放器和私人批注，可选择已有候选替换，不展示无法操作的精细控件；批注不形成协作审阅语义，也不产生独立只读项目角色。

## 8. 数据、类型与公共接口

- `TimelineComposition` 为 ArtifactVersion 内容：timebase、duration_ms、tracks、markers、edit_decisions、source_revision_refs、preview_policy；transition 归具体 EditDecision，不以计划 CutRelation 作为实际播放真相。
- `TimelineClip` 含 clip_id、track_id、media_ref、source_ref、timeline_range、source_range、transform/gain 可选值和 stale_reason。
- `EditDecision` 含 edit_decision_id、from_clip_id/to_clip_id、from/to source shot refs、timeline/cut ranges、transition 和可选 planned_cut_relation_ref；它只属于 Timeline Revision，计划 CutRelation 仍只属于 ShotPlanRevision。
- 可编辑时间线使用 ResourceDraft 保存 composition Artifact；冻结为带专用 schema 的 Timeline ResourceRevision，不另建可变版本源。
- `ClipPackageManifest` 含 composition_ref、media entries/checksums、schema refs、attribution、entitlement evidence、missing_items。
- `ExportRecord` 为 ArtifactVersion，含 input composition revision、preset revision、device capability、actual media metadata、logs ref 和 output ref。
- CreativeWorkRevision 只引用固定导出/媒体 refs，不读取时间线 latest。

## 9. 状态机与业务规则

- 导出任务随 RunStatus/NodeRunStatus；前端 encoder phase 只是事件明细，不能直接标 completed。
- 草稿 patch 按 client_request_id 幂等并以 draft_version compare-and-swap；片段并发冲突返回三方 diff。
- 任何源媒体修订/授权变化只标时间线草稿 stale；EditDecision 变化使依赖旧计划/实际关系的 ContinuityReport stale；历史 Timeline/Export/CreativeWork Revision 不改写。
- 相同 composition/preset/device fingerprint 可提示已有导出，但新导出仍重算当前 entitlement。
- 导出 completed 仅在文件 checksum、可解码探测、安全与 manifest 全部成功后成立。

## 10. 失败、降级与恢复

- 解码不支持时尝试已批准代理/转码 Artifact；不可用则明确阻断对应 clip，不用空白帧冒充。
- WebAV 不可用、内存不足、页面离开或超出上限时停止受限导出，保留 composition 并提供片段包。
- 导出中断可在实现支持的 checkpoint 继续；不支持续传时明确重新开始和成本/时间，不伪造进度。
- 页面刷新从持久 composition 恢复；未保存本地操作在离开前警告，但不能覆盖服务端版本。
- 部分音轨缺失可按策略静音降级并记录明确的非阻断诊断；权限/安全失败始终阻断。

## 11. 安全、隐私、内容与授权

- 预览、片段包和导出分别重算当前 EntitlementDecision；历史 GrantSnapshot 只用于审计。
- 真人肖像/声音、未成年人、冒充、音乐版权和未授权素材在最终导出再次执行 TF-SEC-001 Gate。
- 片段包不含 provider secret、CredentialBinding、永久公开 URL 或敏感人脸/声纹中间数据。
- WebAV 临时文件存浏览器私有作用域并在成功/取消后清理；共享设备提示下载与缓存风险。

## 12. 观测与运营

- 事件：timeline_created/patched/conflicted、clip_replaced、preview_started/failed、export_preflight_failed、export_completed/failed。
- 指标：首帧时延、seek P95、掉帧率、解码失败率、stale 数、导出实时倍率、失败率、内存峰值和包完整率。
- 非空预览探针要求成功解码、videoWidth/height > 0，首 2 秒至少 3 个采样帧且非全透明/全常量像素。
- 质量看板引用 TF-QLT-001；支持信息含 composition/export ref、device/codec、clip id、checksum 和 correlation_id。

## 13. 验收标准

- AC-1：Given V0 固定 ShotPlan 与图片/代理，When 打开并刷新，Then 顺序、时长、选择和 timeline JSON 完整恢复。
- AC-2：Given 可解码媒体，When 播放前 2 秒，Then 达到非空预览探针，且无片段以空白占位标记成功。
- AC-3：Given V1 Core 第 17 镜视频替换且时长变化，When 保存，Then 只更新对应 clip 与受影响 EditDecision 草稿，明确关联字幕/音频/连续性 stale，代理可播放、ShotPlan CutRelation 与历史修订均不变。
- AC-4：Given V1.5 合规 1080p/30fps/3 分钟项目，When 在批准浏览器导出，Then 文件可解码、checksum 匹配、A/V 同步达 TF-QLT-001 阈值。
- AC-5：Given 设备不支持 codec 或项目超过 5 分钟，When 预检，Then WebAV 导出阻断并可生成完整片段包。
- AC-6：Given 任一素材撤权，When 新导出，Then 服务端阻断且不破坏此前合法 ExportRecord 的隔离证据。

## 14. 测试场景

- 正常：V0 图片时间线；V1 Core 视频代理、基础音频/字幕、单镜替换、计划 CutRelation 对照、实际 EditDecision、保存和片段包；V1.5 WebAV 导出。
- 边界：1/51 镜头、100 缩略图、5 分钟上限、无音频、多个字幕语言、纵向 1080p、零长度 clip。
- 失败：codec 不支持、Blob 丢失、内存不足、页面离开、checksum 错、黑帧、权限过期。
- 权限：跨项目 clip、非 owner 导出、平台审核员越界替换、撤权音乐/真人声音、私有包泄漏、未成年人内容。
- 并发/恢复：双端替换、重复导出、刷新/断网、编码取消、服务重启和 stale 竞态。

## 15. 交付与回退

- V0 组合、V1 Core 视频代理/基础音轨/字幕/单镜替换、V1.5 WebAV 各自功能开关；关闭 WebAV 始终保留片段包。
- composition schema 向前兼容；旧 reader 对未知 track/transition 只读并阻止有损保存。
- 发布证据包括浏览器/设备矩阵、非空像素检查、TF-QLT-001 同步报告、刷新恢复、权限和导出 E2E。
- 回退不能删除 Timeline/Export Artifact；未完成导出安全失败，项目仍可编辑与打包。

## 16. 已决策事项与开放问题

- 已决策：WebAV 只做浏览器预览与受限短片导出，不承担 DAG、后端运行时或长片稳定编码。
- 已决策：时间线引用不可变媒体，片段替换产生新组合修订；计划 CutRelation 与实际 EditDecision 分离，均不覆盖历史成片或反向改写 ShotPlan。
- 开放问题：Foundation/V1.5 设备测试后冻结批准浏览器、codec preset 和内存预检阈值；5 分钟/1080p 上限只能收紧发布，放宽需新评审。
