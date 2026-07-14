# TF-MED-005 轻量 3D 导演台

## 1. 元数据

- ID：TF-MED-005
- 标题：轻量 3D 导演台
- 状态：defined
- 版本：V1 Core
- 优先级：P0
- 全局位置：分镜控制工作台
- 直接依赖：TF-MED-003、TF-NFR-001
- 责任域：3D/分镜前端
- 个人 DRI：待指派

## 2. 背景与问题

二维提示词难以稳定表达多人站位、遮挡、视线、180 度轴线、机位、焦段、轨迹和基础光位。创作者需要一个轻量空间预演工具，把导演意图导出为可供图片/视频 provider 使用的控制稿。

该能力是静态关节素模导演台，不是 Blender/Unreal 级 DCC，也不承担完整建模、材质、骨骼动画、物理或角色绑定。

## 3. 目标与非目标

- 目标：用素模、场景代理和道具完成站位、静态姿势、相机、焦段、基础灯光及相机轨迹预演。
- 目标：按 sequence、coverage 或 shot 作用域序列化为固定 `DirectorSceneRevision`，并导出带 `ControlFrameManifest` 的 clay/pose/depth/normal/segmentation/edge 等控制 Artifact 及 camera/lighting metadata。
- 目标：支持轴线、视线、屏幕方向和遮挡检查，并由 TF-QLT-001 固定任务验证空间/镜头遵循度。
- 非目标：不提供网格建模、材质、骨骼动画编辑、动作曲线、布料、毛发、物理或最终渲染。
- 非目标：移动端不提供完整场景搭建，只查看和有限调整。

## 4. 用户与权限

- V1 Core 私有项目只有 owner；owner 可创建场景草稿、放置对象、保存姿势/机位、请求导出控制稿和冻结修订。
- 移动端是同一 owner 的查看与有限调整界面；非 owner 不获得项目场景访问或生成能力。
- 平台审核员仅按审核授权读取必要截图/参数，不能上传模型、修改对象、导出控制或冻结修订。
- 跨 owner 角色/场景/道具进入场景时固定 revision 和 GrantSnapshot，新导出时重算授权。

## 5. 用户场景与主流程

1. 用户从 sequence、CoverageGroup 或单个 ShotSpec 草稿打开/复用对应作用域的导演台，选择角色、场景和道具固定修订；单镜入口只聚焦选中的 camera/actor/pose，不复制整套场景。
2. 系统载入场景单位与坐标系，用户放置静态关节素模、应用姿势库，并把离散姿势、root transform、视线与 blocking 绑定到 actor instance 和带 shot 命名空间的 ShotTemporalAnchorRef；无 Beat 镜头的时间事件使用 start/end，default 仅用于不带具体时间的全镜静态 pose/framing/lighting。
3. 用户创建相机，设置机位、filmback、焦段、光圈/焦点、捕获与交付画幅保护框、屏幕空间构图约束和基础曝光，分别编辑 translation、orientation、optical、motion-quality 轨；灯光分别配置 emitter、环境光、摄影职责与 modifier。
4. 系统实时显示 180 度轴线、视线、屏幕方向、遮挡和取景警告。
5. 用户保存草稿并冻结 DirectorSceneRevision；导出时提交 `director_scene.export_controls` WorkbenchActionRequest，由 child run 先生成控制图、ControlFrameManifest、相机/灯光 metadata Artifact，再生成引用这些 item 的 DirectorSceneControlExport ArtifactVersion，不回写不可变 Scene Revision。
6. 工作台将 export ArtifactRef 作为 ShotSpec ControlLayer source_ref，并用必填 target_scope、按语义条件必填的 ShotTemporalAnchorRef，以及 `director_control_export` selector 的 item IDs 定位具体控制输出，交由 TF-MED-006 编译。

## 6. 功能需求

- FR-1：场景必须显式记录 units 与 coordinate_system，导入对象时执行单位转换并保存转换报告。
- FR-2：对象支持 actor、prop、scene proxy；每项固定资源修订、transform、可见性和语义标签。
- FR-3：actor 使用内置静态关节素模与姿势库，允许关节旋转、镜像姿势及按 ShotTemporalAnchorRef 保存 `ActorPoseBinding`/`BlockingKeyframe`；ActorPoseBinding 可使用 beat/start/end/default，BlockingKeyframe 作为时间事件只能使用 beat/start/end。锚点不复制 time_ms/normalized_time，不提供蒙皮、IK 动画曲线或连续骨骼动画编辑。
- FR-4：支持多人站位、朝向、视线目标、地面吸附、尺度参照、碰撞/穿插提示和遮挡预览。
- FR-5：相机至少支持 transform、filmback/sensor width-height、focal length、可计算 FOV、anamorphic squeeze、aperture、focus target/distance、capture canvas、delivery crop/protection、安全框和命名机位；CameraFramingConstraint 必须有 scene 内稳定 framing_constraint_id，并声明 capture|delivery canvas、`[0,1]` top-left 归一化区域、contain|center|cover 运算、coverage/headroom/lead-room、遮挡策略、容差和 path sampling policy，并记录 derived_from CompositionIntent。
- FR-6：`CameraMotionSpec` 将 rig_mode、translation_path、orientation_track、optical_track、timing_bindings 和 MotionQualitySpec 正交保存；三类轨分别声明插值，所有 path_t/速度 knots 在 0..1 内单调。CameraTimingBinding 只能使用 beat/start/end；V1 使用版本化 CameraSpeedProfile 和基础 easing，不提供任意曲线编辑器。程序化 handheld 必须固定 preset revision、seed 和 amount，确保保存刷新后可重放。
- FR-7：基础灯光以 `LightEmitterSpec` 保存物理发光体参数，以 `EnvironmentLightingSpec` 保存太阳/天空/环境光、单位/色彩空间、time-of-day、environment map 及 precedence，以带稳定 assignment_id 的 `LightingRoleAssignment` 保存 key/fill/rim/background/practical 等摄影职责，并可用 `LightModifierSpec` 表达 flag/diffusion/bounce/negative fill 等 modifier；ambient 归 Environment，不在 role 中重复，role、environment 与 emitter 禁止合并成单一记录，V1 不模拟完整 photometric/IES。
- FR-8：检查器覆盖 180 度轴线、视线匹配、屏幕运动方向、头部空间、引导空间、遮挡和越界。
- FR-9：导出至少包括 viewport clay、pose、depth、normal、segmentation、edge、mask、camera metadata 和 lighting metadata ArtifactRef；每个像素控制稿必须附 `ControlFrameManifest`，固定骨骼 convention、坐标系、单位、投影、near/far、分割标签、画布、裁切和 resize policy，并在组合前验证像素对齐。全部输出 item 先各自成为 ArtifactVersion，再由 DirectorSceneControlExport ArtifactVersion 固定 source scene revision、camera、temporal anchors、child run 与 item/manifest refs。
- FR-10：DirectorSceneRevision 使用 ResourceRevision 真相，target_scope 只能为 sequence/coverage/shot，并以 scope_refs 关联对应固定来源；精确 transform、静态姿势、世界空间 StagingGeometrySpec、camera/framing/path、ExposureSpec、environment/light 参数只在 DirectorScene 权威保存，单镜 ControlLayer 只引用和选择，不复制；Scene Revision 不保存事后产生的控制导出或屏幕观察引用。
- FR-11：桌面端提供完整编辑；移动端仅查看、切换机位、播放轨迹及有限调整对象位置/朝向和已批准姿势。
- FR-12：TF-QLT-001 固定场景任务评测站位、相机、轴线、控制图和交互回归，保存基准截图与数值差异。
- FR-13：`StagingGeometrySpec` 只表达世界空间 action axis、actor world side、gaze target、entry/exit 与 movement world direction；连续性检查必须按固定 scene revision、camera、ShotTemporalAnchorRef/path_t 和 capture/delivery canvas 计算不可变 `ScreenGeometryObservation`，画面左右/屏幕方向不得回写为 DirectorScene 权威。
- FR-14：`ExposureSpec` 的 shutter angle、exposure index、ND、曝光补偿和白平衡均为可选精确参数；V1 Provider 不支持时由编译器明确近似或降级，V1 Core 不承诺完整曝光仿真和 LUT/CDL 色彩管理。
- FR-15：ShotSpec 若直接选择 DirectorScene 内部控制，必须使用 `director_component` selector 及 `DirectorComponentRef[]`；每项固定 DirectorSceneRevision、component_kind、component_id，并按需固定 fragment_path、ShotTemporalAnchorRef 或 path_t_range。component_kind 至少拆分 camera、camera_motion、camera_framing_constraint、staging_geometry、exposure、environment_lighting、light_emitter、lighting_role_assignment、light_modifier、actor_instance、static_pose，禁止用笼统 light/actor/pose 或数组位置猜测对象。

## 7. 交互与展示

- Three.js 场景全幅呈现，工具栏使用熟悉图标、模式分段控件和属性检查器，不放入装饰预览卡。
- transform gizmo、相机视锥、轴线、视线、屏幕方向、构图目标区、轨迹和灯光范围可独立显示，默认避免视觉拥挤。
- 对象树按 actor/prop/scene/camera/light 分组；选择与视口双向同步且保持稳定尺寸。
- 警告直接锚定对象或相机并提供修复建议；红色不得是唯一辨识方式。
- 移动端进入明确的查看模式，有限调整控件与桌面高级工具分开，避免误导为完整编辑。

## 8. 数据、类型与公共接口

- `DirectorSceneRevision` 是带专用 schema 的 ResourceRevision，其内容严格包含 target_scope、scope_refs、units、coordinate_system、scene/actor/prop instances、static_poses、actor_pose_bindings、blocking_keyframes、staging_geometry_specs、cameras、camera_framing_constraints、camera_motions、exposure_specs、environment_lighting_specs、light_emitters、lighting_role_assignments 和 light_modifiers；不含事后导出的控制 Artifact 或屏幕观察引用。
- `ShotTemporalAnchorRef` 含 shot_resource_id、互斥的可选 shot_revision_ref/shot_candidate_ref、anchor_kind=beat|shot_start|shot_end|shot_default 和条件必填 beat_id；ResourceDraft 中由 EditContextSnapshot 解析，RevisionCandidate 可使用 expected_resource_type=ShotSpec 的 CandidateRevisionRef，最终 Revision/Artifact 必须固定 shot_revision_ref。start=0，end=duration_ms，default 不解析 concrete time。`ActorPoseBinding` 只含 actor_instance_id、target_anchor_ref、pose_id，`BlockingKeyframe` 只含 actor_instance_id、非 default target_anchor_ref、root_transform、可选 gaze_target 与 prop_contacts，均不存 Beat 时间或连续动画曲线。
- `StagingGeometrySpec` 含 staging_geometry_id、action_axis_world、actor_world_sides、gaze_targets、entry_exit_world_directions 和 movement_world_directions。`ScreenGeometryObservation` 为 ArtifactVersion 内容，固定 source scene revision、camera_id、可选 target_anchor_ref/path_t、canvas_kind/canvas，以及派生 subject screen side、gaze vector、entry/exit 与 movement screen direction。
- `CameraFramingConstraint` 含 scene 内唯一 framing_constraint_id、camera_id、可选 target_anchor_ref/path_t_range、canvas_kind、固定 `[0,1]` top-left coordinate space、subject_refs、target_screen_regions、target_coverage、fit_mode、headroom、lead_room、occlusion_policy、tolerance、sampling_policy/sample_count、priority 和 derived_from_composition_intent_ref。
- `CameraSpec` 含 camera_id、transform、sensor_width_mm、sensor_height_mm、focal_length_mm、derived_fov、squeeze_factor、aperture、focus_target/distance、capture_canvas、delivery_canvas/crop/protection 和 safe_frame；`ExposureSpec` 与 CameraSpec 分离保存 shutter/exposure/ND/white-balance 可选参数。
- `CameraMotionSpec` 含 camera_motion_id、camera_id、rig_mode、translation_path、orientation_track、optical_track、各轨 interpolation、timing_bindings 和 motion_quality_spec；CameraPathPoint 为 point_id/path_t/position，CameraOrientationKey 与 CameraOpticalKey 各自以 path_t 保存朝向/roll 或焦段/焦点/光圈。`CameraSpeedProfile` 固定 revision、单调 normalized knots、easing family 及可选 acceleration/jerk limit；`MotionQualitySpec` 固定 stabilization/smoothing 及可选 handheld preset revision/seed/amount。
- `LightEmitterSpec` 含 emitter_id/emitter_type、transform、intensity/value_unit、temperature/tint、size/shape、beam/spread/enabled；`EnvironmentLightingSpec` 单独表达 sun/sky/ambient、intensity unit、color space、temperature/tint、time-of-day、environment map 与 precedence policy；`LightingRoleAssignment` 含 scene 内唯一 assignment_id、非 ambient role、emitter_ids、motivated_source_ref；`LightModifierSpec` 含 modifier_id/modifier_type/transform、target_emitter_ids 与 parameters。
- 导出控制图为 ArtifactVersion，并逐项附 `ControlFrameManifest`（control_artifact_ref、source_director_scene_revision_ref、camera_id、target_anchor_ref、control_kind、coordinate_system、units、projection、near_far、canvas、crop、resize_policy 及可选 skeleton_convention/segmentation_label_map_ref）；`DirectorSceneControlExport` 是 ArtifactVersion 内容，含 source scene revision、export config、camera、anchors、child_run_id 和带 item_id/kind/artifact_ref/manifest_ref 的 items。ShotSpec 以 export ArtifactRef 为 source_ref，并用 `director_control_export` selector + export_item_ids 精确引用，不内嵌可变场景状态。
- `DirectorComponentRef` 含 director_scene_revision_ref、component_kind、component_id，以及可选 fragment_path、target_anchor_ref/path_t_range；direct scene selector 按该引用逐项解析，export selector 则只选择不可变 DirectorSceneControlExport item，两条路径不得用同一未类型化 ID 数组混合表达。

## 9. 状态机与业务规则

- DirectorScene ResourceDraft 保存新内容 ArtifactVersion，提交时 compare-and-swap 冻结 Revision；历史修订不可编辑。
- 视口临时选择、相机导航和辅助线开关不是内容状态；对象/姿势/相机/灯光变更才推进 draft_version。
- 对象 ID 在场景 Resource 内稳定；复制对象生成新 ID，替换资源修订保留 transform 并标记导出 stale。
- 控制图导出通过 `director_scene.export_controls` WorkbenchActionRequest 固定 scene revision、导出配置和 EditContextSnapshot 版本向量并创建 child run；结果创建新的 DirectorSceneControlExport ArtifactVersion，场景改变时旧输出只标 stale，不原地重绘或回写旧 Revision。
- 多客户端同一草稿冲突返回对象级 diff；不得以最后保存覆盖另一端姿势或机位。

## 10. 失败、降级与恢复

- WebGL/WebGPU 不可用或设备低于 TF-NFR-001 门槛时进入只读截图/参数模式，不显示空白画布。
- 对象资源加载失败时显示带 ID 的代理体并阻止有损保存；其他对象和相机仍可查看。
- 控制图部分导出失败时逐类型保留成功 Artifact，失败项可重跑且编译报告明确缺失。
- 页面刷新、显卡上下文丢失或服务重启后从持久草稿恢复，不依赖内存中的 Three.js object。
- 不支持的 provider 控制项由 TF-MED-006 处理为 `blocked`/`degraded`/`ignored_with_warning`，导演台不静默删层。

## 11. 安全、隐私、内容与授权

- 只加载已授权、经过格式/大小验证的资产；禁止执行模型文件携带的脚本、外链和任意代码。
- 私有纹理/预览即使不用于最终生成也必须鉴权并限制缓存；日志不记录签名 URL。
- 真人肖像对应的素模/姿势关联不得绕过同意、未成年人或冒充 Gate。
- 场景导出重新计算所有资源引用授权并生成 attribution/rights evidence。

## 12. 观测与运营

- 事件：director_scene_opened、object_added、pose_applied、camera_saved、continuity_warning、scene_frozen、control_exported/failed。
- 指标：首帧可见时延、交互帧率、上下文丢失率、对象加载失败率、导出时延/成功率、检查警告接受率。
- TF-NFR-001 提供设备/浏览器/视口性能矩阵；TF-QLT-001 提供空间控制与视觉回归判定。
- 支持信息含 scene revision、对象/相机 ID、renderer capability、导出 run、错误码和 correlation_id。

## 13. 验收标准

- AC-1：Given 三角色两道具共享 sequence 场景、两个 Shot 各有同名 beat_id 且第三个 Shot 无 Beat，When 完成 ShotTemporalAnchorRef 引用站位/姿势、两机位正交运动轨与三灯 emitter/environment/role/modifier 设置，Then 保存刷新后锚点无歧义，start/end 解析为 0/duration，default 未被用于 CameraTimingBinding，路径点不含 Beat 时间副本且所有数值在 schema 精度内一致。
- AC-2：Given 跨轴正反打机位，When 运行检查，Then 精确标出违规相机；移动回同侧后警告消失。
- AC-3：Given 固定 DirectorSceneRevision，When 导出 clay/pose/depth/normal/segmentation/edge/mask 与 camera/lighting 结果，Then 先产生各输出 Artifact，再产生新的 DirectorSceneControlExport ArtifactVersion；每个像素控制 Artifact 均有可验证且尺寸对齐的 ControlFrameManifest，并可由 ShotSpec ControlLayer 的 export ref/item selector 精确引用，原 Scene Revision 内容不变。
- AC-4：Given 移动视口，When 打开场景，Then 可查看/切机位/播放轨迹且完整建模和关节高级编辑不可用。
- AC-5：Given WebGL 上下文丢失，When 自动恢复，Then 5 秒内重建视图且未保存草稿不被错误提交。
- AC-6：Given TF-QLT-001 固定导演台任务，When 回归，Then 站位、相机、控制图和交互指标达到批准阈值。
- AC-7：Given 一条跟随主体的推轨同时要求固定 headroom 和 screen region，When 播放路径，Then CameraFramingConstraint 可检测偏离并定位 path_t；Provider 不支持精确约束时编译报告显示 transformed/degraded 或 blocked。
- AC-8：Given 同一世界空间站位由两台相反机位拍摄，When 计算屏幕几何，Then StagingGeometrySpec 保持一份世界权威，分别产生带 camera/anchor/canvas 的 ScreenGeometryObservation 且主体屏幕侧可不同，不互相覆盖。
- AC-9：Given 一个 scene 同时存在多个 framing constraint、emitter、role assignment、modifier 以及 actor/static pose，When 重排或删除非目标数组项后将 ShotSpec 的 DirectorComponentRef 往返保存，Then 每项目标仍按独立 component_kind 与稳定 ID 精确恢复；任一 kind-ID 不匹配或使用笼统 light/actor/pose 时保存或编译被阻断。

## 14. 测试场景

- 正常：sequence/coverage/shot 共享场景、单人/多人、姿势库、机位、焦段、三点光、正交相机轨、轴线检查、控制稿/manifest 导出和 ShotSpec selector 绑定。
- 边界：零对象、允许上限对象、极端 filmback/焦段/FOV、不同单位导入、重叠对象、捕获/交付画幅差异和移动窄屏。
- 失败：GPU 不可用、上下文丢失、模型/Blob 损坏、部分导出失败、未知坐标系、权限过期。
- 权限：非 owner transform、跨项目模型加载、撤权后导出、平台审核员越界写入、恶意模型文件、未成年人关联素材。
- 并发/恢复：双端改同相机、重复导出、取消晚到、刷新、断网和服务重启。

## 15. 交付与回退

- 3D 编辑、路径、灯光和各控制导出独立功能开关；关闭时保留 scene 参数与只读预览。
- scene schema 新字段必须可选并保留未知内容；旧客户端只读未知姿势/相机配置，禁止有损保存。
- 发布证据包括桌面/移动设备矩阵、Three.js 非空像素截图、交互/运动验证、控制导出和恢复 E2E。
- 回退为 2D 工作台时保留 DirectorSceneRevision 与 ControlLayer 引用，不删除历史导出。

## 16. 已决策事项与开放问题

- 已决策：V1 是轻量静态关节素模导演台，不是完整 DCC 或骨骼动画系统；完整速度曲线、灯光 cue、IK/动捕、3DGS/空间重建和高级色彩管理后置 V1.5。
- 已决策：DirectorScene 可由 sequence、coverage 或 shot 共享；移动端仅查看和有限调整，导演台输出通过独立 DirectorSceneControlExport、固定控制 Artifact 与 ControlFrameManifest 进入 ShotSpec。
- 已决策：素模约束空间与姿势，不直接保证角色身份不变；身份仍由 CharacterRevision 与身份参考控制。
- 开放问题：Foundation 设备 spike 后冻结对象上限、路径控制点上限和最低 GPU 门槛，数值纳入 TF-NFR-001。
