# 动态 Vue Flow 业务画布

## 1. 元数据

- ID：TF-WF-001
- 标题：动态 Vue Flow 业务画布
- 状态：in_delivery
- 目标版本：V1 Core
- 优先级：P0
- 全局位置：主业务工作流画布
- 直接依赖：TF-ARC-002、TF-WF-002、TF-WF-004
- 责任域：工作流前端
- 个人 DRI：main-agent

## 2. 背景与问题

Toonflow 的固定节点位置、固定 slot 和 FlowData 适合单一生产流程，但无法支撑用户自由组合业务能力。开放平台需要由后端注册表驱动的 Vue Flow 画布，同时避免把章节、镜头或模型原子全部铺成节点。

画布是 WorkflowDraft 的编辑器，不是运行状态或领域内容真相源。

## 3. 目标与非目标

目标：

- 动态渲染注册节点、端口、配置和状态。
- 支持添加、连接、移动、删除、复制和保存合法业务图。
- 将节点摘要、Inspector、Artifact 预览和领域工作台衔接起来。
- 新增节点类型时不修改画布主组件。

非目标：

- 不实现完整领域编辑器或媒体 Recipe 图。
- 不由前端判定图可运行或节点已完成。
- 不为每个模板增加专用 slot、字段或隐藏路径。

## 4. 用户与权限

- V1 私有项目只有项目 owner 可查看 WorkflowDraft、WorkflowRevision 与运行状态，且只有 owner 可编辑 WorkflowDraft；不可变 Revision 和后端运行状态均不得由前端编辑。项目成员、共享编辑、审阅和只读角色统一后置 TF-TEAM-001。
- Workflow Architect 只输出 Proposal ArtifactVersion；owner 确认后由平台 API 应用 Patch，Agent 不能修改 Draft 或直接操作前端 store。
- 无权节点、资源和配置在节点库中隐藏或显示不可用原因。
- policy_required 节点与 Gate 不允许通过 UI 绕过。

## 5. 用户场景与主流程

1. 用户打开一个 WorkflowDraft，前端加载 graph、layout 和 registry snapshot。
2. 用户从节点库检索业务节点并拖入画布。
3. 连接端口时前端先执行注册表兼容提示，保存时后端再次编译。
4. 用户在 Inspector 修改配置并查看 schema 错误。
5. 用户保存时提交 base_hash；成功后获得新 draft hash。
6. 用户激活 Revision 或打开节点对应工作台继续精修。

## 6. 功能需求

- FR-1：节点渲染器必须根据 node_type_id 和版本从注册表动态映射。
- FR-2：画布主组件不得包含按业务节点类型编写的固定 slot。
- FR-3：节点库支持搜索、分类、权限和兼容状态筛选。
- FR-4：端口展示 schema identity、cardinality 和必需/可选状态。
- FR-5：连接操作必须即时提示类型不兼容，但最终裁决由后端编译器完成。
- FR-6：Inspector 根据配置 JSON Schema 动态生成或加载专用编辑器。
- FR-7：节点卡只展示名称、摘要、关键输入输出、状态和打开工作台命令。
- FR-8：保存必须携带 base_hash 并处理乐观并发冲突。
- FR-9：画布位置、缩放和分组属于 layout，不进入执行 hash。
- FR-10：新增合法节点无需修改 WorkflowCanvas 主组件即可添加、连接、保存和重载。
- FR-11：支持撤销/重做、框选、复制粘贴、删除和键盘可达操作。
- FR-12：运行、成本和错误只显示后端快照或事件，不在前端推导状态。

## 7. 交互与展示

- 左侧为节点库和模板入口，中央为画布，右侧为 Inspector，底部为可收起运行信息。
- 节点使用稳定尺寸，长文本在 Inspector 或工作台显示。
- 熟悉图编辑的用户可缩放、平移、框选和快捷连线。
- 非法连接使用端口级说明，不只改变颜色。
- 节点失败显示安全错误摘要、correlation ID 和允许的重试/打开工作台动作。
- 画布在移动端只提供查看和有限选择，不承诺完整编辑。

## 8. 数据、类型与公共接口

前端消费 WorkflowDraft、WorkflowRevision、NodeDefinitionRevision、Edge 和 layout schema。

边引用 node_instance_id 与 port_id，不使用显示名称。节点配置按注册表 schema 保存。

ArtifactRef、ResourceRef 和状态族沿用主表第 8 节：ArtifactRef 只在同一 owner_scope 内直接消费；跨 owner 内容必须先提升为 ResourceRevision，并通过带授权证据的固定 ResourceRef 使用。前端缓存只做显示加速，后端 Revision 和 Draft 是恢复依据。

## 9. 状态机与业务规则

编辑对象是 WorkflowDraft；运行对象必须是 RevisionStatus.active 的 WorkflowRevision。

保存使用 base_hash compare-and-swap。冲突时保留本地未提交 patch，并展示基于共同版本的 diff。

前端不允许将 NodeRunStatus 或 RunStatus 写回后端。节点 UI 状态来自运行快照与 RunEvent。

## 10. 失败、降级与恢复

- 注册表加载失败时画布进入只读并保留未知节点占位，不丢弃图数据。
- 专用 Inspector 加载失败时回退到安全的 schema 表单。
- 保存冲突时不自动覆盖，允许刷新、合并或另存 Draft。
- 网络中断时保留本地未提交 patch，恢复后重新校验 base_hash。
- 节点版本被退役时仍能查看旧 Revision，并提供迁移诊断。

## 11. 安全、隐私、内容与授权

- 节点库与 Inspector 不展示用户无权使用的密钥或敏感资源内容。
- ResourceRef 选择器按 owner_scope 和 entitlement 过滤。
- ArtifactRef 选择器只显示当前 owner_scope 内容；禁止通过给 ArtifactRef 附加 grant 或临时 URL 实现跨 owner 引用。
- 配置日志与浏览器存储不得保存明文 secret。
- 粘贴或导入图先视为不可信输入，必须经后端验证。
- 节点富文本和错误信息必须净化，防止脚本注入。

## 12. 观测与运营

- 记录画布加载时长、节点数、交互延迟、保存失败和冲突率。
- 记录未知节点、Inspector 回退和类型连接拒绝。
- 前端错误关联 build SHA、workflow draft 和 correlation ID。
- 监控是否出现固定业务字段或绕过注册表的新节点实现。

## 13. 验收标准

- AC-1：注册一个新 NodeDefinition 后，不改 WorkflowCanvas 即可在节点库出现、添加、连接、保存和重载。
- AC-2：含 50 个节点和 100 个 Artifact 缩略图的画布可操作，节点尺寸不因状态变化跳动。
- AC-3：非法端口连接在前端定位，强制构造后仍被后端拒绝。
- AC-4：两个标签页保存同一 Draft 时，后提交者收到 diff，不覆盖先提交内容。
- AC-5：刷新页面后 graph、layout、配置、选中 Revision 和运行状态从后端恢复。
- AC-6：未知退役节点以只读占位显示，原图 JSON 不被删除或改写。

## 14. 测试场景

- 正常：添加十种节点、连线、配置、保存、激活并重载。
- 边界：50 节点、长名称、多端口、空图和 owner 查看 retired Revision 的只读模式。
- 失败：注册表、Inspector、保存 API 和事件连接分别失败。
- 权限：非 owner 项目访问、无权节点、跨 owner 裸 ArtifactRef、缺少授权证据的 ResourceRef 和敏感配置不可选。
- 并发/恢复：双标签冲突、离线 patch 和刷新恢复不丢数据。

## 15. 交付与回退

- V1 Core 通过功能开关开放动态画布，模板入口仍可独立使用。
- 先交付通用 NodeShell、节点库、Inspector、连接与保存，再接运行状态。
- 回退时保留 WorkflowDraft/Revision 数据，可暂时使用只读图查看器。
- 发布证据包括新增节点零主组件修改演示、容量测试和视觉截图。

## 16. 已决策事项与开放问题

已决策：使用独立 Vue 3 + Vue Flow 核心；Toonflow 仅作经许可的组件参考或供体。

已决策：V1 私有项目 owner-only；团队查看、编辑、评论与审阅由 TF-TEAM-001 提供。

开放问题：画布自动布局算法可在可用性测试后选择，不得进入执行 hash 或改变节点语义。
