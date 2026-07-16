# LensFlow PRD 开发交付跟踪表

> 文档 ID：TF-DELIVERY-TRACKER-001
> 状态：active
> 日期：2026-07-12
> 需求真相源：`docs/2026-07-12-toonflow-product-requirements-master.md`
> 开发顺序：`docs/2026-07-12-toonflow-development-readiness-and-prd-order.md`

## 1. 使用规则

[规则不变 - 见原始文档]

## 2. 当前总览

| 指标 | 当前值 |
| --- | --- |
| PRD 总数 | 62 |
| defined | 32 |
| reviewed | 1 |
| in_delivery | 23 |
| deferred | 3 |
| 已指派个人 DRI | 27 (main-agent) |
| implemented | 0 |
| verified | 3 |
| released | 0 |
| 已完成 `[x]` | 3 |

## 3. PRD 跟踪表

### 3.1 治理、架构与产品平台

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 下一动作/阻塞 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TF-GOV-001.md` | Foundation | 产品治理 | main-agent | in_delivery | [ ] | ADR 已冻结；需求验证脚本已扩展；治理服务已实现 | ADR-005, governance_service.py, validate-requirements.mjs, test_governance.py (36 tests) |
| `TF-GOV-002.md` | Foundation | 法务/工程治理 | main-agent | in_delivery | [ ] | 第三方台账已建立；NOTICE 生成已实现 | docs/third-party-source-ledger.md, governance_service.py |
| `TF-ARC-001.md` | Foundation | 平台架构 | main-agent | in_delivery | [ ] | FastAPI 骨架 + 7 份 ADR | backend/src/app.py, ADR-001~007, pyproject.toml |
| `TF-ARC-002.md` | Foundation | 产品架构/前端架构 | main-agent | in_delivery | [ ] | 产品分层 ADR + Vue 路由骨架 | ADR-002, frontend/src/router.ts |
| `TF-PLT-001.md` | V0 -> V1 Core | 平台产品/身份后端 | main-agent | in_delivery | [ ] | 身份服务实现（bootstrap/注册/登录/会话） | identity_service.py, session_service.py, test_identity.py (24 tests) |
| `TF-PLT-002.md` | V0 | 核心产品/前端平台 | main-agent | in_delivery | [ ] | 项目外壳 + 资源库实现 | project_service.py, resource_library.py, test_project.py (18 tests) |
| `TF-PLT-003.md` | V0 -> V1 Core | 核心产品/前端平台/工作流平台/资源平台 | main-agent | reviewed | [ ] | 用户已冻结产品决策；进入 approved 前同步受影响 PRD 的公共合同与认领卡 | multi-agent UI/UX、前端、后端/领域架构评审；`requirements/TF-PLT-003.md` |
| `TF-QLT-001.md` | Foundation -> V1 Core | QA/AI 评测 | main-agent | in_delivery | [ ] | 六类测试套件框架 + 质量评估服务 | docs/quality/foundation-test-suite.md, quality_service.py |

### 3.2 工作流、Agent 与 Media Recipe

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 下一动作/阻塞 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TF-WF-001.md` | V1 Core | 工作流前端 | main-agent | in_delivery | [ ] | Vue Flow 画布 + 节点库 + Inspector | WorkflowCanvas.vue, nodeRegistry.ts |
| `TF-WF-002.md` | Foundation | 工作流平台 | main-agent | in_delivery | [ ] | 节点注册表 + RegistrySnapshot 实现 | registry_service.py, node_definition.py, test_registry.py |
| `TF-WF-003.md` | Foundation | 工作流平台/安全 | main-agent | verified | [x] | 已独立验收；解锁持久运行与模板编译 | `compiler.py`, `entitlement_gate.py`, `compile_resolver.py`; PostgreSQL 专项/依赖回归 143 passed（2026-07-16） |
| `TF-WF-004.md` | Foundation -> V0 -> V1 Core | 工作流平台 | main-agent | verified | [x] | 已独立验收；后续依赖可进入实施 | `workflow_service.py`, `draft_revision.py`, `sql_workflow_service.py`, `architect_service.py`; PostgreSQL/API/Architect 专项 34 passed（2026-07-15）；前端 typecheck/test/build 通过 |
| `TF-WF-005.md` | Foundation | 数据平台/工作流平台 | main-agent | verified | [x] | 已独立验收；解锁编译与下游资源合同 | `artifact_repository.py`, `resource_repository.py`, `blob_service.py`; PostgreSQL 专项/回归 91 passed（2026-07-16） |
| `TF-WF-006.md` | Foundation -> V0 -> V1 Core | 运行时平台 | main-agent | in_delivery | [ ] | Foundation 已独立验收；V0 最小持久运行与 V1 lease/复杂恢复待交付 | `runtime_service.py`, `worker.py`; PostgreSQL 运行时/回调/局部重跑专项 45 passed，迁移 `8c9d0e1f2a3b`（2026-07-16） |
| `TF-WF-007.md` | V1 Core | 运行时平台 | main-agent | in_delivery | [ ] | 控制流节点（Condition/Join/Fallback/Map/Fold） | models.py 中相关类型定义 |
| `TF-WF-008.md` | V1 Core | 运行时平台/核心产品 | main-agent | in_delivery | [ ] | Human Gate + RequestInput 持久化 | runtime_service.py, models.py |
| `TF-WF-009.md` | V0 -> V1 Core | 模板产品/工作流平台 | main-agent | in_delivery | [ ] | 模板服务（PackageManifest/依赖/实例化） | template_service.py, test_template.py (16 tests) |
| `TF-WF-010.md` | V0 -> V1 Core | 核心产品/工作流平台 | main-agent | in_delivery | [ ] | 通用业务节点目录 schema | models.py (WorkbenchTask) |
| `TF-AGT-001.md` | V1 Core | Agent 平台 | main-agent | in_delivery | [ ] | AgentDefinition/Revision 模型 | models.py (AgentInvoke, AgentRevision) |
| `TF-AGT-002.md` | V1 Core | Agent 产品/平台 | main-agent | in_delivery | [ ] | Agent Studio 前端 | AgentStudio.vue, models.py |
| `TF-AGT-003.md` | V1 Core | Agent 产品/工作流平台 | main-agent | in_delivery | [ ] | WorkflowArchitect 输入/提案 schema | models.py (WorkflowChangeProposal) |
| `TF-AGT-004.md` | V1 Core | Agent 产品/运行时 | main-agent | in_delivery | [ ] | 多 Agent 编排 schema | models.py, enums.py |
| `TF-AGT-005.md` | V1 Core | Agent 安全/平台 | main-agent | in_delivery | [ ] | Tool 注册/凭证 schema | models.py, enums.py |
| `TF-AGT-006.md` | V1 Core | Agent 平台/知识资源 | main-agent | in_delivery | [ ] | Skill 定义/装配 schema | models.py (SkillContent, SkillAssemblyPlan) |
| `TF-MR-001.md` | V1 Core | 媒体平台 | main-agent | in_delivery | [ ] | MediaRecipe Revision/Invoke schema | models.py (MediaRecipeInvoke) |

### 3.3 小说、剧本与角色资源

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 下一动作/阻塞 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TF-STY-001.md` | V0 | 小说产品/AI | 待认领 | defined | [ ] | 等待 WF-005/010 | - |
| `TF-STY-002.md` | V1 Core | 小说产品/AI | 待认领 | defined | [ ] | 等待 STY-001、AGT-001、WF-005/008 | - |
| `TF-STY-003.md` | V1 Core | 小说产品/资源平台 | 待认领 | defined | [ ] | 等待 STY-002、WF-005 | - |
| `TF-STY-004.md` | V1 Core | 小说产品/AI | 待认领 | defined | [ ] | 等待世界观、OC 和 Agent 合同 | - |
| `TF-STY-005.md` | V1 Core | 小说产品/AI | 待认领 | defined | [ ] | 等待小说框架智能体 | - |
| `TF-STY-006.md` | V1 Core | 小说/影视产品 | 待认领 | defined | [ ] | 等待扩写、资源和剧本 Schema | - |

### 3.4 媒体、影视与广告

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 下一动作/阻塞 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TF-MED-001.md` | V0 -> V1 Core | 影视资产产品/媒体 AI | 待认领 | defined | [ ] | 等待 MED-009 真实图片能力 | - |
| `TF-MED-002.md` | V0 -> V1 Core | 影视产品/AI | 待认领 | defined | [ ] | V0 等待 STY-001、WF-005/010 | - |
| `TF-MED-003.md` | V1 Core | 分镜产品 | 待认领 | defined | [ ] | 等待 MED-002、WF-005/008/010 | - |
| `TF-MED-004.md` | V1 Core | 分镜产品/媒体 AI | 待认领 | defined | [ ] | 等待 MED-003/006 | - |
| `TF-MED-005.md` | V1 Core | 3D/分镜前端 | 待认领 | defined | [ ] | 等待 MED-003、NFR 设备基线 | - |
| `TF-MED-006.md` | V1 Core | 媒体平台/分镜产品 | 待认领 | defined | [ ] | 等待 MED-003、MR-001、OPS-001 | - |
| `TF-MED-007.md` | V1 Core | 摄影规则/分镜产品 | 待认领 | defined | [ ] | 等待 MED-003/005/006 | - |
| `TF-MED-008.md` | V1 Core | 媒体 AI/质量 | 待认领 | defined | [ ] | 等待 STY-003、MED-001/003/006/009、QLT | - |
| `TF-MED-009.md` | V0 -> V1 Core | 媒体平台 | 待认领 | defined | [ ] | 完成真实图片 Provider E2E | - |
| `TF-MED-010.md` | V1 Core | 媒体平台/影视产品 | 待认领 | defined | [ ] | 真实视频与 source-video modify Gate | - |
| `TF-MED-011.md` | V1 Core -> V1.5 | 音频产品/媒体平台 | 待认领 | defined | [ ] | 音频 Provider、授权与同步基线 | - |
| `TF-MED-012.md` | V0 -> V1 Core -> V1.5 | 成片产品/前端媒体 | 待认领 | defined | [ ] | 分版本等待图片、视频、音频和 NFR Gate | - |
| `TF-IMG-001.md` | V0 -> V1 Core | 图片广告产品/媒体 AI | 待认领 | defined | [ ] | 等待 STY-001、WF-010、MED-009、QLT、SEC | - |

### 3.5 社区与生态

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 下一动作/阻塞 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TF-COM-001.md` | V1 Community | 社区产品 | 待认领 | defined | [ ] | 等待作品、授权和审核合同 | - |
| `TF-COM-002.md` | V1 Community | 社区产品/资源平台 | 待认领 | defined | [ ] | 等待世界/OC、授权和资源合同 | - |
| `TF-COM-003.md` | V1 Community | 社区产品/工作流平台 | 待认领 | defined | [ ] | 等待 WF-009、COM-004/006 | - |
| `TF-COM-004.md` | V1 Community | 法务/社区平台 | 待认领 | defined | [ ] | 冻结 License/Grant/Entitlement 合同 | - |
| `TF-COM-005.md` | V1 Community | 社区产品/搜索 | 待认领 | defined | [ ] | 等待三类公开内容稳定 | - |
| `TF-COM-006.md` | V1 Community | Trust & Safety/社区运营 | 待认领 | defined | [ ] | 等待身份、安全和审计 Gate | - |
| `TF-COM-007.md` | V1.5 | 生态产品 | 待认领 | defined | [ ] | 等待 Agent/Recipe、模板、授权和社区闭环 | - |

### 3.6 运行、安全与非功能

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 下一动作/阻塞 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `TF-OPS-001.md` | Foundation -> V1 Core | Provider 平台/安全 | 待认领 | in_delivery | [ ] | Foundation 已独立验收；真实 Provider spike、健康、fallback、轮换与 V1 矩阵待交付 | `runtime_service.py`, `worker.py`; PostgreSQL provider/outbox 专项 45 passed，迁移 `8c9d0e1f2a3b`；真实凭证 gate 未关闭（2026-07-16） |
| `TF-OPS-002.md` | V0 -> V1 Core | FinOps/运行时 | 待认领 | defined | [ ] | 等待身份、编译、运行时和 Provider 合同 | - |
| `TF-OPS-003.md` | Foundation -> V0 | 存储平台 | main-agent | in_delivery | [ ] | Foundation 已验证；V0 仍需真实对象存储、私有签名读取与分片/断点续传 | `blob_service.py`, `storage.py`; PostgreSQL 专项/回归 91 passed（2026-07-16） |
| `TF-OPS-004.md` | V0 -> V1 Core | 事件平台 | 待认领 | defined | [ ] | 等待 PLT-001、WF-006 | - |
| `TF-OPS-005.md` | Foundation | SRE/安全工程 | 待认领 | defined | [ ] | 冻结 Observability/Audit ADR | - |
| `TF-SEC-001.md` | Foundation -> V0 -> V1 Core | Trust & Safety | main-agent | in_delivery | [ ] | Foundation 编译 entitlement gate 已验证；同意、审核、撤回、披露与导出待交付 | `entitlement_gate.py`, `compile_resolver.py`; 跨 owner 编译攻击路径已验收（2026-07-16） |
| `TF-NFR-001.md` | V0 -> V1 Core | 前端平台/QA | 待认领 | defined | [ ] | 冻结参考设备基线 | - |
| `TF-NFR-002.md` | V1 Core | 数据平台/SRE/法务 | 待认领 | defined | [ ] | 冻结保留期 | - |

### 3.7 V2 Deferred

| PRD | 目标版本 | 责任域 | 个人 DRI | 状态 | 完成 | 证据索引 |
| --- | --- | --- | --- | --- | --- | --- |
| `TF-LNG-001.md` | V2 | 长内容产品/运行时 | 待认领 | deferred | N/A | - |
| `TF-TEAM-001.md` | V2 | 协作产品/平台 | 待认领 | deferred | N/A | - |
| `TF-MKT-001.md` | V2 | 市场产品/财务 | 待认领 | deferred | N/A | - |

## 4. 已发布切片记录

[空]

## 5. 证据索引格式

[不变]
