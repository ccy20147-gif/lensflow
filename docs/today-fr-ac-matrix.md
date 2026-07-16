# LensFlow FR/AC 实施矩阵 — 2026-07-12 开发跟踪

> 本节覆盖 master.md 3.1 + 3.2 共24份PRD的全部FR与AC。
> 状态值: ready | in_progress | implemented | verified | blocked

## 3.1 治理、架构与产品平台

### TF-GOV-001: 需求真相源与变更控制
**状态：in_progress | 目标版本：Foundation**
- FR-1~FR-10: 需求ID、元数据、证据链、变更控制
- AC-1~AC-5: 查询、依赖列表、漂移检查、证据链、审计
- 依赖: 无
- 实现模块: `/www/lensflow/scripts/validate-requirements.mjs` (已存在)
- 验证: node validate + 本矩阵文档

### TF-GOV-002: 第三方代码来源与许可Gate
**状态：in_progress | 目标版本：Foundation**
- FR-1~FR-10: 台账、裁决、NOTICE、clean-room
- AC-1~AC-5: 固定SHA、CI阻断、clean-room构建、NOTICE一致性
- 依赖: TF-GOV-001
- 实现模块: docs/THIRD_PARTY_SOURCE_LEDGER.md

### TF-ARC-001: 新FastAPI后端
**状态：implemented | 目标版本：Foundation**
- FR-1~FR-10: FastAPI应用、API版本、模块分离、事务/outbox、健康检查、secret管理、SafeError
- AC-1~AC-5: 60秒启动、无旧后端、事务+outbox写入、跨owner拒绝、回退兼容
- 依赖: TF-GOV-001, TF-GOV-002
- 实现模块: backend/src/app.py, backend/src/core/

### TF-ARC-002: 分层架构与工作区
**状态：in_progress | 目标版本：Foundation**
- FR-1~FR-11: 产品外壳、调用矩阵、层约束、WorkbenchTask、ManagedAgentTaskPlan
- AC-1~AC-6: 模板→工作台→画布回环、Agent越权拒绝、Workbench越权拒绝、动态工作区注册、WorkbenchTask完整链
- 依赖: TF-GOV-001, TF-ARC-001

### TF-PLT-001: 用户账户与项目所有权
**状态：in_progress | 目标版本：V0 → V1 Core**
- FR-1~FR-10: bootstrap owner、owner_scope、注册/登录、会话、账户删除、V0→V1升级
- AC-1~AC-5: 幂等bootstrap、owner_scope追溯、跨账户隔离、会话撤销、Revision不变
- 依赖: TF-ARC-001

### TF-PLT-002: 项目外壳与资源库
**状态：in_progress | 目标版本：V0**
- FR-1~FR-10: 项目ID/owner/状态、模板创建、资源检索、刷新恢复、归档/删除
- AC-1~AC-5: 多工作流+多资源检索、幂等模板、项目切换隔离、归档只读、跨owner安全拒绝
- 依赖: TF-PLT-001, TF-WF-004, TF-WF-005, TF-OPS-003

### TF-QLT-001: AI/媒体质量评测基线
**状态：in_progress | 目标版本：Foundation → V1 Core**
- FR-1~FR-10: 版本化测试集、六类套件、critical failure、盲评rubric、Provider记录、基线
- AC-1~AC-5: 六类集均有dataset+rubric、可区分随机波动/模型变化、critical failure阻断、双评三级裁决、可追溯评分
- 依赖: TF-GOV-001, TF-OPS-005

## 3.2 工作流、Agent与Media Recipe

### TF-WF-001: 动态Vue Flow画布
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-12: 动态节点渲染、注册表驱动、节点库、端口兼容提示、Inspector、base_hash保存、undo/redo、layout/execution_hash分离
- AC-1~AC-5: 新节点不修改主组件、保存/重载、冲突检测、端口兼容、移动查看
- 依赖: TF-ARC-002, TF-WF-002, TF-WF-004

### TF-WF-002: 节点注册表与强类型端口
**状态：in_progress | 目标版本：Foundation**
- FR-1~FR-12: NodeDefinitionRevision、PortTypeRef、类型兼容矩阵、ConverterRevision、RegistrySnapshot、UI metadata与执行分离
- AC-1~AC-5: 动态发现、兼容检测、schema校验、旧版本迁移、新节点不修改画布
- 依赖: TF-ARC-001, TF-GOV-001

### TF-WF-003: 图编译与执行计划
**状态：ready | 目标版本：Foundation**
- FR-1~FR-12: CompiledExecutionPlan、hash、类型/配置/权限/预算校验、非法图拒绝、版本快照、熵权检查
- AC-1~AC-5: 运行前必须编译、非法connection拒绝、owner_scope校验、旧计划拒绝、纯layout变化hash不变
- 依赖: TF-WF-002, TF-WF-004, TF-WF-005, TF-OPS-001, TF-SEC-001

### TF-WF-004: Workflow草稿与不可变修订
**状态：in_progress | 目标版本：Foundation → V0 → V1 Core**
- FR-1~FR-12: Workflow/Draft/Revision分离、base_hash CAS、graph/layout/execution_hash、activation编译门、Agent Proposal不写Draft
- AC-1~AC-5: 运行固定revision、CAS冲突、纯layout执行hash不变、旧Revision可读、Agent不能写Draft
- 依赖: TF-ARC-001, TF-WF-002

### TF-WF-005: Artifact/Resource/lineage
**状态：ready | 目标版本：Foundation**
- FR-1~FR-12: ArtifactVersion不可变、Resource/Revision/Ref、CAS、跨owner提升边界、stale传播、lineage
- AC-1~AC-5: 不可变Artifact、跨owner ArtifactRef拒绝、lineage追踪、CAS冲突、stale不修改历史
- 依赖: TF-ARC-001, TF-WF-002, TF-OPS-003

### TF-WF-006: 持久DAG执行与异步恢复
**状态：ready | 目标版本：Foundation → V0 → V1 Core**
- FR-1~FR-18: Run/NodeRun/Attempt、epoch/fencing、outbox双写、ProviderInvocationAttempt、unknown对账、fallback重编译
- AC-1~AC-5: 重启恢复、幂等回调、epoch过期拒绝、unknown对账、outbox重试
- 依赖: TF-WF-003, TF-WF-004, TF-WF-005, TF-OPS-003, TF-OPS-005

### TF-WF-007: 控制流/批处理/Subworkflow
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-12: Condition/Join/Fallback/Map/OrderedMap/Fold/SubworkflowCall、token传递、checkpoint、局部运行闭包
- AC-1~AC-5: 分支激活、join语义、map上限、深度限制、局部运行确定性
- 依赖: TF-WF-002, TF-WF-003, TF-WF-006

### TF-WF-008: Human Gate与RequestInput
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-14: 持久Gate/Input、waiting_user、governance强度、幂等提交、超时策略、错误传播、Agent Gate限制
- AC-1~AC-5: 等待恢复、重复提交拒绝、gate不可删除、超时执行策略、Agent不能创建Gate
- 依赖: TF-WF-006, TF-OPS-004

### TF-WF-009: 工作流模板与依赖包
**状态：ready | 目标版本：V0 → V1 Core**
- FR-1~FR-12: 固定WorkflowRevision、PackageManifest、依赖闭包、replacement slot、无secret、导入校验
- AC-1~AC-5: 固定Revision模板、实例化独立Draft、依赖检查、缺失依赖阻断、来源lineage
- 依赖: TF-GOV-002, TF-WF-002, TF-WF-003, TF-WF-004, TF-WF-005, TF-SEC-001

### TF-WF-010: 通用业务节点目录与WorkbenchTask
**状态：ready | 目标版本：V0 → V1 Core**
- FR-1~FR-14: 10类业务节点、WorkbenchTask、input snapshot、CAS提交、managed Agent展开
- AC-1~AC-5: 节点typed I/O、WorkbenchTask完整链、Agent不能创建WorkbenchTask、两个专业基准流程
- 依赖: TF-WF-002, TF-WF-005, TF-WF-006, TF-WF-008

### TF-AGT-001: AgentDefinition与不可变修订
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-11: Agent Resource/Revision、managed preset vs configurable、AgentInvoke固定revision、只输出ArtifactVersion
- AC-1~AC-5: AgentRevision固定运行、只输出Artifact、不能写ResourceDraft/Revision、跨owner ArtifactRef拒绝
- 依赖: TF-WF-002, TF-WF-004, TF-WF-005, TF-AGT-006

### TF-AGT-002: 自定义Agent Studio
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-11: 结构化编辑器、静态校验、试跑隔离、RequestInput、只输出ArtifactVersion、无嵌套Agent
- AC-1~AC-5: 可定义Agent、嵌套调用拒绝、试跑隔离、revision提交、画布调用
- 依赖: TF-AGT-001, TF-AGT-005, TF-AGT-006, TF-WF-001, TF-WF-003, TF-WF-006, TF-WF-008

### TF-AGT-003: Workflow Architect Agent
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-12: schema化Proposal、编译+权限+预算校验、diff展示、确认前不写Draft、hash校验
- AC-1~AC-5: 合法proposal、draft hash变化禁止、模型越权提案拒绝、幂等确认、lineage保留
- 依赖: TF-AGT-001, TF-AGT-005, TF-WF-001~004, TF-WF-006, TF-OPS-002, TF-SEC-001

### TF-AGT-004: 多Agent显式编排
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-9: 独立Agent节点、typed port连接、复用WF-007控制流、RequestInput复用WF-008、失败归属
- AC-1~AC-5: 多Agent可见DAG、无隐藏会话内存、单节点失败不杀无关分支、跨owner检查
- 依赖: TF-AGT-001, TF-WF-006, TF-WF-007, TF-WF-008

### TF-AGT-005: Tool注册/凭证绑定
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-9: ToolRevision、CredentialBinding加密、执行broker、权限交集、输出净化、调用限流
- AC-1~AC-5: 工具定义完整、凭证加密不可导出、网络策略阻断、撤权即时生效、调用记录审计
- 依赖: TF-PLT-001, TF-OPS-001, TF-OPS-005, TF-SEC-001

### TF-AGT-006: Skill定义/装配
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-12: Skill Resource/Revision、非可执行、token预算、冲突检测、装配顺序、SkillAssemblyPlan
- AC-1~AC-5: Skill不可执行、装配顺序确定、超预算裁剪报告、撤权后新编译阻断、历史运行可审计
- 依赖: TF-WF-002, TF-WF-005, TF-SEC-001

### TF-MR-001: Media Recipe定义/实验室/调用
**状态：ready | 目标版本：V1 Core**
- FR-1~FR-14: Lab动态算子、RecipeDraft/Revision、Provider编译报告、控制降级、attempt/outbox复用
- AC-1~AC-5: Lab图保存、主画布调用固定revision、每个控制项裁决、算子失败可追溯、未知对账
- 依赖: TF-WF-002~006, TF-OPS-001~003
