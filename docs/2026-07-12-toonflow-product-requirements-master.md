# LensFlow 开放创作平台产品需求总指导与跟踪表

> 文档 ID：TF-PRD-MASTER-001  
> 状态：产品范围与公共合同已完成主代理验收；开发准备门已建立，待团队认领与逐项批准  
> 创建日期：2026-07-12  
> 适用范围：从产品验证到完整开放创作平台  
> 目标读者：产品、设计、前端、后端、AI/媒体工程、QA、运营与后续编码 Agent  
> 上游材料：`docs/2026-07-11-toonflow-open-creation-platform-blueprint.md`、Toonflow Web/App、SeedV 参考代码及 2026-07-11 至 2026-07-12 的产品决策

## 1. 文档权威性

本文件是完整产品的需求真相源，负责回答：做什么、为什么做、边界在哪里、位于哪个产品层、何时交付、依赖什么以及怎样证明完成。

规则：

1. 本文件中的已批准需求优先于旧蓝图中的冲突描述。
2. 旧蓝图只承担技术研究与历史决策记录；规范性执行语义必须迁入本文件、详细需求或固定版本 ADR。
3. 每个需求必须有唯一 ID、详细需求文档、目标版本、负责人、依赖、验收证据和状态。
4. 功能实现、接口、测试或演示如果不能对应到需求 ID，不计入正式交付。
5. 需求改变边界、公共接口、权限、版本或验收条件时，必须先更新本文件，再修改详细需求文档和实现计划。
6. 不得以“已有类似页面”“接口能返回数据”“测试未报错”替代端到端验收证据。
7. 旧蓝图和参考仓库只能提供证据，不能通过一句“继续有效”隐式引入规范；仍有效的规则必须迁入本文件、详细需求或固定版本 ADR。
8. 开发认领、串并行顺序、ADR/Schema/provider/质量 Gate 以 `docs/2026-07-12-toonflow-development-readiness-and-prd-order.md` 为准；该文件不得修改本文件的产品范围和公共合同。
9. 日常 DRI、进度、完成标记和交付证据登记在 `docs/2026-07-12-toonflow-prd-delivery-tracker.md`；只有 Master/详细 PRD 达到 `verified` 或 `released` 时才能标记完成。

## 2. 已冻结的产品决策

以下结论不再留给详细需求文档自行选择：

- 产品对用户展示的名称统一为 LensFlow；历史 `TF-*` 需求 ID、已应用数据库迁移、审计证据和不可变 `toonflow.*` schema 不改写，新 schema 使用 `lensflow.*` 并保留兼容解析。
- 产品信息架构采用“创作资产库 + 项目工作室”双轴：Workflow 画布、World、Character OC 和私有 Screenplay 在浏览与“从此创作”心智上平级，但 Workflow 与 Resource 的 canonical 类型、Draft/Revision 和运行语义不得合并。
- Workflow 是可命名、检索、克隆和版本化的私有创作对象，但每个可变 WorkflowDraft 只能属于一个可编辑 Project；Resource 可被多个 Project 固定引用。
- 节点运行结果默认是 ArtifactVersion；只有用户从准确 OutputBinding/SelectionRecord 显式保存时，才能事务性提升为 ResourceRevision。不得默认批量提升或把索引投影当作内容真相。
- V1 Screenplay 完全私有，不提供社区 Listing、搜索、引用、派生，也不得包装为只读 CreativeWork 或通过工作流模板泄漏。
- 消费社区后置；在没有真实只读内容和授权闭环前，社区导航必须隐藏，不得提供空搜索、假内容或可点击失败动作。
- 新建独立 FastAPI 后端；SeedV 仅提供领域流程、Schema、可靠执行和生产经验参考，不作为代码或数据库基础。
- 新建 Vue 3 + Vue Flow 开放工作流前端；Toonflow 仅作为交互参考和经授权、经解耦的组件供体。
- 任何 Toonflow 或其他第三方代码、视觉资产、品牌元素和组件复用必须先通过来源与许可 Gate；未通过时采用 clean-room 重写，不阻塞独立核心建设。
- WebAV 只承担浏览器时间线预览和受限导出，不作为 DAG 或后端运行时。
- 主业务画布使用业务能力节点；重复内容进入领域工作台；底层模型链进入媒体配方实验室。
- 六阶段小说框架不可拆分为六个主画布节点，统一封装为“小说框架智能体”；内部语义固定为 Stage 0 初始核心骨架、Stage 1 核心维度、Stage 2 主线多轮重构、Stage 3 支线衍生与网状结构、Stage 4 逻辑闭环与细节优化、Stage 5 主题升华与续作延展，每阶段使用独立 typed contract 和 validator。
- 首批官方创作智能体为“世界观智能体”“小说框架智能体”“扩写智能体”。
- 世界观智能体包含初始角色 OC 设计；角色可从世界包提升为独立、可版本化、可发布的角色 IP/OC。
- 用户自定义智能体由 SOP、skills、tools 和强类型输入输出组成；Skill 是非可执行的版本化指令/知识资源，Tool 才能产生外部副作用；V1 禁止智能体内部嵌套调用智能体、工作流或媒体配方。
- 多智能体协作必须显式出现在主画布；智能体可以请求用户输入并持久化为 `waiting_user` 后恢复。
- 官方 managed Agent 业务节点可以在画布上保持一个节点，但编译后必须显式物化 AgentInvoke、workflow-owned WorkbenchTask/Human Gate 和 Resource commit 步骤；Agent 本身不得调用工作台或 Gate。高级运行视图必须能展开这些步骤。
- ComfyUI 粒度的媒体图位于媒体配方实验室，在主画布中以一个固定版本的配方调用节点出现。
- V1 必须建设镜头级可展开工作台和轻量 3D 导演台；不建设 Blender/Unreal 级完整 DCC。
- 9/16/25 宫格是镜头或关键帧的生成与展示策略，不是 canonical 数据模型。
- 故事板、动作板、宫格、素模、3D 导演台、摄影灯光、连续性检查和 Provider 编译是同一镜头工作台内的可组合方法、视图或 child run，不注册为逐镜主画布节点；带精确时长、切点和音轨的 Animatic 后置 V1.5。
- 宫格复合图不得默认作为多角度身份或镜头控制的单一输入；系统必须保留逐格 Artifact 与语义映射，并按 Provider 能力拆成独立参考、有序关键帧或明确支持的复合输入。
- `ArtifactVersion` 是不可变内容载体和运行产物真相；`Resource` 是持久业务身份，`ResourceRevision` 通过 `content_artifact_version_id` 固定内容。可运行、可引用、可发布对象不得指向可变草稿。
- `ArtifactRef` 只允许在同一 owner_scope 内直接消费；跨 owner 内容必须先提升为 ResourceRevision，再通过带 GrantSnapshot 的 ResourceRef 使用。不得给裸 ArtifactVersion 发放跨 owner 许可。
- ShotPlan、ShotSpec、DirectorScene 分别是可独立修订的 Resource 类型；工作台编辑草稿，运行、生成、发布和社区引用只消费不可变 Revision。镜头数据实行字段级唯一权威，不以任一对象笼统充当“全部镜头真相”。
- 51 镜头工作台使用非 canonical 的 ShotPlanEditSession 和 EditContextSnapshot 协调计划、51 个独立 ShotSpecDraft 及所引用的 DirectorSceneDraft；每个 ResourceDraft 单独 CAS，提交通过原子 CommitManifest 同时冻结受影响 Revision。
- V1 建立作品、世界观、角色 OC 和工作流模板的基础社区发布/引用/克隆闭环；交易、收益分成和复杂推荐后置。
- 公开展示不等于允许复用。引用、派生和商业使用必须由作者分别授权。
- 社区复用通过版本化 LicenseOffer、LicenseAcceptance、GrantSnapshot 和当前 EntitlementDecision 执行；clone/install/execute/redistribute 必须有明确 action scope，不能从“公开”或“商业允许”猜测。
- V1 Core 为多账户、每个私有项目单 owner；项目成员、共享编辑、Reviewer/Operator 项目角色和团队空间统一后置 TF-TEAM-001。平台审核员等运营角色不等于项目成员。
- 世界观、角色、工作流、智能体、媒体配方和作品运行时均固定到不可变修订，不动态追随 latest。

## 3. 产品目标与成功标准

### 3.1 产品目标

建设面向非技术创作者、专业 AIGC 制作者和工作流作者的开放创作平台，使用户能够：

- 从想法、小说、剧本、产品资料或参考素材开始创作；
- 使用官方集成智能体快速完成高质量工作流；
- 在业务画布中自由替换、组合和编排能力；
- 在领域工作台中精修世界观、OC、小说、剧本、资产、镜头和时间线；
- 在媒体配方实验室中复刻和发布专业生成方法；
- 发布作品、世界观、角色 OC 和工作流模板，并按授权被其他用户引用或派生；
- 以可恢复、可追溯、可控成本的方式生成文本、图片、视频、音频和成片。

### 3.2 产品级成功标准

完整产品必须同时满足：

1. 非技术用户不打开画布也能从模板完成一次真实创作。
2. 高级用户可以在主画布重排业务流程，而无需修改前端代码。
3. 专业用户可以在镜头工作台和媒体配方实验室等粒度复刻公开专业工作流。
4. 任意运行可解释其工作流修订、输入修订、模型、参考素材、成本、尝试和输出来源。
5. 世界观和角色 OC 能跨作品复用，并保持身份、版本、授权和署名关系。
6. 失败、刷新、服务重启、人工等待和异步 provider 回调不会破坏运行真相。
7. 社区中的展示、引用、派生和商业授权边界可执行，而不是仅靠文字声明。

## 4. 产品全局位置

```text
产品外壳
├── 首页/目标入口/模板
├── 项目与资源库
├── 社区
└── 设置、模型、用量和账户

创作空间
├── 小说剧本创作工作区
│   ├── 世界观与 OC 工作台
│   ├── 小说框架工作台
│   ├── 扩写与连续性工作台
│   └── 剧本改编工作台
├── 影视生产工作区
│   ├── 角色/场景/道具资产工作台
│   ├── 镜头规划与分镜控制工作台
│   ├── 轻量 3D 导演台
│   └── 时间线与成片工作台
├── 图片/广告创作工作区
│   ├── 产品与品牌 Brief
│   ├── 创意方向与候选工作台
│   └── 版式、多尺寸变体与交付包
├── 主业务工作流画布
├── Agent Studio
└── Media Recipe Lab

平台内核
├── 工作流注册、编译与执行
├── Artifact、资源与修订
├── Provider、异步任务与成本
├── 权限、授权、内容安全与审计
└── 社区发布、检索、引用与派生
```

工作区是导航与编辑体验，不是运行时类型系统。跨工作区连接由强类型 Artifact/ResourceRef 决定。

## 5. 版本与交付门

| 版本 | 产品目的 | 必须交付 | 明确后置 |
| --- | --- | --- | --- |
| Foundation | 冻结合同与技术可行性 | 新 FastAPI/Vue Flow 骨架、核心 ADR、第三方代码许可裁决、真实 provider spike、公共版本与调用合同 | 用户产品发布 |
| V0 | 验证用户价值 | 模板入口、idea/剧本到可编辑 ShotPlan 预览、产品 brief 到真实广告图片包、最小持久运行、成本与失败可见 | 自由复杂 DAG、专业镜头工作台、社区闭环、真实成片 |
| V1 Core | 形成开放创作产品 | 动态画布、官方三智能体、自定义智能体、Artifact/资源修订、镜头工作台、轻量 3D 导演台、真实图片和视频 provider、视频代理与基础音轨时间线 | 完整 DCC、长剧集量产、任意代码节点、浏览器最终编码 |
| V1 Community | 验证复用网络 | 作品/世界观/OC/工作流模板发布、资源库、引用、克隆、派生、授权与审核 | 交易、分成、复杂推荐、独立插件市场 |
| V1.5 | 提升专业媒体能力 | 有限 WebAV 导出、更多 provider、3DGS/动作参考、独立 Agent/媒体配方发布 | 电影级协同与大规模生产 |
| V2 | 长内容与团队生态 | 长篇/长剧集生产、团队权限、多人协作、市场和生态治理 | 另行立项能力 |

V1 Core 与 V1 Community 可以分批上线，但在产品宣称“开放创作社区”前必须同时通过两者的验收门。

版本字段使用 `A -> B` 时，表示同一能力在 A 交付可独立验收的最小切片，在 B 完成扩展切片；详细需求必须分别列出每个里程碑的功能、数据兼容和验收证据。不得把后续切片作为前一版本通过的隐含条件。

V0 的身份模式为单部署 bootstrap owner，不开放公共注册；所有项目、Blob、运行和密钥仍必须带 owner/tenant 边界，以便 V1 Core 开放多账户时无破坏性迁移。V0 的 ShotPlan 预览支持逐镜文本、缩略图、排序和选择，但不包含 V1 Core 的完整控制层、3D 导演台和专业连续性审查。

V1 Core 开放公共注册和多账户，但每个私有项目仍只有一个 owner。V1 Core 需求不得把“编辑者、审阅者、Operator、Viewer、项目成员或协作者”写成独立项目主体；编辑、运行、选择、私人批注和提交等 capability 均由该项目 owner 行使。多主体项目绑定、共享评论和审阅只在 TF-TEAM-001 交付后生效。

## 6. 统一需求状态

```text
discovered -> defined -> reviewed -> approved -> in_delivery
          -> implemented -> verified -> released
          -> deferred / superseded / rejected
```

- `implemented` 只表示实现存在。
- `verified` 必须具备详细需求文档中定义的自动测试、人工验收或运行证据。
- `released` 必须满足版本级交付门、监控和回退条件。
- 本节是 Requirement 文档生命周期，不得与第 8.6 节的运行、修订、人工任务、社区审核或授权状态混用。

## 7. 需求总表

### 7.1 产品基础与治理

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-GOV-001 | 需求真相源与变更控制 | 全局治理 | Foundation | P0 | 统一 ID、状态、版本、变更和追踪，不代替项目管理工具 | 需求变更可追溯到设计、实现和验收 | `requirements/TF-GOV-001.md` | in_delivery |
| TF-GOV-002 | 第三方代码来源、许可与 clean-room Gate | 全局治理/工程基线 | Foundation | P0 | 逐组件裁决复用、重写或放弃；素材权利另由 TF-SEC-001 管理 | 锁定 SHA、来源台账、许可/NOTICE、品牌条款和回退演练通过 | `requirements/TF-GOV-002.md` | in_delivery |
| TF-ARC-001 | 新 FastAPI 单后端 | 平台内核 | Foundation | P0 | 全新建设；不运行 Toonflow/SeedV 旧后端作为生产依赖 | 服务骨架、ADR、健康检查、模块边界和部署基线通过 | `requirements/TF-ARC-001.md` | in_delivery |
| TF-ARC-002 | 分层创作架构与工作区 | 产品外壳/创作空间 | Foundation | P0 | 工作区、业务画布、领域工作台、配方实验室和运行内核职责不得混淆 | 导航、数据流和跨层调用合同通过评审 | `requirements/TF-ARC-002.md` | in_delivery |
| TF-PLT-001 | 用户账户与项目所有权 | 产品外壳/平台内核 | V0 -> V1 Core | P0 | V0 bootstrap owner；V1 多账户私有项目；不含团队实时协作 | owner 边界、注册/登录、项目隔离、删除与权限 E2E | `requirements/TF-PLT-001.md` | in_delivery |
| TF-PLT-002 | 项目、工作流与资源库外壳 | 产品外壳 | V0 | P0 | 一个项目可有多个工作流和资源；不把项目等同工作流 | 项目创建、切换、保存、资源检索和恢复 E2E | `requirements/TF-PLT-002.md` | in_delivery |
| TF-PLT-003 | LensFlow 创作外壳、个人创作资产与项目关联 | 产品外壳/项目工作室/创作资产库 | V0 -> V1 Core | P0 | 资产库与项目双轴；私人 Workflow、运行产物和 Artifact 提升不制造第二真相源 | 模板/自由画布、项目关联、Run 深链、产物资产化和恢复 E2E | `requirements/TF-PLT-003.md` | reviewed |
| TF-QLT-001 | AI、媒体与交互质量评测基线 | 全局质量治理 | Foundation -> V1 Core | P0 | 固定测试集、人工 rubric、自动指标和回归容差；不以单张样例判定质量 | 文本、身份、镜头控制、51 镜头、广告图和交互回归报告 | `requirements/TF-QLT-001.md` | in_delivery |

### 7.2 工作流与运行内核

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-WF-001 | 动态 Vue Flow 业务画布 | 主业务工作流画布 | V1 Core | P0 | 动态注册节点；不得使用固定 slot/FlowData | 新节点无需改画布主组件即可出现、连接和保存 | `requirements/TF-WF-001.md` | in_delivery |
| TF-WF-002 | 节点注册表与强类型端口 | 平台内核 | Foundation | P0 | 类型基于 schema identity/version，不按名称或字符串前缀猜测 | 注册、兼容矩阵和错误定位 contract tests | `requirements/TF-WF-002.md` | in_delivery |
| TF-WF-003 | 图编译、执行计划与策略校验 | 平台内核 | Foundation | P0 | 运行前冻结完整计划；禁止边运行边解释草稿 | 独立验收：固定 Revision、计划、canonical owner 授权、能力报告与结构化诊断通过 | `requirements/TF-WF-003.md` | verified |
| TF-WF-004 | Workflow 草稿与不可变修订 | 主画布/平台内核 | Foundation -> V0 -> V1 Core | P0 | 画布位置与执行 hash 分离；任何运行固定 revision | 独立验收：Draft/Proposal 完整 hash CAS、发布、运行隔离、diff 与回滚测试通过 | `requirements/TF-WF-004.md` | verified |
| TF-WF-005 | Artifact、Resource 与 lineage | 平台内核/资源库 | Foundation | P0 | ArtifactVersion 是运行产物真相；World/Character 等为一等资源 | 独立验收：不可变版本链、CAS、lineage、stale、跨 owner 与显式提升测试通过 | `requirements/TF-WF-005.md` | verified |
| TF-WF-006 | 持久 DAG 执行与异步恢复 | 平台内核 | Foundation -> V0 -> V1 Core | P0 | V0 最小持久运行；V1 完成 attempt、lease、epoch、task binding、outbox 和恢复 | Foundation + V0 hardening 独立验收：固定计划、phase-2 fence、lease recovery、outbox/unknown/取消回归通过；完整 V0/V1 待交付 | `requirements/TF-WF-006.md` | in_delivery |
| TF-WF-007 | 控制流、批处理、子工作流与局部运行 | 平台内核/主画布 | V1 Core | P0 | Condition、Join、Fallback、有限 Map、OrderedMap/Fold；允许固定修订、有限深度、无递归 SubworkflowCall | 分支、缺失输入、checkpoint、调用深度、上游/下游闭包测试 | `requirements/TF-WF-007.md` | in_delivery |
| TF-WF-008 | Human Gate 与 RequestInput | 主画布/平台内核 | V1 Core | P0 | 决策持久化；智能体可等待用户；前端弹窗不是事实源 | waiting_user、恢复、超时、重复提交和强制 gate 测试 | `requirements/TF-WF-008.md` | in_delivery |
| TF-WF-009 | 工作流模板与依赖包 | 模板入口/平台内核 | V0 -> V1 Core | P0 | V0 内置模板；V1 完成 typed dependency、依赖闭包和替换槽；社区上架由 TF-COM-003 管理 | 内置模板复制、依赖解析、缺失/私有依赖阻断测试 | `requirements/TF-WF-009.md` | in_delivery |
| TF-WF-010 | 通用业务节点目录与 WorkbenchTask | 主画布/领域工作台 | V0 -> V1 Core | P0 | Brief、Constraint、Structured Generate、Model Router、Variants、Select/Rank、Review、Transform、WorkbenchTask、Package Export | 两个专业基准工作流只用公共节点与领域工作台即可复刻 | `requirements/TF-WF-010.md` | in_delivery |

### 7.3 智能体与开放扩展

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-AGT-001 | AgentDefinition 与不可变修订 | Agent Studio/平台内核 | V1 Core | P0 | managed preset 与 configurable 分离；运行固定 AgentRevision | 创建、发布、回滚、权限和运行固定测试 | `requirements/TF-AGT-001.md` | in_delivery |
| TF-AGT-002 | 自定义智能体 Studio | Agent Studio | V1 Core | P0 | SOP、skills、tools、I/O schema；V1 禁止嵌套智能体/工作流 | 自定义 Agent 从定义到画布调用 E2E | `requirements/TF-AGT-002.md` | in_delivery |
| TF-AGT-003 | Workflow Architect Agent | 默认入口/主画布 | V1 Core | P0 | 只提案、校验、估算和 diff；不得绕过确认直接改图 | 恶意提案、过期 hash、越权、预算和确认测试 | `requirements/TF-AGT-003.md` | in_delivery |
| TF-AGT-004 | 多智能体显式编排 | 主画布 | V1 Core | P1 | 每个智能体是可见节点；共享状态只通过强类型产物 | 多 Agent 顺序/并行、人工等待和失败归属测试 | `requirements/TF-AGT-004.md` | in_delivery |
| TF-AGT-005 | Tool 注册、凭证绑定与 Agent 执行策略 | Agent Studio/平台内核 | V1 Core | P0 | V1 仅允许平台托管或审批工具；最小权限、网络策略、数据披露和输出净化 | 越权、数据外传、凭证泄漏、撤权和审计测试 | `requirements/TF-AGT-005.md` | in_delivery |
| TF-AGT-006 | Skill 定义、修订与受控装配 | Agent Studio/资源库/平台内核 | V1 Core | P0 | Skill 只承载版本化指令、知识和示例，不执行代码/网络/工具；运行固定 SkillRevision | 创建、兼容校验、token 预算、冲突、授权、撤权和 Agent 装配测试 | `requirements/TF-AGT-006.md` | in_delivery |
| TF-MR-001 | Media Recipe 定义、实验室与调用 | Media Recipe Lab/主画布 | V1 Core | P0 | 底层图封装为固定修订调用；V1 不开放任意代码 | 配方编辑、版本、typed I/O、调用和失败映射 E2E | `requirements/TF-MR-001.md` | in_delivery |

### 7.4 小说、世界观与剧本创作

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-STY-001 | Idea、Creative Brief 与约束输入 | 默认入口/小说工作区 | V0 | P0 | 支持快速输入与结构化约束；不强迫先搭画布 | 从目标表单生成合法输入 Artifact E2E | `requirements/TF-STY-001.md` | defined |
| TF-STY-002 | 官方世界观智能体与工作台 | 小说工作区 | V1 Core | P0 | 输出世界规则、势力、地点、时间线、视觉圣经和初始 OC 集 | 创建、补问、编辑、版本、确认和引用 E2E | `requirements/TF-STY-002.md` | defined |
| TF-STY-003 | 角色 IP/OC 资源与工作台 | 小说/影视工作区/资源库 | V1 Core | P0 | 角色是独立资源，包含身份、外观、声音、变体和授权 | 世界包提升、独立编辑、版本固定和跨作品引用测试 | `requirements/TF-STY-003.md` | defined |
| TF-STY-004 | 官方小说框架智能体 | 小说工作区 | V1 Core | P0 | 六阶段内部不可拆；主画布一个节点；允许 RequestInput | idea/world/OC 到框架、暂停恢复、修订和严格输出测试 | `requirements/TF-STY-004.md` | defined |
| TF-STY-005 | 官方扩写智能体 | 小说工作区 | V1 Core | P0 | 内部管理章节规划、顺序扩写、记忆和连续性；主画布一个节点 | 长篇 checkpoint、单章重跑、连续性阻断和恢复测试 | `requirements/TF-STY-005.md` | defined |
| TF-STY-006 | 小说/框架到影视剧本改编 | 小说/影视工作区 | V1 Core | P1 | 保留来源 span、人物和世界约束；不直接生成镜头图片 | 结构化剧本、来源追踪和人工修订 E2E | `requirements/TF-STY-006.md` | defined |

### 7.5 影视资产、镜头与媒体生产

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-MED-001 | 角色/场景/道具视觉资产生产 | 影视工作区/资源库 | V0 -> V1 Core | P0 | 多候选、人工选择、衍生资产、明确引用用途 | 资产计划、变体、选择、版本和 stale 传播 E2E | `requirements/TF-MED-001.md` | defined |
| TF-MED-002 | 镜头规划智能体与 V0 ShotPlan 预览 | 影视工作区 | V0 -> V1 Core | P0 | V0 接受 CreativeBrief 或剧本并输出可编辑 ShotPlan；不把每个镜头变成主画布节点 | brief/剧本到 typed ShotPlan、覆盖率、逐镜排序选择和人工修订测试 | `requirements/TF-MED-002.md` | defined |
| TF-MED-003 | 镜头级分镜控制工作台 | 影视工作区 | V1 Core | P0 | 字段级唯一权威；故事板、动作板、宫格和 3D 通过版本向量共享上下文 | 逐镜编辑、批量、单镜重跑、视图切换和保存恢复 E2E | `requirements/TF-MED-003.md` | defined |
| TF-MED-004 | 多宫格与动作板 | 分镜控制工作台 | V1 Core | P0 | 支持 sequence/coverage/action/variation；宫格是派生产物 | 9/16/25 宫格生成、切分、逐格修复和回写测试 | `requirements/TF-MED-004.md` | defined |
| TF-MED-005 | 轻量 3D 导演台 | 分镜控制工作台 | V1 Core | P0 | 静态关节素模、姿势库、站位、机位、焦段、基础灯光和轨迹；不含完整骨骼动画/DCC | 可操作场景任务集、控制稿、序列化、桌面编辑和移动查看验证 | `requirements/TF-MED-005.md` | defined |
| TF-MED-006 | ShotSpec、控制层与 Provider 编译 | 分镜控制/平台内核 | V1 Core | P0 | 控制可叠加；每个控制片段必须裁决为 applied/transformed/degraded/ignored_with_warning/blocked | capability matrix、编译报告、冲突和无静默丢失测试 | `requirements/TF-MED-006.md` | defined |
| TF-MED-007 | 电影语言、摄影配置与连续性检查 | 分镜控制工作台 | V1 Core | P0 | 基础 coverage/cut/轴线连续性为 Core；高级风格建议渐进交付；默认不用未授权导演姓名承诺风格 | 轴线、视线、方向、景别、焦段、灯光连续性测试 | `requirements/TF-MED-007.md` | defined |
| TF-MED-008 | 角色身份锚定与一致性审查 | 影视工作区/平台内核 | V1 Core | P0 | 多参考条件降低漂移但不保证不变脸；需可审计评分 | 引用版本、用途、顺序、评分、重试和 gate 测试 | `requirements/TF-MED-008.md` | defined |
| TF-MED-009 | 图片生成、编辑与变体 | 图片/影视工作区 | V0 -> V1 Core | P0 | 通用 provider adapter；支持文生图、多参考和编辑 | 真实图片 provider、取消、重试、历史和成本 E2E | `requirements/TF-MED-009.md` | defined |
| TF-MED-010 | 视频生成与镜头控制 | 影视工作区 | V1 Core | P0 | 支持模型能力范围内的首尾帧、关键帧、轨迹/构图约束、参考视频和经真实 Provider E2E 后启用的有界 source-video modify；extend/reframe 后置 | 真实视频 provider、分片编译报告、异步恢复、modify 门和单镜替换 E2E | `requirements/TF-MED-010.md` | defined |
| TF-MED-011 | 音频、配音、音乐、音效与字幕 | 影视工作区 | V1 Core -> V1.5 | P1 | 分离对白、旁白、音乐、SFX 和字幕轨；真人声音克隆必须有同意凭证 | TTS、角色音色、字幕时间、失败恢复、撤回和授权测试 | `requirements/TF-MED-011.md` | defined |
| TF-MED-012 | 时间线、预览、片段包与有限导出 | 成片工作台 | V0 -> V1 Core -> V1.5 | P0 | V0 timeline JSON；V1 Core 视频代理/基础音轨与单镜替换；V1.5 WebAV 受限导出 | 非空预览、刷新恢复、音视频片段替换、stale 和目标设备导出测试 | `requirements/TF-MED-012.md` | defined |

### 7.6 图片与广告创作

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-IMG-001 | 产品/品牌广告图片创作工作台 | 图片/广告创作工作区 | V0 -> V1 Core | P0 | Product/Brand Brief、素材锚定、创意方向、文案/安全区、候选选择、多尺寸版式和交付包；底层出图由 TF-MED-009 提供 | 产品 brief 到三张真实候选、人工选择及多尺寸广告图包 E2E，品牌与产品约束通过评测 | `requirements/TF-IMG-001.md` | defined |

### 7.7 社区、授权与复用

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-COM-001 | 创作作品发布与创作者主页 | 社区 | V1 Community | P0 | 发布 CreativeWorkRevision；不等同开放源工作流 | 草稿/审核/发布/撤回、详情页和来源展示 E2E | `requirements/TF-COM-001.md` | defined |
| TF-COM-002 | 世界观与 OC 发布、收藏和引用 | 社区/资源库 | V1 Community | P0 | 展示与复用分开；引用固定 revision/grant snapshot | 加入资源库、引用、升级、撤权后新旧使用测试 | `requirements/TF-COM-002.md` | defined |
| TF-COM-003 | 工作流模板发布、克隆与派生 | 社区/主画布 | V1 Community | P0 | 发布包必须闭合依赖或声明替换槽位 | 校验、预览、克隆、缺失依赖和 lineage 测试 | `requirements/TF-COM-003.md` | defined |
| TF-COM-004 | 授权、署名、使用凭证与派生谱系 | 社区/平台内核 | V1 Community | P0 | reference/derivative/commercial 分权；历史合法快照不被追溯破坏 | 权限矩阵、署名渲染、撤权和派生链测试 | `requirements/TF-COM-004.md` | defined |
| TF-COM-005 | 社区发现、搜索与个人资源库 | 社区/资源库 | V1 Community | P1 | 基础检索和筛选；不做复杂推荐或交易排名 | 类型/标签/作者/授权筛选和加入资源库 E2E | `requirements/TF-COM-005.md` | defined |
| TF-COM-006 | 内容审核、举报与社区治理 | 社区/运营后台 | V1 Community | P0 | 发布前后审核、举报和处置；不以模型结果代替人工责任 | 状态机、申诉/处置审计、敏感内容阻断测试 | `requirements/TF-COM-006.md` | defined |
| TF-COM-007 | Agent 与 Media Recipe 独立发布和安装 | 社区/Agent Studio/Media Recipe Lab | V1.5 | P1 | V1 Community 只允许随工作流内嵌合规修订或依赖官方 preset；V1.5 增加独立可发现 listing | 包校验、安装、固定修订、授权、替换依赖和下架测试 | `requirements/TF-COM-007.md` | defined |

### 7.8 平台能力、安全与运营

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-OPS-001 | Provider、模型与密钥管理 | 设置/平台内核 | Foundation -> V1 Core | P0 | 密钥不进图和日志；能力快照、选择策略和实际调用记录均版本化 | Foundation + V0 hardening 独立验收：持久 invocation、rejected callback dedupe、phase-2 fencing 与结果事务回归通过；真实 Provider spike、健康、fallback 和轮换待交付 | `requirements/TF-OPS-001.md` | in_delivery |
| TF-OPS-002 | 成本、配额、预算与用量 | 设置/运行内核 | V0 -> V1 Core | P0 | 运行前估算/预留，运行后实际记账；V1 可无支付 | 超预算阻断、偏差、取消和用户用量展示测试 | `requirements/TF-OPS-002.md` | defined |
| TF-OPS-003 | 文件、Blob 与媒体资产存储 | 资源库/平台内核 | Foundation -> V0 | P0 | Foundation Blob/UploadSession 合同已验证；V0 继续真实对象存储与可恢复上传 | Foundation 独立验收：完整性、引用保护、生命周期与重建；V0 上传/签名读取待交付 | `requirements/TF-OPS-003.md` | in_delivery |
| TF-OPS-004 | RunEvent、通知与任务状态 | 产品外壳/平台内核 | V0 -> V1 Core | P0 | 数据库事件是真相，SSE/WebSocket 只是投递 | after_seq 回放、重连、通知去重和状态恢复测试 | `requirements/TF-OPS-004.md` | defined |
| TF-OPS-005 | 审计、可观测性与安全错误 | 运营/平台内核 | Foundation | P0 | 外部展示安全错误，内部保留关联 ID 和完整诊断 | 日志/指标/trace、敏感信息清理和故障定位演练 | `requirements/TF-OPS-005.md` | defined |
| TF-SEC-001 | 权限、内容安全与素材权利 Gate | 平台内核/导出 | Foundation -> V0 -> V1 Core | P0 | 编译期 canonical owner/GrantSnapshot 准入门已验证；其余安全与权利能力后续交付 | 编译期跨 owner 绕过拒绝已验收；同意、撤回、审核、披露与导出待交付 | `requirements/TF-SEC-001.md` | in_delivery |
| TF-NFR-001 | 性能、可访问性与响应式基线 | 全前端 | V0 -> V1 Core | P0 | 桌面完整编辑；移动端查看和有限操作；数值目标覆盖 50 节点、100 缩略图和 51 镜头 | 浏览器/视口矩阵、长文本、容量、延迟、键盘和无重叠视觉测试 | `requirements/TF-NFR-001.md` | defined |
| TF-NFR-002 | 数据导出、删除、保留与灾难恢复 | 设置/平台内核 | V1 Core | P1 | 删除不得破坏被授权引用的历史证据；Blob 按策略清理 | 导出、软删/硬删、备份恢复和引用保护测试 | `requirements/TF-NFR-002.md` | defined |

### 7.9 已跟踪的 V2 能力

| ID | 需求 | 全局位置 | 目标版本 | 优先级 | 边界摘要 | 交付证据 | 详细文档 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TF-LNG-001 | 生产级长篇、长剧集与批量调度 | 小说/影视工作区/运行内核 | V2 | P1 | V1 支持有界单项目创作；V2 增加季/集/章节生产单元、跨单元连续性、批量调度和恢复 | 多集基准、跨集变更传播、批量审批和恢复演练 | `requirements/TF-LNG-001.md` | deferred |
| TF-TEAM-001 | 团队空间、角色权限与多人协作 | 产品外壳/全工作区 | V2 | P1 | 组织、项目角色、评论审阅、锁/冲突和协作；不改变不可变运行真相 | 权限矩阵、并发编辑、审阅交接和审计 E2E | `requirements/TF-TEAM-001.md` | deferred |
| TF-MKT-001 | 创作能力市场、交易与生态治理 | 社区/结算/运营 | V2 | P2 | Agent、Recipe、模板与资源的付费、分成、退款、排名和反滥用；免费发布不依赖本项 | 沙箱交易、结算对账、退款、下架、争议和排名治理演练 | `requirements/TF-MKT-001.md` | deferred |

## 8. 核心公共接口与类型

以下接口是跨需求合同，详细需求不得自行创造不兼容替代物。

### 8.1 内容、资源与修订真相

```text
ArtifactVersion
  artifact_id
  artifact_version_id
  schema_id / schema_version
  owner_scope
  content_uri | content_json
  created_by_run_id?
  lineage_input_refs[]

Resource
  resource_type / resource_id
  owner_scope

ResourceDraft
  resource_id / draft_version
  base_revision_id?
  content_artifact_version_id

ResourceRevision
  resource_id / revision_id / revision_number
  content_artifact_version_id
  revision_status
  created_from_artifact_version_id?

ResourceRef
  resource_type / resource_id / revision_id
  role
  grant_snapshot_id?        # 同 owner 可空，跨 owner 强制

ArtifactRef
  artifact_id / artifact_version_id
  schema_id / schema_version

CreativeWorkContent
  title / summary / cover_ref
  primary_media_refs[]
  source_revision_refs[]
  attribution_manifest[]
  public_metadata
```

规则：

1. `ArtifactVersion` 是不可变内容载体；运行节点只能产生新版本，不能原地修改输入。
2. `Resource` 提供稳定业务身份；可变编辑只发生在 `ResourceDraft`。运行、引用、发布、导出或社区 listing 必须先冻结为 `ResourceRevision`。
3. Agent 输出先成为 ArtifactVersion；“提升为世界观/OC/ShotPlan/作品”等操作创建或更新 ResourceDraft，并在确认时生成指向该内容的 ResourceRevision。
4. 工作台保存生成新的内容 ArtifactVersion 并推进 `draft_version`；提交运行或发布时使用 compare-and-swap 冻结 Revision。运行中的 Revision 永不被草稿覆盖。
5. 上游 Revision 改变时，下游只标记 `stale` 并给出重算建议，不改写历史 ArtifactVersion、Revision、Run 或授权证据。
6. `WorldRevision`、`CharacterRevision`、`ShotPlanRevision`、`ShotSpecRevision`、`DirectorSceneRevision`、`CinematicGrammarProfileRevision`、`TimelineRevision`、`CreativeWorkRevision`、`AgentRevision` 和 `MediaRecipeRevision` 都是带专用 schema 的 ResourceRevision，不另建平行版本真相源。
7. 从 WorldRevision 内嵌角色提升为独立 Character Resource 时，必须保存来源 world revision、原角色局部 ID 和提升事件；后续分叉不回写或覆盖来源 Revision。
8. ArtifactRef 只在同一 owner_scope 内直接授权访问。跨 owner 媒体、控制稿、关键帧或知识内容必须先提升为专用 ResourceRevision；ResourceRef 的授权可覆盖其 revision 内容中固定的 ArtifactVersion。

### 8.2 分层调用合同

| 调用方 | V1 允许调用 | V1 禁止调用 | 强制记录 |
| --- | --- | --- | --- |
| 主 Workflow | 注册业务节点、固定修订 Agent、固定修订 Media Recipe、有限深度 Subworkflow、WorkbenchTask | 递归子工作流、运行中读取 latest、任意代码 | 编译计划、输入快照、节点 trace、成本和失败归属 |
| Agent | 平台批准的模型与 ToolInvocation、有界 SOP 状态、RequestInput | 其他 Agent、Workflow、Subworkflow、Media Recipe、任意网络/代码 | 内部 step trace、checkpoint、工具输入最小披露、用量和 typed output |
| Media Recipe | 媒体算子、provider、转换、评分和有限 DAG | Agent、Workflow、Recipe 嵌套、Human Gate、RequestInput、任意代码 | 算子 trace、能力快照、provider 请求、降级和成本 |
| Workbench | 读取固定 Revision、编辑 ResourceDraft、提交 Revision、显式请求运行 | 直接改写 Run/NodeRun、绕过编译器调用 provider | base revision、draft version、diff、提交者和触发的 run_id |

Agent 与 Media Recipe 虽可通过平台运行时使用批准的模型/provider，但不存在简化的外部调用旁路。任何网络副作用都必须遵守第 8.4 节的 ProviderInvocationAttempt、双 Outbox、unknown 对账、fencing 和多 OutputBinding 合同；Agent/Recipe trace 只引用运行时形成的调用记录，不能自行伪造成功、成本或候选。

主 Workflow 的 `WorkbenchTask` 必须携带输入快照、目标工作台、期望 typed output 和完成/取消状态；用户在工作台提交后才产生下游可见的 ArtifactRef/ResourceRef。

```text
WorkbenchActionRequest
  workbench_task_id / action_type             # WorkbenchActionType
  scope_refs[]                                # WorkbenchScopeRef tagged union
  requested_input_refs[]                      # 用户已选择的固定 Revision/Artifact refs
  draft_input_selectors[]                     # 需从 EditContextSnapshot 冻结的 DraftInputSelector
  edit_context_snapshot_ref
  expected_draft_versions[]
  config_ref / expected_output_schema
  idempotency_key

InputFreezeManifest
  freeze_manifest_id / workbench_action_request_id
  requested_input_refs[] / actual_input_revision_refs[]
  resolved_draft_entries[]                    # resource/draft version -> frozen revision
  edit_context_snapshot_ref / snapshot_fingerprint

DraftInputSelector
  resource_type / resource_id
  expected_base_revision_id / expected_draft_version

WorkbenchActionResult
  workbench_action_request_id / child_run_id
  input_freeze_manifest_ref
  actual_input_revision_refs[]
  output_refs[] / usage / actual_cost
  provider_compilation_report_ref?
  writeback_proposal_ref?

WorkbenchActionType
  provider.precompile | board.generate | grid.generate | grid.cell.regenerate
  director_scene.export_controls | continuity.check
  shot.generate | shot.rerun

WorkbenchScopeRef
  scope_kind                                  # project | sequence | coverage | shot | temporal_anchor
  project_ref? / sequence_group_ref? / coverage_group_ref?
  shot_revision_ref? / temporal_anchor_ref?

WorkbenchWritebackProposal
  proposal_id / source_action_result_ref
  base_revision_refs[] / expected_draft_versions[]
  typed_patch_sets[]                          # target resource + owned fields + patch schema/version
  lineage_refs[] / impact_refs[] / stale_effects[]

TypedWorkbenchPatchSet
  patch_set_id / target_resource_type / target_resource_id
  base_revision_id / expected_draft_version
  edit_context_snapshot_ref / source_mapping_ref?
  owned_field_paths[] / patch_schema_id / patch_schema_version
  patch_artifact_ref / proposal_fingerprint

ApplyWorkbenchProposalRequest
  proposal_ref / accepted_patch_set_ids[]
  apply_mode                                  # atomic | per_target；默认 atomic
  expected_draft_versions[] / client_request_id

ApplyWorkbenchProposalResult
  proposal_ref / client_request_id / apply_mode
  patch_results[]                             # applied | conflicted | skipped + current draft version
  committed_atomically / resulting_snapshot_ref?
```

WorkbenchActionRequest 是工作台请求预编译、生成、渲染控制稿、检查或局部重跑的唯一运行入口。`action_type` 与 `scope_refs` 必须使用公共枚举/tagged union，禁止各工作台私造裸字符串语义。客户端以 requested_input_refs 提交已固定 Revision/Artifact，以 draft_input_selectors 明确声明需从 EditContextSnapshot 冻结的 ResourceDraft，并同时提交完整 expected draft versions；Draft 不得伪装成 requested_input_ref。运行时在创建 child run 前原子验证 selector、snapshot 与版本向量，冻结所需 Draft Revision，并生成服务端 InputFreezeManifest，`actual_input_revision_refs` 只能来自该 Manifest。Result 只产生不可变输出和可选 WorkbenchWritebackProposal，不得直接改写 ResourceDraft。用户接受提案后通过 ApplyWorkbenchProposalRequest 逐资源 CAS；默认 `apply_mode=atomic`，任一 patch 冲突则全部不应用，显式 per_target 才允许部分成功并逐项返回结果。跨镜最终冻结仍使用 ShotPlanCommitManifest；取消或晚到 Result 只能作为隔离候选保留。

官方 managed Agent 节点的单卡片只是产品展示聚合。编译器必须生成 `ManagedAgentTaskPlan`，至少列出有序 AgentInvoke、RequestInput、workflow-owned WorkbenchTask/Human Gate、ResourceCommit 及每步 typed I/O。Agent 不能创建或调用这些 workflow task；运行详情和高级画布必须可展开计划。自定义 Agent 若需要富内容人工编辑，工作流作者必须显式连接 WorkbenchTask。

### 8.3 调用、镜头与控制合同

```text
AgentInvoke
  agent_revision_id
  typed_inputs
  config

MediaRecipeInvoke
  media_recipe_revision_id
  typed_inputs
  config

ShotPlanRevision
  resource_id / revision_id
  source_refs[]
  ordered_shot_revision_refs[]
  sequence_groups[] / coverage_groups[] / cut_relations[]
  cinematic_style_stack_refs[]
  plan_constraints

StoryBeatRef
  source_revision_ref / story_beat_id
  source_spans[] / label?

ShotSequenceGroup
  sequence_group_id / scene_ref?
  ordered_shot_revision_refs[]
  master_axis? / screen_direction_policy?
  grammar_profile_ref? / color_script_ref? / framing_ref?

CoverageGroup
  coverage_group_id / story_beat_ref
  coverage_objective / required_shot_roles[]
  member_shot_revision_refs[]

CutRelation
  cut_relation_id / from_shot_revision_ref / to_shot_revision_ref
  from_anchor_ref? / to_anchor_ref?             # ShotTemporalAnchorRef
  temporal_relation / match_intent / transition
  axis_transition_policy? / screen_direction_policy?

ShotSpecRevision
  resource_id / revision_id
  shot_id
  source_spans[] / duration_ms
  shot_role / shot_size / subject_coverage / edit_intent
  narrative / composition_intent / continuity_intent
  camera_intent / lighting_intent / look_intent
  beats[] / control_layers[]
  generation_policy / selected_output_refs[]

BeatOrKeyframe
  beat_id
  time_ms | normalized_time
  intent / pose_intent / performance
  frame_ref?

ShotTemporalAnchorRef
  shot_resource_id
  shot_revision_ref? | shot_candidate_ref?      # 后者为 expected_resource_type=ShotSpec 的 CandidateRevisionRef
  anchor_kind                                # beat | shot_start | shot_end | shot_default
  beat_id?                                   # anchor_kind=beat 时必填且在该 Shot 内唯一

CompositionIntent
  subject_anchors[] / framing_pattern?
  headroom? / lead_room? / negative_space?
  depth_layers[] / horizon? / vanishing_points[]

StagingGeometrySpec
  staging_geometry_id / action_axis_world?
  actor_world_sides[] / gaze_targets[]
  entry_exit_world_directions[] / movement_world_directions[]

ScreenGeometryObservation                    # 派生 ArtifactVersion 内容
  source_director_scene_revision_ref / camera_id
  target_anchor_ref? / path_t? / canvas_kind  # capture | delivery
  canvas / subject_screen_sides[] / gaze_vectors[]
  entry_exit_screen_directions[] / movement_screen_directions[]

ShotSetupVariant                            # 不可变 ArtifactVersion 内容
  variant_id / base_shot_revision_ref
  base_director_scene_revision_ref?
  changed_dimensions[] / typed_overrides[]
  candidate_output_refs[]

ShotSetupVariantOverride
  owner_domain                               # shot_spec | director_scene
  target_resource_ref / owned_field_path
  value_schema_id / value_schema_version / value_artifact_ref

SourceSpanCoverageReport
  source_revision_ref / shot_plan_revision_ref
  covered_spans[] / duplicate_spans[] / uncovered_spans[]
  constraint_results[]

ControlLayer
  control_layer_id / type / source_ref
  target_scope                                 # sequence | coverage | shot；默认显式 shot
  target_anchor_ref?                           # ShotTemporalAnchorRef
  source_selector?                             # ControlSourceSelector tagged union
  coordinate_space / source_time_range?
  strength? / required                         # source_video 使用 fragment strength，不设 layer strength
  unsupported_policy       # block | degrade | ignore_with_warning

ControlSourceSelector
  selector_kind                               # board_panel | grid_cell | director_component
                                              # director_control_export
                                              # source_video_fragment | tracking_region | artifact_fragment
  board_panel_ref?                            # selector_kind=board_panel 时唯一 payload
  grid_artifact_ref? / cell_index?            # selector_kind=grid_cell
  director_component_refs[]?                  # selector_kind=director_component
  director_control_export_ref? / export_item_ids[]
  source_video_control_spec_ref? / source_video_fragment_ids[]
  tracking_ref? / region_ids[]
  artifact_ref? / fragment_path?

DirectorComponentRef
  director_scene_revision_ref
  component_kind                              # camera | camera_motion | camera_framing_constraint
                                              # staging_geometry | exposure | environment_lighting
                                              # light_emitter | lighting_role_assignment | light_modifier
                                              # actor_instance | static_pose
  component_id / fragment_path?               # fragment_path 可定位 translation/roll/focus/emitter/role 等
  target_anchor_ref? / path_t_range?

SourceVideoControlSpec
  source_video_ref / operation                # reference | modify
  source_time_range / target_time_range? / retime_policy
  fragments[]

SourceVideoControlFragment
  fragment_id / fragment_kind                 # motion | structure | body_pose | face_performance
                                              # mask_region | style | appearance_edit
  control_mode                                # preserve | transfer | replace | relax
  strength_0_to_1 / required / unsupported_policy
  mask_ref? / mask_manifest_ref? / tracking_ref? / prompt_ref?

VideoControlMaskManifest
  mask_artifact_ref / source_video_ref
  timebase / source_time_range / canvas / resize_policy
  tracked_object_ids[] / label_map_ref?

BoardArtifact
  board_kind               # storyboard | action
  source_revision_refs[]
  ordered_panels[]

BoardGenerationSpec
  board_kind / ordered_source_mappings[]       # Shot/ShotTemporalAnchorRef + panel role
  reference_refs[] / control_layer_refs[]
  canvas / provider_policy_ref / panel_count_policy

BoardSourceMapping
  mapping_id / order / source_kind             # shot | temporal_anchor
  shot_revision_ref? / temporal_anchor_ref?    # 按 source_kind 恰有一个
  panel_role / caption_hint?

BoardPanel
  panel_id / shot_revision_ref
  target_anchor_ref? / frame_ref?
  annotation_ref? / caption?

GridArtifact
  layout                   # 9 | 16 | 25 | custom-approved
  mode                     # sequence | coverage | action | variation
  grid_template_ref / rows / columns / reading_order
  composite_canvas / gutter / cell_rects[]
  source_revision_refs[]
  cell_mappings[]          # GridCellMapping tagged union
  composite_output_ref?
  cell_results[]

GridTemplateRef
  template_resource_id / template_revision_id
  template_schema_id / template_schema_version

GridCellRect
  cell_index / x / y / width / height          # composite canvas normalized coordinates

GridCellResult
  cell_index / mapping_ref
  status / output_ref? / failure_reason?

GridCellMapping
  cell_index / mapping_kind                    # board_panel | shot | coverage_member | temporal_anchor | variant
  canonical_writeback_target
  board_panel_ref? / shot_revision_ref?        # 按 mapping_kind 恰有一个来源 payload
  coverage_member_ref? / temporal_anchor_ref? / variant_ref?
  expected_role?

BoardPanelRef
  board_artifact_ref / panel_id

CoverageMemberRef
  shot_plan_revision_ref / coverage_group_id / member_shot_revision_ref

ShotSetupVariantRef
  variant_artifact_ref / variant_id / base_shot_revision_ref

CanonicalWritebackTarget
  target_kind                                 # shot_field | shot_beat | director_scene_field
                                              # plan_field | coverage_member
  target_resource_type / target_resource_id / base_revision_id
  owned_field_path
  temporal_anchor_ref? / coverage_member_ref?

DirectorSceneRevision
  resource_id / revision_id
  target_scope             # sequence | coverage | shot
  scope_refs[]
  units / coordinate_system
  scene_asset_refs[] / actor_instances[] / prop_instances[]
  static_poses[] / actor_pose_bindings[] / blocking_keyframes[]
  cameras[] / camera_motions[]
  camera_framing_constraints[] / staging_geometry_specs[]
  light_emitters[] / lighting_role_assignments[] / light_modifiers[]
  exposure_specs[] / environment_lighting_specs[]

StaticPose
  pose_id / skeleton_convention / joint_rotations[]

ActorPoseBinding
  actor_instance_id / target_anchor_ref / pose_id

BlockingKeyframe
  actor_instance_id / target_anchor_ref
  root_transform / gaze_target? / prop_contacts[]

CameraSpec
  camera_id / transform
  sensor_width_mm / sensor_height_mm / focal_length_mm / derived_fov
  squeeze_factor / aperture
  focus_target? / focus_distance?
  capture_canvas / delivery_canvas / crop / protection / safe_frame

CameraFramingConstraint
  framing_constraint_id / camera_id
  target_anchor_ref? / path_t_range?
  canvas_kind                                # capture | delivery
  normalized_coordinate_space                # 固定为 [0,1] top-left origin
  subject_refs[] / target_screen_regions[] / target_coverage?
  fit_mode                                   # contain | center | cover
  headroom? / lead_room? / occlusion_policy? / tolerance
  sampling_policy / sample_count? / priority
  derived_from_composition_intent_ref?

CameraMotionSpec
  camera_motion_id / camera_id / rig_mode
  translation_path[] / orientation_track[] / optical_track[]
  translation_interpolation / orientation_interpolation / optical_interpolation
  timing_bindings[] / motion_quality_spec

CameraPathPoint
  point_id / path_t / position

CameraOrientationKey
  key_id / path_t / rotation? / look_at_target? / roll?

CameraOpticalKey
  key_id / path_t / focal_length_mm? / focus_distance? / aperture?

CameraTimingBinding
  target_anchor_ref / path_t / speed_profile_ref? / easing_hint?

CameraSpeedProfile
  speed_profile_ref / profile_revision
  normalized_speed_knots[]                    # path_t/value，0..1 且 path_t 单调
  easing_family / acceleration_limit? / jerk_limit?

MotionQualitySpec
  stabilization_mode / smoothing_amount
  handheld_preset_ref? / procedural_seed? / handheld_amount?

ExposureSpec
  exposure_spec_id / camera_id
  shutter_angle? / exposure_index? / nd_stops? / exposure_compensation?
  white_balance_temperature? / white_balance_tint?

EnvironmentLightingSpec
  environment_lighting_id / environment_type
  sun_direction? / sky_intensity? / ambient_intensity? / intensity_unit
  color_space / temperature? / tint?
  time_of_day? / environment_map_ref? / precedence_policy

LightEmitterSpec
  emitter_id / emitter_type / transform
  intensity / value_unit / temperature / tint
  size / shape / beam / spread / enabled

LightingRoleAssignment
  assignment_id / role / emitter_ids[] / motivated_source_ref?

LightModifierSpec
  modifier_id / modifier_type / transform
  target_emitter_ids[] / parameters

ControlFrameManifest
  control_artifact_ref / source_director_scene_revision_ref
  camera_id / target_anchor_ref? / control_kind
  coordinate_system / units / projection / near_far
  canvas / crop / resize_policy
  skeleton_convention? / segmentation_label_map_ref?

DirectorSceneControlExport                    # ArtifactVersion content schema
  export_id / source_director_scene_revision_ref / export_config_ref
  camera_id / target_anchor_refs[]
  items[]                                     # DirectorSceneControlExportItem
  child_run_id

DirectorSceneControlExportItem
  item_id / item_kind                         # control | manifest | camera_metadata | lighting_metadata
  artifact_ref / control_kind? / manifest_ref?

CinematicStyleStack
  style_stack_id / layers[]

CinematicStyleLayer
  layer_id / layer_order
  scope / scope_ref       # project | sequence | coverage | shot + typed ref
  domain                  # framing | optics | movement | blocking | lighting | look | edit_rhythm
  intent_tags[] / parameter_overrides[] / reference_refs[]
  merge_policy            # replace | merge_named | append_ordered
  provenance / confidence?

CinematicParameterOverride
  parameter_path / parameter_schema_id / parameter_schema_version
  value / unit? / merge_operator

ShotPlanEditSession
  session_id / base_shot_plan_revision_id
  plan_draft_ref / shot_draft_refs[] / director_scene_draft_refs[]
  edit_context_snapshot_ref
  session_version / state

EditContextSnapshot
  snapshot_id / session_id / session_version
  base_revision_refs[]
  draft_version_vector[]

DraftVersionVectorEntry
  resource_type / resource_id / base_revision_id
  draft_version / content_artifact_version_id

ShotDraftRef
  shot_resource_id / base_revision_id
  draft_version / content_artifact_version_id

RevisionCandidate
  local_candidate_id / resource_type / resource_id / base_revision_id?
  content_artifact_version_id

CandidateRevisionRef                         # 仅允许存在于 RevisionCandidate 内容
  local_candidate_id / expected_resource_type / expected_resource_id

CandidateResolutionMap
  entries[]                                   # CandidateResolutionEntry

CandidateResolutionEntry
  local_candidate_id / assigned_revision_id
  source_candidate_content_artifact_ref
  resolved_content_artifact_ref

ShotPlanCommitManifest
  session_id / idempotency_key
  expected_plan_draft_version
  expected_shot_draft_versions[]
  expected_director_scene_draft_versions[]
  plan_revision_candidate
  shot_revision_candidates[]
  director_scene_revision_candidates[]
```

V1 `ControlLayer.type` 至少支持故事板、动作板、构图标注、pose、depth、normal、segmentation、edge、clay、mask/region、3D director scene、camera、lighting、首帧、尾帧、中间关键帧、motion/performance reference、source video、style/look reference 和 negative constraint。subject/object tracking 可作为 mask/region 或 source-video 的 selector/派生控制输入；V1.5 可增加 3DGS、动作捕捉、更复杂空间重建和定时 Animatic，但不得改变 V1 已存 Revision 的解释。

ControlLayer.target_scope 在 V1 持久化内容中必填；target_anchor_ref、source_selector 与 strength 按类型条件必填：关键帧、动作和任何锚点局部控制必须固定 ShotTemporalAnchorRef，多对象/多片段来源必须用 ControlSourceSelector 精确定位，只有覆盖整个 target_scope 的单一 Artifact 控制可省略相应字段。source video 的语义强度必须先保存为不可变 SourceVideoControlSpec，每个 fragment 独立声明模式、归一化强度、required 与 unsupported policy；source-video ControlLayer 不再保存顶层 strength，VideoReferenceBinding 也不得另建平行分片强度真相。编译器必须按 layer type/schema 校验条件，不能把缺失 selector 当作“默认第一个对象”。

字段级权威规则：ShotPlanRevision 唯一拥有镜头顺序、ShotSequenceGroup、CoverageGroup 和计划 CutRelation；ShotSpecRevision 唯一拥有单镜抽象叙事、CompositionIntent、摄影、灯光、look、生成和选择意图；BeatOrKeyframe 唯一拥有镜内时间、表演意图和选中关键帧 `frame_ref`；DirectorSceneRevision 唯一拥有精确世界空间变换、关节姿势、StagingGeometrySpec、相机/构图约束、路径、曝光和灯具参数。BoardArtifact、GridArtifact、ShotSetupVariant、ScreenGeometryObservation、DirectorSceneControlExport、ControlFrameManifest 和检查报告均为固定来源的不可变派生产物，不得成为第二套镜头模型。ShotSetupVariant 只是非 canonical proposal overlay，保存为固定来源的 ArtifactVersion：typed override 必须声明 owner domain、目标固定 base resource、owned field path 和 value schema；不得自有 Beat ID/time，也不得把 DirectorScene 精确参数复制到 shot_spec domain。涉及精确相机、路径、姿势或灯光时必须固定 base DirectorSceneRevision。采纳时每个 override 按 owner domain 生成 target_kind=shot_field 或 director_scene_field 的 WorkbenchWritebackProposal/patch set；Variant 只作为来源 lineage，不是回写目标。

`StoryBeatRef` 指向剧本或故事层的跨镜叙事 Beat；`BeatOrKeyframe` 只表示单镜内部时间事件，二者禁止混用。SourceSpanCoverageReport 只证明来源文本覆盖；CoverageGroup 表示同一 StoryBeat 的多个可剪辑 Shot；ShotSetupVariant 表示单 Shot 的候选设置，variation 不得伪装成 coverage。

故事板和动作板必须通过 BoardGenerationSpec 生成或由用户按同一 schema 手工组板后形成 BoardArtifact；动作 Panel 通过 ShotTemporalAnchorRef 指向镜内 Beat 或无 Beat 镜头的 start/end 锚点。9/16/25 宫格仅负责布局、批量生成、切分与逐格审查，通过 GridCellMapping 引用 BoardPanelRef、Shot、CoverageMemberRef、ShotTemporalAnchorRef 或 Variant，并用 CanonicalWritebackTarget 精确声明目标 Resource 和 owned field path。mode/mapping 兼容矩阵固定为：sequence 接受 shot 或 storyboard BoardPanelRef；coverage 接受 CoverageMemberRef，若接收 storyboard BoardPanelRef 则其 shot 必须是该 coverage member；action 接受 ShotTemporalAnchorRef 或带相同 anchor 的 action BoardPanelRef；variation 只接受 ShotSetupVariantRef。每格恰有一个来源 branch，CanonicalWritebackTarget 的 target_kind/owned field path 必须与 mode 和 branch 相容。GridArtifact 必须固定 GridTemplateRef、rows/columns、reading order、composite canvas、gutter 和每格 rect，保证历史切分可重放。复合宫格不得默认作为多角度身份控制的单一输入；编译器必须依据能力快照选择独立 cell、有序关键帧或明确支持的 composite。逐格回写发生在 canonical_writeback_target 对应草稿上；若来源已变化，必须显示三方 diff 并要求合并，禁止最后写入覆盖或把 25 个格子隐式变成 25 个新镜头。

DirectorScene 可由 sequence、coverage 或 shot 共享；所有精确时域绑定使用带 shot 命名空间的 ShotTemporalAnchorRef，避免共享场景中的同名 Beat 歧义。shot_start 确定解析为 0，shot_end 解析为 ShotSpec.duration_ms；shot_default 是不带具体时间的全镜静态锚点，只允许静态 pose/framing/lighting 等控制，不得用于 action/keyframe、CameraTimingBinding 或任何要求单调 concrete time 的轨道。单镜 ControlLayer 通过类型化 `source_selector` 选择 camera、camera_motion、camera_framing_constraint、staging_geometry、exposure、environment_lighting、light_emitter、lighting_role_assignment、light_modifier、actor_instance、static_pose、DirectorSceneControlExport item 或 Artifact fragment；`director_component` 必须使用一个或多个 DirectorComponentRef，每项固定 scene revision、component kind/id，并在需要逐片段裁决时固定 fragment_path，禁止依靠数组位置或跨类型裸 ID 猜测对象。CameraMotion 的 translation、orientation、optical 与 motion quality 必须正交保存；`path_t` 只描述空间曲线进度，实际时间由 CameraTimingBinding 引用 ShotTemporalAnchorRef，不得在路径点复制时间权威。V1 使用版本化 speed profile/基础 easing，完整速度曲线后置 V1.5。灯具发光参数、环境光、曝光/白平衡与 key/fill/rim/practical 等摄影职责分离。

StagingGeometrySpec 只保存世界空间动作轴、站位侧、视线目标和进出/运动方向；任何“画面左/右、屏幕方向、屏幕视线向量”都必须由固定 DirectorSceneRevision、camera、ShotTemporalAnchorRef/path_t 和 capture/delivery canvas 计算为 ScreenGeometryObservation。CameraFramingConstraint 使用 `[0,1]` top-left 归一化坐标，并显式声明 canvas_kind、fit mode、tolerance 和 path sampling policy；不得把相机相关屏幕结果写回世界空间权威。

CameraMotionSpec 的三类轨必须分别声明插值；所有 path_t 与 speed knots 在 0..1 内单调。V1 的 CameraSpeedProfile 是版本化预设而非任意曲线编辑器；程序化 handheld 必须固定 preset revision、seed 和 amount，确保预演可重放。CinematicStyleLayer 必须带 typed scope_ref、layer_order、merge_policy 和带 schema/path/unit 的 CinematicParameterOverride；姓名或作品 reference 只有具备策展证据或授权参考时才可解析成参数，否则只保留 reference。

所有 pose/depth/normal/segmentation/edge/clay/mask 等控制图必须带 ControlFrameManifest，固定骨骼 convention、坐标系、深度单位、投影、near/far、分割标签、画布、裁切与 resize policy；不同控制稿在进入 provider 前必须验证像素对齐。控制稿只能在固定 DirectorSceneRevision 之后由 WorkbenchAction child run 产生；child run 先创建控制/manifest/metadata ArtifactVersion，再创建以它们为 item 的 DirectorSceneControlExport ArtifactVersion。ControlLayer 通过 export ArtifactRef 与 item ID 精确选择，禁止反向回写不可变 Scene Revision。素模约束空间、构图和姿势，角色身份仍由 CharacterRevision 与身份参考控制，不得宣传为绝对防变脸。

CinematicStyleStack 允许 project、sequence、coverage、shot 逐级组合，窄作用域只覆盖明确参数；framing、optics、movement、blocking、lighting、look 和 edit_rhythm 必须分别展示参数、参考来源与置信度。无法解析的导演姓名或风格描述只保留为 reference，不得伪装成已应用参数；官方默认不以未授权姓名承诺复刻。计划 CutRelation 与 Timeline 的实际 EditDecision 分离；时间线重排、替换或改变切点后，依赖旧计划关系的连续性报告必须标记 stale。

ShotPlanEditSession 和 EditContextSnapshot 都不是 Resource 或镜头真相源。每个 ResourceDraft 独立 CAS；WorkbenchActionRequest 使用完整版本向量。Draft 内容中的 ShotTemporalAnchorRef 以 shot_resource_id 结合 snapshot 定位；RevisionCandidate 的暂存 content Artifact 在任何原本要求 ResourceRevisionRef 的跨候选字段中可用 CandidateRevisionRef，包括 ShotTemporalAnchorRef、ControlLayer.source_ref、ShotPlan ordered/group/cut refs 与 DirectorScene.scope_refs。最终提交必须在一个数据库事务中验证全部 expected draft versions，预分配所有 candidate Revision ID、按 expected resource type/id 验证引用，并为每个候选创建已把 CandidateRevisionRef 重写为 assigned Revision ID 的新 resolved content ArtifactVersion；CandidateResolutionEntry 同时记录原暂存 Artifact、新 resolved Artifact 与 assigned Revision ID。新 ResourceRevision 只指向 resolved Artifact，再验证 beat_id 归属并激活受影响 ShotSpecRevision/DirectorSceneRevision 和引用它们及计划级关系的新 ShotPlanRevision。原暂存 Artifact 保持不可变、按 session 隔离且不能作为运行输入；canonical/active/downstream API 禁止暴露 CandidateRevisionRef 或 local_candidate_id。任何候选、锚点或字段权威验证失败时不得激活部分 Revision。

### 8.4 包、Provider 与实际调用记录

```text
PackageDependency
  dependency_kind          # workflow | agent | skill | recipe | resource | schema | managed_preset
  revision_id
  schema_id / schema_version
  inclusion_mode           # included | reusable | managed | replacement_slot
  grant_requirement
  provider_capability_requirement?

WorkflowPackageManifest
  workflow_revision_id
  package_dependencies[]
  required_entitlements[]
  attribution_manifest[]

ProviderSelectionPolicyRef
  policy_revision_id

CapabilitySnapshotRef
  provider_id / model_id / capability_revision_id

ProviderCompilationReport
  report_id / input_fingerprint / compiler_revision
  capability_snapshot_ref
  items[] / summary_counts
  estimated_usage / estimated_cost

ControlCompilationItem
  control_layer_id / source_selector? / fragment_path?
  requested_type / outcome
  application_mode
  provider_field? / transformer_revision_id?
  semantic_loss? / reason_code / evidence_refs[]

ProviderInvocationAttempt
  invocation_attempt_id / node_run_attempt_id
  request_fingerprint / provider_idempotency_key
  attempt_status           # 使用公共 AttemptStatus，不建立平行 provider 状态枚举
  prepared_request_ref / compilation_report_ref
  provider_request_id? / task_binding_id?
  reconciliation_state / last_error?

OutboxEvent
  outbox_event_id / aggregate_ref
  purpose                  # provider_dispatch | result_publish
  event_type / payload_ref / dedupe_key
  publish_status / attempt_count / next_attempt_at?

ProviderInvocationRecord
  invocation_attempt_id
  provider_id / model_id / model_version
  capability_snapshot_ref
  request_fingerprint / provider_request_id
  seed? / ordered_reference_refs[]
  compilation_report_ref / fallback_from?
  output_bindings[]
  usage / actual_cost

ProviderOutputBinding
  output_index / artifact_ref
  provider_output_id?
```

包不得包含明文 secret 或 CredentialBinding。依赖闭包禁止循环；无法内嵌或复用的依赖必须声明 typed replacement slot。V1 Community 工作流可内嵌经授权的 Agent/Recipe 不可变修订，但不创建独立可发现 listing；独立发布与安装由 TF-COM-007 在 V1.5 交付。

每个 requested control 及其可独立裁决的字段片段必须在 `ControlCompilationItem` 中恰好有一个最终 outcome：`applied | transformed | degraded | ignored_with_warning | blocked`。`fragment_path` 用于分别裁决同一 camera/light layer 的 path、roll、focus、aperture 等片段；`application_mode` 至少区分 native field、derived control、reference media、prompt approximation、omitted 和 blocked。任何片段缺项、冲突未决或 required control 被忽略都视为编译失败。

每个真实外部 provider 请求对应一个 ProviderInvocationAttempt，并复用关联 NodeRunAttempt 的公共 AttemptStatus；provider 内部 submitted/queued/processing 等只作为 task binding 事件，不另建公共状态族。网络副作用前必须在同一数据库事务中提交 NodeRunAttempt、ProviderInvocationAttempt 和 `purpose=provider_dispatch` 的 OutboxEvent；事务提交后 dispatcher 才可发送。

支持 provider idempotency key 时必须使用；发送后无法确认 provider 是否接收时 AttemptStatus 进入 `unknown`，只能通过查询、回调、账单或人工对账收敛，禁止盲目重提或直接 fallback。明确需要新外部请求时，fallback 必须从原始固定输入重新做能力快照、编译、授权和成本估算，并创建新的 Attempt/dispatch outbox。

结果采用带 execution epoch/fencing token 的条件更新。在同一事务中验证 Attempt 仍可发布，写入输出 ArtifactVersion、最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本，并写入 `purpose=result_publish` 的 OutboxEvent；事务失败不得发布部分结果。只有 unknown/waiting_external 已对账并收敛到公共终态后才能形成 InvocationRecord。一个 Attempt 最多一条 InvocationRecord；一次请求的多个候选共享该 Record，只有实际发起多个外部请求时才创建多组 Attempt/Record。

### 8.5 授权证据与当前权限

```text
ResourceLocator
  resource_type / resource_id / revision_id
  listing_id?
  executable = false

LicenseOfferRevision
  offer_id / offer_revision_id
  licensor / resource_revision_id
  eligible_principal       # public | authenticated | explicit_subjects
  usage_scopes             # display | reference | derivative | commercial
  capability_actions       # clone | install | execute | redistribute
  conditions / attribution / territory / effective_period
  acceptance_required / terms_hash

LicenseAcceptance
  acceptance_id / offer_revision_id
  accepting_subject / accepted_at / terms_hash

GrantSnapshot
  grant_snapshot_id / grant_revision_id
  source_kind              # direct_grant | accepted_offer
  grant_or_offer_revision_id
  grantor / grantee / resource_revision_id
  usage_scopes / capability_actions
  terms_hash / captured_at / acceptance_id?

EntitlementDecision
  subject / action / resource_revision_id
  current_grant_status
  decision / reason / evaluated_at

Listing
  listing_id / owner_scope / listing_kind

ListingDraft
  listing_id / draft_version
  target_resource_revision_id / public_metadata_artifact_version_id

ListingRevision
  listing_id / listing_revision_id / content_hash
  target_resource_revision_id / public_metadata_artifact_version_id
  offer_revision_refs[] / attribution_manifest[]
```

直接 Grant 面向明确 grantee；面向公众或任意登录用户的复用必须通过版本化 LicenseOffer。匿名访问最多使用无需接受的 display offer；reference/clone/install/execute/redistribute 等行为按 offer 要求先产生 LicenseAcceptance 或直接 Grant，再捕获 GrantSnapshot。

`GrantSnapshot` 只证明某一历史动作使用了什么条款，不自动赋予永久新行为。编译、运行、发布和商业导出必须重新计算当前 `EntitlementDecision`。已合法完成的运行及其审计证据默认可读；旧草稿重跑、新派生、新发布和新导出需要当前授权。法律下架、安全处置或法院命令可以限制历史内容访问，但必须保留隔离的审计证据。

收藏使用不可执行 ResourceLocator，不需要 GrantSnapshot；“加入可用资源库”必须先取得当前 entitlement 并创建带 snapshot 的 ResourceRef。工作流 clone 需要 clone；包内嵌依赖需要 redistribute；Agent/Recipe 安装和调用分别需要 install/execute，不能由 reference/derivative/commercial 自动推导。

ListingDraft 可变，审核与公开展示只针对不可变 ListingRevision。`unlisted` 退出搜索但直接 URL 仍可访问，所有新行为继续由当前 entitlement 决定；`withdrawn` 只保留公共 tombstone 和既有合法用户可见的必要元数据，阻断新接受/安装/克隆；`suspended` 隐藏内容并阻断新行为；grant revoked 可与 listing 展示独立；resource deleted 使 listing 进入 tombstone，不删除隔离审计。

### 8.6 状态族

禁止把不同聚合的状态合成一个公共枚举：

```text
RevisionStatus     = draft | active | retired
RunStatus          = pending | queued | running | waiting_user | completed | failed | cancelling | cancelled
NodeRunStatus      = pending | ready | queued | running | waiting_user | completed | failed | skipped | cancelled | stale
AttemptStatus      = pending | leased | running | waiting_external | completed | failed | cancelled | superseded | unknown
HumanTaskStatus    = pending | in_progress | submitted | accepted | rejected | cancelled | expired
ListingStatus      = draft | review_pending | listed | unlisted | withdrawn
ModerationStatus   = not_reviewed | pending | approved | rejected | suspended
GrantStatus        = draft | active | expired | revoked | legally_restricted
```

“激活内部 Revision”使用 `RevisionStatus.active`；“社区上架”使用 `ListingStatus.listed`，两者不得复用 `published`。

RunStatus 聚合优先级：显式取消为 `cancelling/cancelled`；存在可运行、排队、运行的必需节点，或有效 Attempt 处于 `waiting_external/unknown` 时为 `running`；只有在不存在其他可推进工作且至少一个必需节点等待人工时才为 `waiting_user`；所有必需路径终结且存在未被 Fallback 消费的失败时为 `failed`；全部必需输出完成时才为 `completed`。AttemptStatus 独立于 NodeRunStatus；`unknown` 在对账完成前不是可重试终态，晚到或被新 execution epoch 取代的 attempt 使用 `superseded` 且不得发布结果。

## 9. 全局边界

### 9.1 V1 不做

- 任意 Python、JavaScript、Shell 或系统命令节点；
- 智能体内部嵌套调用智能体或工作流；
- 任意 `while` 或无限自迭代；
- 完整 3D 建模、材质、骨骼动画、物理、布料和毛发系统；
- 多人实时协作和团队级细粒度权限；
- 工作流、Agent、配方或模型交易和收益分成；
- 复杂个性化推荐、热度操纵检测和商业排名；
- 长视频浏览器端稳定编码承诺；
- 宣称参考图、多参考或身份评分可以“保证不变脸”；
- SeedV/Toonflow 历史数据迁移。

### 9.2 禁止的产品捷径

- 用一个固定后端 API 伪装成开放模板；
- 把每个章节、镜头、宫格单元或模型原子铺成主业务画布节点；
- 把所有复杂流程塞进不可检查的黑盒 Agent；
- 用显示名称绑定人物、世界观或社区资产；
- 运行中读取 latest 资源、latest Agent 或 latest 配方；
- provider 不支持控制项时静默忽略；
- 公开作品后默认授予引用、派生或商业使用权；
- 撤回资源时破坏此前合法取得的不可变运行和授权证据；
- 让 LLM、前端或社区客户端直接决定核心状态转换。

## 10. 详细需求文档标准

每个 `requirements/<ID>.md` 必须使用以下结构：

1. 元数据：ID、标题、状态、版本、优先级、全局位置、依赖、负责人。
2. 背景与问题：用户问题和现有证据。
3. 目标与非目标：可以验证的结果和明确不做项。
4. 用户与权限：参与者、资源所有者和授权边界。
5. 用户场景与主流程：至少一个成功流程。
6. 功能需求：使用 `FR-1` 起的稳定编号。
7. 交互与展示：页面、工作台、状态和渐进披露。
8. 数据、类型与公共接口：输入、输出、版本和关系。
9. 状态机与业务规则：合法转换、幂等、并发和继承。
10. 失败、降级与恢复：安全错误、重试、取消和回退。
11. 安全、隐私、内容与授权：适用时不可省略。
12. 观测与运营：事件、指标、审计和支持信息。
13. 验收标准：Given/When/Then 或可执行判定。
14. 测试场景：正常、边界、失败、权限和恢复。
15. 交付与回退：功能开关、数据兼容和发布证据。
16. 已决策事项与开放问题：不得把本总表冻结项重新列为开放问题。

详细文档还必须遵守：

- 元数据中的直接依赖和责任域必须与第 12 节一致；新增依赖须先回写总表。
- 目标版本含 `A -> B` 时，必须有逐版本切片矩阵，不得只描述最终态。
- 公共数据类型只能扩展第 8 节，不能重定义 Artifact、Resource、Shot、Grant、Provider 或状态语义。
- Agent、Recipe、Workbench 和 Workflow 的内部步骤、检查点、成本、失败映射及 typed I/O 必须可审计。
- AI/媒体质量使用 TF-QLT-001 的固定样本、rubric、阈值和回归容差；性能、容量、文件、并发、RPO/RTO 和浏览器范围必须给出数值目标或明确由哪个依赖需求定义。
- V1 的有界基准至少覆盖 51 镜头项目；长篇扩写的章节/上下文/重跑边界必须量化，以区别 TF-LNG-001 的生产级多单元调度。

详细文档不得只写 UI，不得只写后端接口，也不得把“符合预期”“效果良好”或“场景非空”作为验收标准。

## 11. 追踪与验收规则

每项需求发布前必须形成以下证据链：

```text
Master Requirement ID
  -> Detailed Requirement FR/AC
  -> ADR / Design / API Schema
  -> Implementation PR or commit
  -> Automated tests
  -> E2E or visual evidence
  -> Release gate and monitoring
```

验收裁决：

- 证据明确证明所有 P0/P1 验收条件：通过。
- 只有代码或接口，没有用户流程和失败证据：未通过。
- 只在 mock provider 上成功，但需求要求真实 provider：未通过。
- 运行成功但版本、授权、成本或 lineage 缺失：未通过。
- 需要人工判断的视觉质量必须保存输入、候选、选择和评审记录，不以单张样例代替。
- 详细需求存在未裁决 P0 开放问题、依赖未交付或跨层调用违反第 8.2 节：未通过。
- Provider 编译报告与实际调用记录不一致，或 fallback 后未重新记录能力、成本和控制降级：未通过。

## 12. 逐项责任与依赖

责任域是当前阶段的 accountable function；进入交付时必须在详细文档中补充个人 DRI，但不得改变责任域或删除直接依赖。

### 12.1 逐项映射

| ID | 责任域 | 直接依赖 |
| --- | --- | --- |
| TF-GOV-001 | 产品治理 | 无 |
| TF-GOV-002 | 法务/工程治理 | TF-GOV-001 |
| TF-ARC-001 | 平台架构 | TF-GOV-001、TF-GOV-002 |
| TF-ARC-002 | 产品架构/前端架构 | TF-GOV-001、TF-ARC-001 |
| TF-PLT-001 | 平台产品/身份后端 | TF-ARC-001 |
| TF-PLT-002 | 核心产品/前端平台 | TF-PLT-001、TF-WF-004、TF-WF-005、TF-OPS-003 |
| TF-PLT-003 | 核心产品/前端平台/工作流平台/资源平台 | TF-ARC-002、TF-PLT-001、TF-PLT-002、TF-WF-004、TF-WF-005、TF-WF-006、TF-WF-009、TF-OPS-004、TF-NFR-001 |
| TF-QLT-001 | QA/AI 评测 | TF-GOV-001、TF-OPS-005 |
| TF-WF-001 | 工作流前端 | TF-ARC-002、TF-WF-002、TF-WF-004 |
| TF-WF-002 | 工作流平台 | TF-ARC-001、TF-GOV-001 |
| TF-WF-003 | 工作流平台/安全 | TF-WF-002、TF-WF-004、TF-WF-005、TF-OPS-001、TF-SEC-001 |
| TF-WF-004 | 工作流平台 | TF-ARC-001、TF-WF-002 |
| TF-WF-005 | 数据平台/工作流平台 | TF-ARC-001、TF-WF-002、TF-OPS-003 |
| TF-WF-006 | 运行时平台 | TF-WF-003、TF-WF-004、TF-WF-005、TF-OPS-003、TF-OPS-005 |
| TF-WF-007 | 运行时平台 | TF-WF-002、TF-WF-003、TF-WF-006 |
| TF-WF-008 | 运行时平台/核心产品 | TF-WF-006、TF-OPS-004 |
| TF-WF-009 | 模板产品/工作流平台 | TF-GOV-002、TF-WF-002、TF-WF-003、TF-WF-004、TF-WF-005、TF-SEC-001 |
| TF-WF-010 | 核心产品/工作流平台 | TF-WF-002、TF-WF-005、TF-WF-006；V1 Core 增加 TF-WF-008 |
| TF-AGT-001 | Agent 平台 | TF-WF-002、TF-WF-004、TF-WF-005、TF-AGT-006 |
| TF-AGT-002 | Agent 产品/平台 | TF-AGT-001、TF-AGT-005、TF-AGT-006、TF-WF-001、TF-WF-003、TF-WF-006、TF-WF-008 |
| TF-AGT-003 | Agent 产品/工作流平台 | TF-AGT-001、TF-AGT-005、TF-WF-001、TF-WF-002、TF-WF-003、TF-WF-004、TF-WF-006、TF-OPS-002、TF-SEC-001 |
| TF-AGT-004 | Agent 产品/运行时 | TF-AGT-001、TF-WF-006、TF-WF-007、TF-WF-008 |
| TF-AGT-005 | Agent 安全/平台 | TF-PLT-001、TF-OPS-001、TF-OPS-005、TF-SEC-001 |
| TF-AGT-006 | Agent 平台/知识资源 | TF-WF-002、TF-WF-005、TF-SEC-001 |
| TF-MR-001 | 媒体平台 | TF-WF-002、TF-WF-003、TF-WF-005、TF-WF-006、TF-OPS-001、TF-OPS-002、TF-OPS-003 |
| TF-STY-001 | 小说产品/AI | TF-WF-005、TF-WF-010 |
| TF-STY-002 | 小说产品/AI | TF-STY-001、TF-AGT-001、TF-WF-005、TF-WF-008 |
| TF-STY-003 | 小说产品/资源平台 | TF-STY-002、TF-WF-005 |
| TF-STY-004 | 小说产品/AI | TF-STY-001、TF-STY-002、TF-STY-003、TF-AGT-001、TF-WF-008 |
| TF-STY-005 | 小说产品/AI | TF-STY-004、TF-AGT-001、TF-WF-008、TF-QLT-001 |
| TF-STY-006 | 小说/影视产品 | TF-STY-004、TF-STY-005、TF-WF-005 |
| TF-MED-001 | 影视资产产品/媒体 AI | TF-WF-005、TF-WF-010、TF-MED-009、TF-QLT-001；V1 Core 增加 TF-WF-008 |
| TF-MED-002 | 影视产品/AI | V0：TF-STY-001、TF-WF-005、TF-WF-010；V1 Core 增加 TF-STY-006、TF-AGT-001、TF-WF-008 |
| TF-MED-003 | 分镜产品 | TF-MED-002、TF-WF-005、TF-WF-008、TF-WF-010 |
| TF-MED-004 | 分镜产品/媒体 AI | TF-MED-003、TF-MED-006 |
| TF-MED-005 | 3D/分镜前端 | TF-MED-003、TF-NFR-001 |
| TF-MED-006 | 媒体平台/分镜产品 | TF-MED-003、TF-MR-001、TF-OPS-001 |
| TF-MED-007 | 摄影规则/分镜产品 | TF-MED-003、TF-MED-005、TF-MED-006 |
| TF-MED-008 | 媒体 AI/质量 | TF-STY-003、TF-MED-001、TF-MED-003、TF-MED-006、TF-MED-009、TF-QLT-001 |
| TF-MED-009 | 媒体平台 | TF-WF-006、TF-OPS-001、TF-OPS-002、TF-OPS-003、TF-OPS-004、TF-SEC-001 |
| TF-MED-010 | 媒体平台/影视产品 | TF-MED-003、TF-MED-006、TF-MED-008、TF-MED-009、TF-WF-006、TF-OPS-001、TF-OPS-002、TF-OPS-003、TF-OPS-004 |
| TF-MED-011 | 音频产品/媒体平台 | TF-STY-003、TF-WF-006、TF-OPS-001、TF-OPS-003、TF-SEC-001 |
| TF-MED-012 | 成片产品/前端媒体 | V0：TF-MED-009、TF-OPS-003、TF-NFR-001；V1 Core 增加 TF-MED-010、TF-MED-011；V1.5 无新增强制需求依赖 |
| TF-IMG-001 | 图片广告产品/媒体 AI | TF-STY-001、TF-WF-010、TF-MED-009、TF-QLT-001、TF-SEC-001 |
| TF-COM-001 | 社区产品 | TF-PLT-001、TF-WF-005、TF-MED-012、TF-COM-004、TF-COM-006 |
| TF-COM-002 | 社区产品/资源平台 | TF-PLT-001、TF-WF-005、TF-STY-002、TF-STY-003、TF-COM-004、TF-COM-006 |
| TF-COM-003 | 社区产品/工作流平台 | TF-GOV-002、TF-PLT-001、TF-WF-009、TF-COM-004、TF-COM-006 |
| TF-COM-004 | 法务/社区平台 | TF-PLT-001、TF-WF-005、TF-SEC-001、TF-NFR-002 |
| TF-COM-005 | 社区产品/搜索 | TF-PLT-001、TF-WF-005、TF-COM-001、TF-COM-002、TF-COM-003 |
| TF-COM-006 | Trust & Safety/社区运营 | TF-PLT-001、TF-SEC-001、TF-OPS-005 |
| TF-COM-007 | 生态产品/Agent 与媒体平台 | TF-AGT-001、TF-AGT-002、TF-AGT-006、TF-MR-001、TF-WF-009、TF-COM-003、TF-COM-004、TF-COM-005、TF-COM-006 |
| TF-OPS-001 | Provider 平台/安全 | TF-ARC-001、TF-GOV-001 |
| TF-OPS-002 | FinOps/运行时 | TF-PLT-001、TF-WF-003、TF-WF-006、TF-OPS-001 |
| TF-OPS-003 | 存储平台 | Foundation：TF-ARC-001；V0 增加 TF-PLT-001 |
| TF-OPS-004 | 事件平台/核心产品 | TF-PLT-001、TF-WF-006 |
| TF-OPS-005 | SRE/安全工程 | TF-ARC-001、TF-GOV-001 |
| TF-SEC-001 | Trust & Safety/安全平台 | Foundation：TF-GOV-001、TF-ARC-001、TF-OPS-005；V0 增加 TF-PLT-001、TF-WF-005、TF-OPS-003 |
| TF-NFR-001 | 前端平台/QA | V0：TF-ARC-002、TF-PLT-002；V1 Core 增加 TF-WF-001 |
| TF-NFR-002 | 数据平台/SRE/法务 | TF-PLT-001、TF-WF-005、TF-OPS-003、TF-OPS-005、TF-SEC-001 |
| TF-LNG-001 | 长内容产品/运行时 | TF-STY-005、TF-STY-006、TF-WF-007、TF-MED-002、TF-MED-012、TF-NFR-001、TF-NFR-002 |
| TF-TEAM-001 | 协作产品/平台 | TF-PLT-001、TF-PLT-002、TF-WF-004、TF-WF-005、TF-COM-006、TF-OPS-005 |
| TF-MKT-001 | 市场产品/财务/Trust & Safety | TF-COM-001、TF-COM-002、TF-COM-003、TF-COM-004、TF-COM-006、TF-COM-007、TF-OPS-002、TF-TEAM-001 |

### 12.2 依赖主链

```text
TF-GOV-001 -> TF-GOV-002 -> TF-ARC-001
  -> TF-ARC-002 + TF-OPS-001/003/005 + TF-WF-002
  -> TF-QLT-001 + TF-SEC-001 + TF-WF-004
  -> TF-WF-005 -> TF-WF-003 -> TF-WF-006
  -> TF-WF-009/010（V0）
  -> TF-WF-001/007/008 + TF-AGT-005/006 + TF-MR-001（V1 Core）
  -> TF-AGT-001
  -> TF-AGT-002/003/004
  -> TF-STY-* + TF-MED-* + TF-IMG-001
  -> TF-COM-*

TF-OPS-001/003/004/005 + TF-SEC-001
  贯穿所有可运行和可发布需求
```

### 12.3 镜头主链

```text
TF-MED-009 -> TF-MED-001 -> TF-MED-008

TF-STY-006 -> TF-MED-002
  -> TF-MED-003
  -> TF-MED-005/006
  -> TF-MED-004/007/008
  -> TF-MED-010

TF-STY-003 + TF-WF-006 + TF-OPS-001/003 + TF-SEC-001
  -> TF-MED-011

TF-MED-010/011 -> TF-MED-012
  -> TF-COM-001
```

## 13. 当前冲突与待修订蓝图项

旧蓝图以下结论已被本文件 supersede：

| 旧结论 | 新结论 |
| --- | --- |
| 以 SeedV FastAPI 能力为后端基础 | 全新 FastAPI 后端，SeedV 只作参考 |
| 六阶段拆成六个主画布节点 | 一个不可拆分的小说框架智能体 |
| SeedV 工作流不能包装成黑盒 | 业务 Agent 可集成内部 SOP，但多 Agent 协作和外部依赖必须显式；运行、版本和产物仍可审计 |
| V1 单用户且不做用户发布 | V1 多账户私有项目，并交付基础社区发布/引用闭环 |
| 真实视频和主要镜头控制后置 V1.5 | V1 Core 至少接入一个真实视频 provider，并交付镜头工作台与轻量 3D 导演台 |
| 工作流市场整体 V2 | V1 Community 支持免费模板发布/克隆；交易和复杂市场后置 |

旧蓝图自本次修订起是 informative research，不再通过整章引用产生规范性约束。仍采纳的规则已迁入以下责任项；实现必须引用固定 Requirement/ADR，而不能只引用旧蓝图：

| 旧蓝图主题 | 当前规范责任项 |
| --- | --- |
| 强类型节点、图编译与 CompiledExecutionPlan | TF-WF-002、TF-WF-003 |
| Workflow/Artifact 不可变版本与 lineage | TF-WF-004、TF-WF-005、第 8.1 节 |
| attempt fencing、lease/epoch、outbox、取消和恢复 | TF-WF-006、TF-OPS-004 |
| Agent 提案、draft hash、确认和权限预算复核 | TF-AGT-003 |
| Toonflow 来源、授权、品牌和 clean-room 回退 | TF-GOV-002 |
| Provider 能力、成本、实际调用和降级 | TF-OPS-001、TF-OPS-002、TF-MED-006、第 8.4 节 |

## 14. 总体验收基准工作流

完整产品至少以以下基准证明抽象成立：

1. Idea -> 世界观智能体 -> 小说框架智能体 -> 扩写智能体 -> 小说作品。
2. 社区 WorldRevision + CharacterRevision -> 小说框架智能体 -> 新作品，并保留授权和署名。
3. 小说/剧本 -> 镜头规划 -> 51 镜头 ShotPlan -> 分镜控制 -> 视频片段 -> 时间线。
4. 《丧尸清道夫》式多模型候选、多参考角色/道具/场景、手绘构图、机位参数和人工选择流程。
5. 《万物生》式共享风格锁、角色/场景/色卡锚点、逐镜头运镜、表演、对白、音效和负面约束流程。
6. 素模多人站位 -> 180 度轴线检查 -> 正反打机位 -> 控制图 -> 图片/视频生成。
7. 9/16/25 宫格生成 -> 自动切分 -> 逐格修订 -> 单镜头重跑，不丢失 ShotSpec。
8. 用户发布作品、世界观、OC 和可克隆工作流；展示权限与复用授权分别生效。

## 15. 文档生成与审查流程

1. 本文件由主 Agent 设计和维护。
2. Terra 子代理对需求完整性、抽象粒度、版本范围、依赖和验收可证性做只读审查。
3. 主 Agent 对每条意见作采纳、部分采纳或拒绝裁决，并修改本文件。
4. 冻结后的需求按领域分组交给 Terra 子代理生成详细需求文档。
5. 主 Agent 验收每份详细文档是否遵守第 10 节、是否与公共合同和其他需求一致。
6. 未通过验收的详细文档必须返修，不能以子代理已完成作为交付证据。

## 16. Terra 审查与主代理裁决

2026-07-12 的 Terra 只读审查共提出 18 项。主代理逐项裁决如下：

| # | Terra 发现 | 裁决 | 落地 |
| --- | --- | --- | --- |
| 1 | Artifact/Resource/Shot 多重真相源 | 采纳 | 第 2 节与第 8.1 节统一 ArtifactVersion、ResourceDraft、ResourceRevision 和提升/编辑/发布链 |
| 2 | V0 依赖不闭合 | 采纳 | 第 5 节明确 bootstrap owner 和 ShotPlan 预览；WF-004/006、PLT-001、MED-002 增加 V0 切片 |
| 3 | 缺第三方代码许可 Gate | 采纳 | 新增 TF-GOV-002 |
| 4 | 缺通用业务节点与人工工作台任务 | 采纳 | 新增 TF-WF-010，并在第 8.2 节冻结 WorkbenchTask |
| 5 | 四层抽象缺调用合同 | 采纳 | 第 8.2 节给出 Workflow/Agent/Recipe/Workbench 调用矩阵与 trace 要求 |
| 6 | ShotSpec 公共合同过薄 | 采纳 | 第 8.3 节冻结 ShotPlan/ShotSpec/Beat、Board/Grid、DirectorScene、ControlLayer 及字段级权威；第二轮继续补全 Coverage、相机、灯光与风格合同 |
| 7 | 图片/广告工作区无需求 | 采纳 | 新增 TF-IMG-001，闭合 V0 广告图门 |
| 8 | GrantSnapshot 与撤权语义不完整 | 采纳 | 第 8.5 节分离历史 GrantSnapshot 与当前 EntitlementDecision |
| 9 | WorkflowPackage 依赖不足且发布节奏冲突 | 采纳 | 第 8.4 节增加 typed PackageDependency；新增 TF-COM-007 |
| 10 | 多聚合状态混为一个枚举 | 采纳 | 第 8.6 节拆分六类状态族 |
| 11 | 总表缺负责人和逐 ID 依赖 | 采纳 | 第 12.1 节覆盖全部需求；版本字段改用可验收切片语义 |
| 12 | 自定义 Agent 工具与凭证安全无归属 | 采纳 | 新增 TF-AGT-005 |
| 13 | Provider 编译能力与实际调用无统一记录 | 采纳 | 第 8.4 节增加策略、能力快照、编译报告和 InvocationRecord |
| 14 | AI/视觉质量和 NFR 不可客观裁决 | 部分采纳 | 新增 TF-QLT-001，并要求详细 PRD 数值化；具体阈值由固定测试集和 provider spike 后冻结，主表不伪造未经验证数值 |
| 15 | 旧蓝图仍被隐式作为规范 | 采纳 | 第 1、13 节取消整章继承并建立 Requirement/ADR 责任索引 |
| 16 | 3D 姿势与完整骨骼动画边界模糊 | 采纳 | TF-MED-005 明确静态关节素模/姿势库，移动端仅查看和有限调整 |
| 17 | 真人肖像、声音、未成年人和冒充风险不足 | 采纳 | TF-SEC-001、TF-MED-011 增加同意、撤回、披露和阻断证据 |
| 18 | CreativeWorkRevision 与 OC 提升 lineage 未定义 | 采纳 | 第 8.1 节增加 CreativeWorkContent 及 World 内嵌 OC 提升规则 |

主代理另补 TF-QLT-001 质量基线，并补三项完整产品路线责任：TF-LNG-001 生产级长内容、TF-TEAM-001 团队协作和 TF-MKT-001 市场治理。后三项保持 `deferred`，不进入 V1 承诺，但必须有范围文档以防路线图变成无主口号。

第二轮跨文档审查及镜头/AIGC/电影工业调研又提出 15 项。主代理裁决如下：

| # | 审查发现 | 裁决 | 落地 |
| --- | --- | --- | --- |
| 1 | “ShotSpec 是全部镜头真相”与 3D/Beat 权威冲突 | 采纳 | 第 2、7.5、8.3 节改为 ShotPlan/ShotSpec/Beat/DirectorScene 字段级唯一权威 |
| 2 | 单一 draft_version 无法覆盖计划、逐镜和共享场景 | 采纳 | 增加 EditContextSnapshot 与多 ResourceDraft 版本向量 |
| 3 | 工作台生成/检查缺少统一运行入口 | 采纳 | 第 8.2 节增加 WorkbenchActionRequest/Result，Result 只产生不可变输出和回写提案 |
| 4 | Storyboard/Action Board 与 Grid 混为同一产物 | 采纳 | 增加 BoardArtifact；GridArtifact 只负责布局、生成、切分和逐格结果 |
| 5 | Sequence/Coverage/Cut 关系没有计划级归属 | 采纳 | ShotPlanRevision 拥有 ShotSequenceGroup、CoverageGroup、CutRelation 并由 CommitManifest 原子冻结 |
| 6 | 剧本 StoryBeat 与镜内 Beat 混用 | 采纳 | 增加 StoryBeatRef；BeatOrKeyframe 只负责单镜时间与表演事件 |
| 7 | 文本覆盖、Coverage 和单镜变体概念混淆 | 采纳 | 分离 SourceSpanCoverageReport、CoverageGroup 和 ShotSetupVariant |
| 8 | 运镜预设把位移、朝向、光学和运动质感混存 | 采纳 | CameraMotionSpec 正交拆分 translation/orientation/optical/motion quality，并以 ShotTemporalAnchorRef 绑定时间 |
| 9 | 灯具类型与 key/fill/rim 职责一对一绑定 | 采纳 | 分离 LightEmitterSpec、LightingRoleAssignment 和可选 LightModifierSpec |
| 10 | ControlLayer 无法定位共享 3D 场景内的具体对象 | 采纳 | 增加必填 target_scope、ShotTemporalAnchorRef 和类型化 source_selector |
| 11 | Provider 报告不能分别裁决 path、roll、focus 等片段 | 采纳 | 增加 ControlCompilationItem.fragment_path 与 application_mode |
| 12 | 计划切镜关系与时间线实际剪辑决策混淆 | 采纳 | CutRelation 与 Timeline EditDecision 分离；重排后连续性报告 stale |
| 13 | “导演风格”仍可能退化成不可测字符串 | 采纳 | 增加可组合 CinematicStyleStack，按 project/sequence/coverage/shot 作用域覆盖 |
| 14 | pose/depth/normal/seg 控制稿缺坐标与尺寸合同 | 采纳 | 增加 ControlFrameManifest，冻结骨骼、单位、投影、near/far、标签、画布和 resize policy |
| 15 | 基础 Coverage/Cut/轴线连续性被错误后置 P1 | 采纳 | TF-MED-007 提升为 V1 Core P0；高级风格推荐仍可渐进交付 |

镜头产品裁决：主 DAG 保持“剧本 -> 镜头规划 Agent -> 分镜控制 WorkbenchTask -> ShotPlanRevision -> 镜头生成 -> 时间线”。故事板、动作板、Coverage 矩阵、9/16/25 宫格、视觉标注、轻量 3D 导演台、摄影、灯光、连续性和 Provider 编译均是工作台内的可组合方法/视图，不展开为 51 个主画布节点。行业证据包括 Runway 的 camera/keyframe/reference/motion control、Luma 的 camera motion/keyframe/visual annotation/master reference、Google Flow 的 ingredients/camera/scenebuilder/storyboard/grid、ComfyUI 的多 ControlNet/Wan 首尾帧，以及 Unreal Cine Camera 的 filmback/lens/focus 模型。V1 交付静态关节素模与可测控制稿，不建设完整 DCC；素模只约束空间和姿势，身份一致性仍依赖 CharacterRevision 与 Provider 身份控制。

第三轮镜头工作台专项调研进一步提出 9 项实现级缺口，主代理裁决如下：

| # | 专项发现 | 裁决 | 落地 |
| --- | --- | --- | --- |
| 1 | sequence/coverage 共享 DirectorScene 时裸 beat_id 会跨 Shot 冲突，且无 Beat 镜头无法绑定 | 采纳 | 增加 ShotTemporalAnchorRef，以 shot revision 命名空间表达 beat/start/end/default |
| 2 | DirectorSceneRevision 先冻结后导出，却保存事后输出引用，形成不可变循环 | 采纳 | 移除 Scene 内导出引用，增加独立 DirectorSceneControlExport |
| 3 | Grid 宣称可引用 Board，但 cell mapping 缺 BoardPanel 类型 | 采纳 | GridCellMapping 改为带 BoardPanelRef、CoverageMemberRef 和 CanonicalWritebackTarget 的 tagged union |
| 4 | Workbench action/scope/selector 与回写提案仍是裸字段 | 采纳 | 增加 WorkbenchActionType、WorkbenchScopeRef、ControlSourceSelector、WorkbenchWritebackProposal 与 Apply 请求 |
| 5 | 构图只有自由文本，无法表达主体屏幕位置和运动中构图保持 | 采纳 | 增加 CompositionIntent、世界空间 StagingGeometrySpec、派生 ScreenGeometryObservation 和 CameraFramingConstraint |
| 6 | 连续性要求曝光/time-of-day，但 Camera/Light 合同字段不足 | 采纳 | 增加 ExposureSpec 与 EnvironmentLightingSpec，并与 emitter/role/look 分离 |
| 7 | 复合宫格被误当作天然多角度身份控制输入 | 采纳 | 强制保存逐格 Artifact；按能力编译为独立 references、有序关键帧、明确 composite 或阻断/降级 |
| 8 | 视频转视频的 motion/structure/body/face 等控制被压成单一强度 | 采纳 | TF-MED-006 要求各语义 fragment 独立裁决，单强度 Provider 必须说明合并损失 |
| 9 | 动作板、Animatic 和完整 DCC 边界不清 | 采纳 | V1 Core 交付动作板与基础时域预览；精确 Animatic、完整曲线/灯光 cue/动捕/3DGS/高级色彩后置 V1.5 |

第三轮产业证据补强了上述裁决：Google Flow 官方帮助将 ingredients、首尾帧、source video 编辑、clip extension 与 Scenebuilder 分成不同能力，且不同模型能力矩阵并不相同；Luma Ray 3.2 将 motion、structure、body/pose、face 分离控制，并明确多角度身份参考宜逐图锚定而非默认拼成单张 sheet；ComfyUI 的 ControlNet、首尾帧、多关键帧和 Vidu multiframe 集成也显示每类控制有独立数量与格式约束；Unreal Cine Camera 继续证明 filmback、lens、focus、look-at/framing 需要结构化表达。因此产品必须保存原始创作意图，并由 ProviderCompilationReport 逐 fragment 解释 native、transformed、prompt approximation、ignored 或 blocked。

专项调研来源（访问日期 2026-07-12）：Google Flow `https://support.google.com/flow/answer/16353334?hl=en`、`https://support.google.com/flow/answer/16935718?hl=en`、`https://support.google.com/flow/answer/16352836?hl=en`；Luma `https://lumalabs.ai/learning-center/articles/ray-3-2-controls-and-workflows-in-depth`、`https://lumalabs.ai/learning-center/articles/character-and-object-consistency`；ComfyUI `https://docs.comfy.org/tutorials/controlnet/controlnet.md`、`https://docs.comfy.org/built-in-nodes/RunwayAleph2KeyframeNode.md`、`https://docs.comfy.org/api-reference/api-nodes/post-proxyvidumultiframe.md`；Unreal `https://dev.epicgames.com/documentation/en-us/unreal-engine/cinematic-cameras-in-unreal-engine`；ASC `https://theasc.com/article/shot-craft-camera-movement/`。其中 ComfyUI 内嵌节点页声明为 AI-generated documentation，只作为集成表面与能力约束的辅助证据，Provider 最终能力仍以其官方 API/capability snapshot 为准。

第四轮合同复验把研究结论收敛为以下实现约束：

| # | 复验发现 | 裁决 | 落地 |
| --- | --- | --- | --- |
| 1 | `director_component` 仅有 kind 与裸 ID 数组，无法区分异构对象及逐片段选择 | 采纳 | 增加 DirectorComponentRef，逐项固定 scene revision、component kind/id、可选 fragment/anchor/path range |
| 2 | Grid 同时保存泛型 source_ref 与类型化引用，可能形成平行来源身份 | 采纳 | GridCellMapping 改为严格 tagged union，按 mapping_kind 恰有一个类型化来源 payload |
| 3 | BoardGenerationSpec 的 ordered source mappings 没有公共结构 | 采纳 | 增加 BoardSourceMapping，固定顺序、shot/temporal-anchor 来源与 panel role |
| 4 | 工作台 patch 缺少对编辑快照与来源单元的直接审计关联 | 采纳 | TypedWorkbenchPatchSet 增加 edit_context_snapshot_ref 与可选 source_mapping_ref |
| 5 | 动作板可能被误读为跨镜统一局部时间轴 | 采纳 | 明确 normalized_time 只在各 Shot 内单调，跨 Shot 仅按 ShotPlan/Board 顺序 |
| 6 | source-video modify 容易被 compile-only 实现冒充交付 | 采纳 | TF-MED-010 增加真实 Provider capability、调用、恢复、控制遵循与安全 E2E 功能门 |
| 7 | 灯光、角色和姿势使用宽泛 component kind，kind-ID 无法唯一往返 | 采纳 | 拆分 DirectorComponentRef kind；LightingRoleAssignment 增加 scene 内稳定 assignment_id，并增加错误 kind-ID 阻断验收 |
| 8 | 原子提交只处理镜头锚点候选，其他跨候选 ResourceRevision 引用无法编码 | 采纳 | 增加 CandidateRevisionRef；事务创建新的 resolved content Artifact 并以 CandidateResolutionEntry 对账，激活内容禁止残留 |
| 9 | requested_input_refs 只允许固定引用，但验收又称 Request 直接包含 Draft | 采纳 | 增加 DraftInputSelector；固定 ref 与 Draft selector 分离，InputFreezeManifest 记录服务端解析结果 |
| 10 | ShotSetupVariant 自由 overrides 可能复制 Beat 时间或 DirectorScene 精确参数 | 采纳 | Variant 定位为非 canonical typed proposal overlay，按 owner domain/field schema 约束并经 WorkbenchWritebackProposal 采纳 |
| 11 | framing constraint 无稳定 ID，Director component selector 仍可能依赖数组位置 | 采纳 | CameraFramingConstraint 增加 scene 内唯一 framing_constraint_id，并加入重排/删除后的往返验收 |
| 12 | Variant 被列为 canonical 回写目标，无法落到 DirectorScene 字段权威 | 采纳 | CanonicalWritebackTarget 增加 director_scene_field、移除 variant；Variant 仅保留为 proposal lineage |

## 17. 变更记录

| 日期 | 变更 | 状态 |
| --- | --- | --- |
| 2026-07-12 | 创建产品需求总指导、需求 ID、版本门、边界、全局位置和验收规则 | 待 Terra 审查 |
| 2026-07-12 | 完成首轮 Terra 审查裁决；修复版本真相、V0 闭环、调用层级、ShotSpec、授权、Provider、广告图和责任依赖缺口；需求增至 61 项 | 主代理已裁决，待详细需求验收 |
| 2026-07-12 | 完成第二轮跨文档与镜头产业审查；冻结字段级镜头权威、WorkbenchAction、Board/Grid、Coverage/Cut、3D/相机/灯光、控制图与 Provider 片段编译合同 | 主代理已裁决，详细 PRD 同步与最终验收中 |
| 2026-07-12 | 完成第三轮镜头工作台专项调研；补齐时域锚点、控制导出、类型化命令/回写、构图/屏幕几何、曝光/环境灯光、宫格输入语义和 V1/V1.5 边界 | 主代理已补充并完成合同复验 |
| 2026-07-12 | 完成第四轮镜头工作台合同复验；收紧 Director component、Board/Grid 来源联合、工作台 patch 审计、动作板时间域与 source-video 真实 E2E 门 | 主代理已裁决，待总需求验收 |
| 2026-07-12 | 完成全量开发准备审查；建立团队认领规则、ADR/Schema/provider/质量 Gate 和覆盖全部 PRD 的串并行顺序，并修正人类可读依赖主链 | 产品合同已验收，待逐项 DRI 认领与批准 |
| 2026-07-12 | 增加覆盖 61 个 PRD 的开发交付跟踪表，统一 DRI、Requirement 状态、完成标记和证据索引规则 | active |
| 2026-07-15 | 产品品牌冻结为 LensFlow；增加 TF-PLT-003，冻结创作资产库/项目工作室双轴、私人 Workflow、显式 Artifact 提升、Screenplay 完全私有和社区后置边界；需求增至 62 项 | multi-agent reviewed |
