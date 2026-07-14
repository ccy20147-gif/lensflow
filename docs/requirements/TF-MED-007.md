# TF-MED-007 电影语言、摄影配置与连续性检查

## 1. 元数据

- ID：TF-MED-007
- 标题：电影语言、摄影配置与连续性检查
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：分镜控制工作台
- 直接依赖：TF-MED-003、TF-MED-005、TF-MED-006
- 责任域：摄影规则/分镜产品
- 个人 DRI：待指派

## 2. 背景与问题

“电影感”或“大导演风格”不能成为不可测提示词。镜头控制需要把叙事意图转成景别、角度、焦段、构图、机位、运动、灯位、色温、轴线和连续性等可检查配置，同时保留创作者的语言表达。

连续性错误常跨镜头出现，单镜模型评分无法发现轴线跳跃、视线错位、屏幕方向反转或灯光突变。

## 3. 目标与非目标

- 目标：V1 Core P0 交付 coverage/cut、180 度轴线、视线、屏幕方向、动作衔接和基础摄影/灯光连续性检查。
- 目标：以渐进能力提供中性命名、可测量的电影语法/摄影配置预设，并允许用户查看和调整具体参数；高级风格解析、推荐和专家预设不阻塞 Core 验收。
- 目标：用 TF-QLT-001 固定镜头任务验证规则准确率、误报率和真实输出的语法遵循度。
- 非目标：默认不以未授权导演姓名命名预设或承诺复制其风格。
- 非目标：检查器只给证据与修复建议，不自动改写冻结 Revision。

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可选择语法配置、覆盖参数、接受/驳回建议、运行检查和保存草稿。
- 非 owner 不获得项目报告读取、规则豁免或覆盖能力；平台审核员只按审核授权读取必要证据。
- 平台摄影规则管理员发布版本化规则集；普通用户不能修改全局规则或伪造通过结果。
- 使用人物姓名、作品风格参考或外部摄影资料时遵循 TF-SEC-001 与授权规则。

## 5. 用户场景与主流程

1. 用户先在 V1 Core 检查 ShotPlan 的 sequence/coverage/计划 CutRelation；启用高级摄影建议后，可为 project、sequence、coverage 或 shot 选择叙事意图，如压迫、亲密、失衡、客观观察或动势增强。
2. 系统建议中性摄影配置：景别序列、角度/机高、filmback/FOV、CompositionIntent、世界空间 StagingGeometrySpec、按机位派生的 ScreenGeometryObservation、组合运镜、光比/色温、基础曝光/环境光、画幅保护和剪辑连续性规则。
3. 用户检查每个建议在 ShotSpec 抽象意图、Beat 时间事件和 DirectorScene 精确参数中的唯一落点，以及 ControlLayer 引用和 provider 编译结果，并可逐项覆盖。
4. 连续性检查器读取固定 ShotPlan/ShotSpec/DirectorScene Revision 以及时间线的实际 EditDecision（若存在），输出问题、证据帧和严重性，并区分计划关系与实际剪辑关系。
5. 用户在草稿中修复、记录创作性豁免或保持原方案；再次检查后冻结新 Revision。
6. provider 编译报告说明哪些摄影配置被原生应用、转换、降级或忽略。

## 6. 功能需求

- FR-1：摄影配置至少覆盖 shot size/subject coverage、camera angle/height、filmback/sensor、focal length/FOV/squeeze、capture/delivery canvas 与 protection、结构化 CompositionIntent、StagingGeometrySpec、派生 ScreenGeometryObservation、camera framing constraint/motion、blocking、exposure、environment lighting、lighting/look 和 edit intent；相同景别不得被错误等同于固定焦段。
- FR-2：内置预设使用可描述的中性名称，如“低位广角压迫”“长焦观察”“稳定正反打”，不默认使用在世导演姓名。
- FR-3：自然语言风格输入必须形成可组合 `CinematicStyleStack`；每层固定 layer_order、typed scope_ref、domain、merge_policy、provenance/confidence，参数覆盖使用带 schema/path/value/unit/merge operator 的 CinematicParameterOverride。project、sequence、coverage、shot 逐级叠加时窄作用域只覆盖明确参数；无法解析或缺乏策展证据/授权参考的导演姓名与形容词只保留为 reference，不得伪装成已应用摄影配置。高级自动解析/推荐按渐进开关交付。
- FR-4：V1 Core 必须以 ShotSequenceGroup、同一 StoryBeatRef 下多个可剪辑 Shot 的 CoverageGroup 和计划 CutRelation 检查 180 度轴线、30 度规则、视线匹配、屏幕方向、match on action、头部/引导空间、景别跳变和相机路径突变；这些关系不得只存于单 ShotSpec。
- FR-5：灯光检查至少覆盖 `LightEmitterSpec` 的物理发光参数、`EnvironmentLightingSpec` 的 sun/sky/ambient/time-of-day、`LightingRoleAssignment` 的 key/fill/rim/practical 职责、可选 `LightModifierSpec`、`ExposureSpec` 的 shutter/exposure-index/ND/compensation/white-balance、key direction、key/fill ratio、temperature/tint、softness/beam、motivated source、主色、曝光与 look 连续性；灯具、曝光、白平衡和最终 Look 禁止混为单一“氛围”字段。
- FR-6：连续性规则以版本化 `CinematicGrammarProfile` 和 `ContinuityRuleSet` 表达，报告固定规则版本。
- FR-7：每个 finding 包含 rule_id、severity、affected shot refs、measured values、evidence refs、建议和可记录豁免原因。
- FR-8：跨轴、跳切或灯光突变可被创作性豁免，但必须由有权限用户显式确认，不能由模型自动忽略。
- FR-9：抽象叙事/摄影意图只写 ShotSpecDraft，时间事件只写 Beat，精确 transform/path/light 只写 DirectorScene ResourceDraft，camera/lighting ControlLayer 只引用固定来源；检查器不能直接修改 Run、Revision 或选中输出。
- FR-10：镜头运动正交表达 rig_mode、translation_path、orientation_track、optical_track、各轨 interpolation、CameraSpeedProfile/timing_bindings、CameraFramingConstraint 与 MotionQualitySpec；path_t 只描述空间进度，实际时间由 CameraTimingBinding 引用 ShotTemporalAnchorRef。pan/tilt 是 orientation，dolly/truck/crane 是 translation，zoom/rack-focus 是 optical，handheld/follow/dolly-zoom 是组合结果；最终能力由 provider 编译逐 fragment_path 裁决。
- FR-11：展示视角允许从叙事意图到测量参数、控制层、provider 结果逐级追踪。
- FR-12：TF-QLT-001 对 V1 Core 基础连续性规则设置阻断验收阈值；高级风格推荐单独检测 precision/recall、人工一致率、输出遵循度和交互可理解性，不得以其未交付阻塞基础检查上线。
- FR-13：计划 `CutRelation` 只属于 ShotPlanRevision；时间线排序、替换、切点与转场形成实际 `EditDecision`，不得反写或冒充计划关系。实际编辑变化后，依赖旧 CutRelation/旧 EditDecision 的 ContinuityReport 必须标记 stale 并重新检查。
- FR-14：V1 Core 的速度控制使用版本化 speed profile、基础 easing 和 Beat/ShotTemporalAnchorRef 绑定；完整速度/加速度曲线、灯光 cue、iris/focus 高级轨、LUT/CDL 与精确 Animatic 后置 V1.5，关闭时不影响基础镜头规则验收。
- FR-15：导演姓名或作品风格只是带 provenance/confidence 的 reference input；只有平台策展证据或用户提供的已授权作品级参考存在时，系统才可把可解析部分拆为 framing、optics、movement、blocking、lighting、look、edit_rhythm 参数，否则全部保留为 reference。无法解析部分不得显示为已应用，官方以姓名命名的策展配置必须具备授权与作品级证据。

## 7. 交互与展示

- 用户先看到镜头意图和严重问题，展开后按“创作意图 -> 空间/参数实现 -> Provider 实际执行”查看毫米、FOV、角度、色温、光比、转换损失和规则依据。
- 摄影配置使用数值输入、滑杆、镜头/相机选项、构图目标区、轨迹预览和灯位/光比叠加；不能只提供自由文本框。
- sequence 视图用轴线、视线、方向和灯位小图叠加到相邻镜头，不遮挡主体缩略图。
- finding 可按严重度、规则族、shot、未处理/豁免筛选；每条可定位 3D 相机或输出帧。
- 风格标签旁显示作用域、叠加优先级、“可测参数”与“参考描述”来源，明确 provider 对每个 fragment 是原生应用、转换、提示词近似、带警告忽略还是阻断。

## 8. 数据、类型与公共接口

- `CinematicGrammarProfile` 为 ResourceRevision 专用内容：intent_tags、composition/framing rules、lens_fov_ranges、angle_height_rules、staging/screen-observation rules、movement/framing-constraint patterns、blocking_patterns、exposure/environment/lighting/look refs、coverage_and_cut_rules、duration_rhythm、strictness、allowed_exceptions、provenance/evidence/confidence。`CinematicStyleStack` 的每个 layer 含 layer_id/order、scope + typed scope_ref、domain、intent tags、typed parameter overrides、reference refs、merge policy、provenance/confidence；冲突按 layer order、scope specificity 和 merge operator 确定性解析。
- `ShotSequenceGroup` 固定 scene/ordered shot revision refs、master axis、screen direction、grammar/color/framing refs；`CoverageGroup` 固定 story_beat_ref、coverage objective/required roles 和多个独立可剪辑 member shot revision refs；计划 `CutRelation` 固定 from/to shot revision、可选 ShotTemporalAnchorRef、temporal relation、match intent、transition、axis transition 和 screen-direction policy。
- `ContinuityRuleSet` 为版本化 Artifact/managed preset，含 rule_id、inputs、thresholds、severity、evidence_method 和 repair_templates。
- `ContinuityReport` 为 ArtifactVersion，含 source_revision_refs、plan_cut_relation_refs、timeline_edit_decision_refs、rule_set_ref、findings、waivers、summary_metrics 和 stale_reason。
- 参数严格按权威层落到 ShotSpec 的 CompositionIntent/抽象意图、Beat 时间事件或 DirectorSceneRevision 的精确 cameras/framing constraints/motions/exposure/environment/light emitters/role assignments/modifiers；ControlLayer 的 target_scope 必填，target_anchor_ref/source_selector 按 layer schema 条件必填，提供时分别使用 ShotTemporalAnchorRef 和类型化 ControlSourceSelector，不复制参数或 Beat 时间，不另建镜头真相。
- Timeline `EditDecision` 表达实际相邻 clip、timeline/cut ranges、transition 与来源 shot refs；它与 ShotPlan CutRelation 分属不同 Revision，检查器仅关联比较。
- `ContinuityWaiver` 记录 finding_id、actor、reason、scope、created_at 和 base_revision；只随新草稿/报告生效。
- provider 最终支持情况沿用 ProviderCompilationReport，不在语法配置中伪造 applied 状态。

## 9. 状态机与业务规则

- finding 状态：open -> acknowledged -> resolved | waived；ShotPlan/ShotSpec/DirectorScene 或 Timeline EditDecision 任一来源变化后原报告变 stale，不自动延续 resolved。
- severe 轴线/权限类规则可由工作流策略设为 Human Gate；豁免需相应权限并完整审计。
- 规则执行对相同 source refs/rule revision 幂等；缓存命中仍生成调用上下文审计。
- 单镜参数不完整时报告 unknown，不将缺失值当作通过；提示补全或可计算来源。
- 冻结 Revision 后的检查结果不可回写；修复必定产生新草稿内容和新 Revision。

## 10. 失败、降级与恢复

- 缺少 3D 数据时仍执行基于 ShotSpec/输出帧的规则，并明确证据级别与不可检查项。
- 模型视觉检测不可用时保留确定性参数规则，标记视觉 finding 未评估，不返回假通过。
- provider 不支持具体焦段/轨迹时由 TF-MED-006 报告降级；语法层保留原始意图供比较。
- 检查中断后按 source/rule fingerprint 恢复，已完成规则不重复计费；取消保留部分报告但不可标通过。
- 规则版本故障可回退上一 active revision，新报告必须记录实际版本。

## 11. 安全、隐私、内容与授权

- 默认预设不使用未授权姓名、片名、商标视觉或“完全复制”承诺；用户输入仍经过内容/权利 Gate。
- 真人镜头分析只处理已同意范围内的必要帧；人脸特征向量按 TF-SEC-001/TF-NFR-002 限制保留。
- 未成年人、敏感地点或隐私画面不得为连续性评测扩大 provider 披露范围。
- 报告证据帧继承来源访问控制，公开作品不得自动公开内部构图或 3D 控制资料。

## 12. 观测与运营

- 事件：grammar_profile_applied、continuity_check_started/completed、finding_created/resolved/waived、profile_compiled。
- 指标：各规则触发率、precision/recall、人工驳回率、豁免率、修复后通过率、每 sequence 检查时延。
- 规则质量按 TF-QLT-001 固定集和摄影专家双评记录，阈值变更必须关联新规则 revision。
- 支持信息含 profile/rule/report revision、shot refs、measured values、provider report 和 correlation_id。

## 13. 验收标准

- AC-1：Given 素模正反打场景，When 一台相机跨越 180 度轴线，Then 报告定位机位与测量角度；移回后新报告通过。
- AC-2：Given “低位广角压迫”配置，When 应用到 Shot 草稿和 DirectorScene，Then 展示并保存唯一落点的角度、机高、filmback/FOV、构图和灯光参数，并追踪 provider 实际转换。
- AC-3：Given 用户输入未授权导演姓名，When 生成建议，Then 系统不作复制承诺，并转换为可检查参数或提示权利限制。
- AC-4：Given 创作性跨轴，When 有权限用户豁免，Then 记录原因/范围/版本；源修订变化后需重新评估。
- AC-5：Given provider 不支持精确轨迹，When 编译，Then 原意图保留且各 fragment 按策略形成 blocked/degraded/ignored_with_warning 与 application_mode，不显示已原生应用。
- AC-6：Given TF-QLT-001 Core 连续性集，When 回归，Then coverage/cut/轴线/视线/方向/动作衔接基础规则的 precision/recall 与专家一致率达到批准阈值；高级风格推荐关闭时这些验收仍完整通过。
- AC-7：Given 计划 CutRelation 已通过检查，When 时间线重排或改变切点产生新 EditDecision，Then 旧 ContinuityReport 标记 stale，新报告分别显示计划关系与实际剪辑关系且不反写 ShotPlan。
- AC-8：Given 两个镜头具有相同主体覆盖但分别使用近距离广角与远距离长焦，When 检查，Then 系统保留不同透视/空间压缩证据，不把“同景别”误判为“同焦段”，并分别追踪 Provider 转换。
- AC-9：Given 用户输入导演姓名和三张已授权参考帧，When 解析风格，Then 可测参数按 domain/作用域显示 provenance/confidence，未解析描述保持 reference，未授权姓名不成为官方复刻承诺。

## 14. 测试场景

- 正常：同一 StoryBeat 多镜 coverage、正反打、建立镜头、计划 CutRelation、实际 EditDecision、动作衔接、CompositionIntent/StagingGeometry/ScreenGeometryObservation、filmback/FOV 序列、exposure/environment/emitter/role 灯光、组合轨迹与创作性豁免。
- 边界：单镜、51 镜头、无 3D、极端焦段、零移动、复杂组合路径、日夜转场和未知参数。
- 失败：规则引擎超时、视觉检测不可用、证据帧丢失、规则版本退役、provider 降级错误。
- 权限：非 owner 豁免、跨项目报告、私有证据公开、未授权风格名、平台审核员越界和真人分析撤回。
- 并发/恢复：检查与草稿同时变化、重复执行、规则热更新、取消恢复、服务重启。

## 15. 交付与回退

- V1 Core 基础 coverage/cut/轴线连续性作为 P0 默认交付；高级风格解析、推荐、视觉检测和专家预设使用独立渐进功能开关，关闭时不影响基础参数规则和历史报告读取。
- profile/rule schema 版本化；旧报告保持可解释，新规则不追溯改写旧 finding。
- 发布证据包括摄影专家任务集、TF-QLT-001 报告、3D 轴线 E2E、provider 编译差异和权限测试。
- 回退上一规则 revision 后新检查记录实际版本，禁止复用已失配缓存。

## 16. 已决策事项与开放问题

- 已决策：基础 coverage/cut/轴线连续性是 V1 Core P0；导演风格以可测量镜头语法/摄影配置表达并渐进交付，默认不用未授权姓名作结果承诺。
- 已决策：V1 Core 交付结构化构图、屏幕几何、基础曝光/环境灯光和版本化速度预设；完整曲线、灯光 cue、精确 Animatic 与高级色彩管理后置 V1.5。
- 已决策：计划 CutRelation 与时间线实际 EditDecision 分离，任何实际剪辑变化都会使依赖旧关系的连续性报告 stale。
- 已决策：连续性建议可豁免但不可静默忽略，且修复只进入新草稿/Revision。
- 开放问题：摄影专家评测后冻结首批规则阈值与中性预设名称，不改变公共合同。
