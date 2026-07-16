# TF-NFR-001 性能、可访问性与响应式基线

## 1. 元数据

- ID：TF-NFR-001
- 标题：性能、可访问性与响应式基线
- 状态：defined
- 版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：全前端
- 直接依赖：V0：TF-ARC-002、TF-PLT-002；V1 Core 增加 TF-WF-001
- 责任域：前端平台/QA
- 个人 DRI：待指派

## 2. 背景与问题

开放创作平台包含长表单、动态画布、100 个缩略图、51 镜头工作台、媒体播放器和 Three.js 导演台。若只在开发机验证，会在常见笔记本、移动查看、长中文内容和弱网下出现卡顿、空白画布、文本溢出和控件重叠。

本需求建立版本化设备/浏览器、容量、延迟、可访问性和视觉回归 Gate，使功能验收不能以“页面能打开”代替可用性。

## 3. 目标与非目标

- 目标：桌面端完整编辑，移动端查看与有限操作，所有核心流程在批准浏览器/视口内无不可达控件和内容重叠。
- 目标：量化 V0 常用页面及 V1 的 50 节点、100 缩略图、51 镜头和轻量 3D 性能。
- 目标：达到 WCAG 2.2 AA 关键标准，并以 TF-QLT-001 交互固定任务和视觉回归容差持续验证。
- 非目标：不承诺低于最低设备门槛的完整 3D/视频编辑，也不要求移动端等同桌面功能。
- 非目标：不以牺牲正确性、授权警告或审计信息换取表面速度。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V0 | 产品外壳、模板/项目、Brief、ShotPlan/广告/时间线基础页面的性能、响应式、键盘和视觉 Gate | UI 状态不进入业务真相；埋点 schema 可向前兼容 | 浏览器/视口矩阵与 Web Vitals/无重叠报告 |
| V1 Core | 动态画布、工作台、100 缩略图、51 镜头、3D 导演台和移动有限操作 | V0 页面指标保持，新增场景独立 profile | 50 节点/51 镜/3D/可访问性与长文本回归 |

## 4. 用户与权限

- V1 私有项目的编辑、运行和选择动作仅对项目 owner 开放；平台受控审核员只访问获分配的审核界面，共享项目角色后置到 TF-TEAM-001。
- owner、获分配的平台审核员及历史/受限只读界面均获得相同基本可访问性；只读/编辑是当前界面能力状态，不代表 V1 已存在共享项目角色，并必须通过语义、文本和焦点体现。
- 屏幕阅读器和键盘用户可完成 V0 核心流程；V1 画布/3D 无法完全等价时提供可操作列表/属性表替代路径。
- 移动端向项目 owner 明确展示可用的查看、选择和有限调整；审核员只见获分配的审核命令，不渲染协作评论或其他未交付角色按钮。
- 性能遥测按最小必要采集，禁止记录私有提示、媒体内容、凭证或可识别的资源正文。

## 5. 用户场景与主流程

1. 用户在批准桌面浏览器打开项目，完成模板、Brief、工作台编辑、保存和运行状态查看。
2. 高级用户打开 50 节点业务图，使用键盘/鼠标平移、缩放、选择、连线和属性编辑。
3. 影视用户浏览 51 镜头与 100 缩略图，切换镜头、视图、播放代理并进入轻量 3D 场景。
4. 项目 owner 在移动端查看同一项目、播放结果、筛选镜头、选择候选并进行被允许的有限调整。
5. 自动化在浏览器/视口/长文本/缩放/网络矩阵采集性能、可访问性、canvas 像素和无重叠证据。

## 6. 功能需求

- FR-1：桌面支持 Chrome/Edge/Firefox/Safari 当前与前一主版本；移动支持 iOS Safari、Android Chrome 当前与前一主版本。
- FR-2：完整编辑目标视口为 1280x720 至 2560x1440；1024x768 保持核心编辑可达；移动查看目标为 360x640 至 430x932。
- FR-3：受控网络/参考设备下核心页面 P75 LCP <= 2.5s、INP <= 200ms、CLS <= 0.1；错误/权限页同样计入。
- FR-4：保存命令 P95 在 300ms 内显示持久化中反馈，服务成功返回后 100ms 内更新状态；后端耗时单独展示。
- FR-5：50 节点/100 边画布平移缩放 P5 帧率 >= 45 fps（等价地 P95 frame time <= 22.2ms），选择/打开属性 P95 <= 100ms，布局不因状态文本改变尺寸。
- FR-6：100 缩略图使用虚拟化/渐进加载，连续滚动 P5 帧率 >= 45 fps（P95 frame time <= 22.2ms），首屏可见缩略图 P95 <= 1.5s（缓存）/3s（标准网络）。
- FR-7：51 镜头工作台初次交互 P95 <= 3s，镜头切换 P95 <= 150ms（元数据已载入），搜索/筛选 P95 <= 200ms。
- FR-8：参考 3D 场景（10 素模、20 道具、4 相机、8 灯）桌面 P5 帧率 >= 30 fps（P95 frame time <= 33.3ms）；上下文丢失后 5 秒内恢复或进入可读降级视图。
- FR-9：移动端 3D 参考降级场景 P5 帧率 >= 24 fps（P95 frame time <= 41.7ms），支持查看/切机位/轨迹与有限 transform；不加载桌面高级编辑器。
- FR-10：所有文本/按钮在支持视口、200% 浏览器缩放和最长固定测试字符串下无遮挡、裁断关键命令或不可滚动溢出。
- FR-11：满足 WCAG 2.2 AA：正文对比 >=4.5:1、大文本/非文本控件 >=3:1、可见焦点、语义标签、错误关联和 reduced motion。
- FR-12：所有核心命令可键盘到达；焦点顺序与视觉顺序一致，modal 焦点锁定/恢复，快捷键不与输入法/浏览器冲突。
- FR-13：canvas/3D 提供列表/树/属性替代路径及状态播报；不可访问的纯视觉警告必须有文本等价物。
- FR-14：前端错误边界防止单个缩略图、节点、播放器或 3D 失败导致整个工作区白屏，并提供 correlation_id。

## 7. 交互与展示

- 工作型界面保持密集、安静、可扫描，不使用营销式大标题、装饰浮卡或卡片套卡片。
- 固定格式元素使用 aspect-ratio、网格轨道和 min/max 尺寸；动态状态、hover、加载和长词不引起布局跳变。
- 使用图标表达通用工具并配 tooltip/accessible name；颜色选择用 swatch，模式用 segmented control，数值用输入/滑杆。
- 文字不按 viewport 宽度缩放，letter-spacing 为 0；长中文、英文 URL/ID 提供换行或安全截断与完整 tooltip。
- 错误、警告、stale、权限与运行状态使用图标+文字+语义，不只用颜色、动画或位置。

## 8. 数据、类型与公共接口

- `ClientCapabilitySnapshot` 含浏览器/版本、OS、viewport、DPR、内存档、GPU/WebGL capability、codec/WASM 和 reduced-motion；不含指纹化高熵字段。
- `FrontendPerformanceSample` 含 route/workspace、scenario_id、web vitals、interaction/frames/memory buckets、build revision 和匿名 owner scope。
- `AccessibilityAuditReport` 含 scenario、browser/viewport、automated rules、keyboard steps、screen-reader notes、violations 和 evidence refs。
- `VisualRegressionReport` 含 baseline revision、viewport/theme/locale、screenshot refs、pixel/layout diffs、overlap/text-fit assertions。
- 所有质量报告以 ArtifactVersion 保存并引用 TF-QLT-001 suite/rubric；性能埋点不是业务状态真相。

## 9. 状态机与业务规则

- 前端可使用 loading/saving/degraded/offline 临时展示状态，但 Run/NodeRun/Revision/HumanTask 状态只来自服务端事实。
- 性能 Gate 按 scenario/browser/device/build revision 评定，缺少必测组合即未通过，不以总体平均掩盖失败。
- 延迟分位只在固定采样方法和最小样本数下以 P75/P95 比较。
- 帧率下界只在固定采样窗口、帧率定义和最小样本数下以 P5 比较；基线更新需评审并保留旧报告。
- 响应式能力由 capability/viewport 决定，不按 user-agent 隐藏权限；服务端仍验证所有命令。
- 浏览器恢复/重新连接后重新获取 authoritative state，不能用过期本地成功状态覆盖服务端。

## 10. 失败、降级与恢复

- 低 GPU/内存、codec 或 WebGL 不支持时提供明确只读/代理/参数模式，不显示空白 canvas 或无限加载。
- 缩略图/媒体失败单元化处理并可重试；列表、镜头和其他媒体继续可用。
- SSE/WebSocket 断开时显示连接状态、按 after_seq 重连；轮询降级不得改变状态语义。
- JavaScript 错误边界保留未提交草稿提示，刷新后从服务端草稿恢复；本地缓存损坏可安全清除。
- reduced motion、屏幕阅读器或高对比模式下禁用非必要动画，仍保留等价状态反馈。

## 11. 安全、隐私、内容与授权

- 性能/错误遥测清理 token、签名 URL、提示、媒体正文、姓名/声纹/人脸和跨租户标识；仅保留关联 ID。
- 前端隐藏不能作为授权，移动/替代路径和快捷键均调用相同服务端权限 Gate。
- CSP、可信资源源、下载/Blob 隔离和 iframe/worker 策略由安全基线控制；错误 UI 不渲染未清理 provider HTML。
- 可访问性辅助文本不得泄露视觉上已受限的私有内容或安全判定细节。

## 12. 观测与运营

- 事件：route_interactive、canvas_frame_drop、thumbnail_failed、shot_switch_slow、webgl_context_lost、a11y_violation_detected、error_boundary_shown。
- 看板按 build、route、browser、viewport、device class 分层 LCP/INP/CLS、交互延迟、帧率、内存与错误率。
- 发布 Gate 要求所有 P0 场景样本数达自动套件定义，且无 blocker 可访问性/重叠/空白 canvas 缺陷。
- 生产性能预算连续两个窗口超标触发告警与功能降级/回退评估，不静默扩大阈值。

## 13. 验收标准

- AC-1：Given 批准参考设备/网络，When 执行核心 V0 流程，Then P75 LCP/INP/CLS 分别 <=2.5s/200ms/0.1。
- AC-2：Given 50 节点/100 边画布，When 连续平移缩放与选择，Then P5 帧率 >=45fps（P95 frame time <=22.2ms）且属性打开 P95 <=100ms。
- AC-3：Given 51 镜头/100 缩略图，When 滚动、搜索和切换，Then 达 FR-6/7 阈值且内存峰值 <=1.2GB 桌面参考设备。
- AC-4：Given 360px 移动、200% 缩放及最长文本，When 遍历核心页面，Then 无关键控件重叠/遮挡/不可滚动，命令可达。
- AC-5：Given 键盘与屏幕阅读器，When 完成 V0 Brief->候选->确认，Then 无 WCAG 2.2 AA blocker，焦点/错误播报正确。
- AC-6：Given 参考 3D 场景，When 桌面/移动测试与上下文丢失，Then 达到帧率、恢复或明确降级要求，canvas 像素非空。

## 14. 测试场景

- 正常：项目/Brief/候选/ShotPlan/时间线、50 节点、51 镜、100 图、播放器和 3D 桌面/移动。
- 边界：360px、1024px、4K、200% zoom、长中文/英文/URL、RTL 探测、慢网、高 DPR、低内存。
- 失败：图片/媒体 404、事件断连、JS chunk 失败、WebGL/codec 不支持、上下文丢失和缓存损坏。
- 权限：非 owner 编辑/选择、审核员越出任务、历史/受限只读状态、隐藏按钮快捷键绕过、错误辅助文本泄漏、跨租户媒体缓存。
- 并发/恢复：快速重复保存/切换、事件乱序、刷新、断网恢复、多标签页和服务端冲突。

## 15. 交付与回退

- 3D、高密度缩略图、画布特效和 WebAV 分别可降级/关闭；核心读写与状态提示必须保留。
- 性能、a11y、视觉基线与场景数据版本化；阈值变更需主需求评审，不能随构建自动接受新截图。
- 发布证据包括 Playwright 浏览器/视口截图、axe+人工键盘/读屏、canvas 像素、Web Vitals 和容量报告。
- 回退 build 时服务端 schema 保持兼容；未知前端能力进入只读/降级，不进行有损保存。

## 16. 已决策事项与开放问题

- 已决策：桌面完整编辑、移动查看和有限操作；50 节点、100 缩略图、51 镜头是 V1 必测容量。
- 已决策：无重叠、可访问替代路径和非空 canvas 是发布 Gate，不是视觉优化项。
- 已决策：TF-PLT-003 每个产品切片从首个实现起均须满足本项适用的键盘、读屏、响应式、性能和 reduced-motion Definition of Done；不得把 NFR 推迟为最终视觉补测。
- 开放问题：Foundation 设备实验后冻结具体 CPU/GPU/网络参考机型，不能放宽本文件用户可感知阈值。
