# 基于 Toonflow 的开放式 AI 创作平台实施蓝图

> 文档状态：定稿，已完成 gpt-5.6-terra 架构评审与 gpt-5.5 产品交付评审  
> 编写日期：2026-07-11  
> 目标读者：产品负责人、架构师、前后端工程师、AI 工作流工程师，以及在新会话中接手规划的编码 Agent  
> 基线项目：Toonflow Web / Toonflow App、SeedV v2.2 代码库  
> 文档性质：新项目建设蓝图，不是 SeedV 数据迁移方案，也不是对现有 SeedV 前端的增量改造计划

## 1. 文档用途

本文件用于在新的代码库和新的开发会话中，直接启动一个以 Toonflow 创作体验为基础、以开放节点编排为核心的 AI 创作平台。

新会话开始时，应同时提供：

1. SeedV 代码库；
2. 本文档；
3. 拟采用的 Toonflow 源码版本或 fork 地址；
4. Toonflow 的书面商业授权状态；
5. 第一阶段团队人数和目标发布日期。

新会话不应默认照搬 SeedV 页面、SeedV 固定流水线或 Toonflow 固定生产流程。它应先实现通用工作流内核，再用节点编排重建 SeedV 的两套工作流。

## 2. 执行摘要

### 2.1 产品目标

建设一个面向非技术创作者的开放式 AI 创作空间。用户可以从一个想法、产品资料、小说原文、剧本或参考素材出发，通过无限画布自由组合节点，完成：

- idea 到故事框架；
- idea 到短篇小说或长篇小说；
- 小说到影视剧本；
- 剧本到人物、场景、道具、主镜和分镜；
- 分镜到图片、视频、音频、字幕和成片；
- 单条短篇视频、广告片、产品片、电影视频和长剧集；
- 广告图、产品图、海报、社交媒体图片、人物设定图和场景概念图；
- 用户自定义的跨文本、图片、音频和视频工作流。

平台内置一个 Workflow Architect Agent，通过对话理解用户目标、询问必要约束、选择节点、配置参数、连接端口、校验工作流，并在用户确认后把工作流应用到画布。

SeedV 当前的“剧本专家工作流”和“视频文本流水线”不作为平台硬编码流程，而作为首批内置模板和领域节点实现。

### 2.2 对 Toonflow 的重新判断

Toonflow **适合作为产品交互参考和经授权、经解耦的领域工作台供体，但不再作为开放工作流前端核心的候选底座**。新平台应从一开始建立独立的 Vue 3 + Vue Flow 动态工作流核心，WebAV 作为媒体工作台能力接入；Toonflow Web 只按组件边界选择性移植。

它适合的原因：

- Vue 3 + Vue Flow 已提供创作画布和图片参考图子画布的实现参考；
- 已有小说、剧本、资产、分镜和视频工作台；
- 已接入 WebAV，具备浏览器时间线编辑基础；
- 已实现角色/场景/道具资产到分镜的引用绑定，以及图片、视频生成的多参考素材选择；
- 已有 AI 对话、媒体生成、任务状态和领域交互参考；
- 产品语言和 AI 漫剧领域与目标高度接近。

它不适合作为现成工作流内核的原因：

- `src/views/production/utils/flowBuilder.ts` 写死节点 ID、节点类型和边；
- `src/views/production/index.vue` 用固定 slot 挂载固定节点；
- `FlowData` 是固定字段对象，不是开放图数据模型；
- 图片编辑子画布虽支持动态参考图连线，但它只是局部媒体编辑器，不能替代平台工作流图；
- 现有 Agent 只能操作剧本、资产和分镜数据，不能创建或重构工作流图；
- Toonflow-app 后端是围绕固定业务表和固定 Agent 工具构建的，不是通用 DAG 运行时；
- 当前许可证包含对外商业提供和品牌移除限制。

因此本项目的准确表述是：

> 新建独立的 Vue 3 + Vue Flow 动态工作流前端核心；以 WebAV 作为时间线预览和有限导出能力；把 Toonflow Web 作为交互参考和经授权、经解耦的组件供体，优先评估图片参考图子画布、资产选择器、分镜工作台、多参考视频配置和 WebAV 工作台；以 SeedV FastAPI、PostgreSQL、严格结构化能力和媒体任务体系为唯一后端；建立新的通用节点注册、工作流运行时和 Workflow Architect Agent。

不建议同时保留 Toonflow-app Node/Express 后端与 SeedV FastAPI 后端。双后端会造成状态、任务、权限、模型配置和工作流运行权的冲突。

### 2.3 核心交付判断

如果目标只是固定的小说转视频，Toonflow 小改即可；如果目标是本文件定义的开放创作平台，不应继续改造 Toonflow 固定生产画布，而应直接新建动态 Vue Flow 核心，只移植可合法复用且不依赖固定 `FlowData`、Socket.IO 和 Toonflow-app 表结构的组件。

Step 0 后必须先完成一个不超过两周的“新 Vue Flow 核心 + Toonflow 组件抽取”验证。第 16.1 节的验收重点是证明通用图核心成立，并为每个候选 Toonflow 组件给出复用、重写或放弃结论。第一个 6 至 8 周里程碑还必须产出用户可体验的内容 Demo，不能只展示空画布和节点拖拽。

## 3. 范围

### 3.1 版本边界

| 版本 | 必须证明 | 明确不做 |
| --- | --- | --- |
| V0 可演示验证版 | 模板优先入口；产品 brief 到 3 张广告图；idea/剧本到分镜 mock；一个真实图片 provider；可恢复运行；成本、时延和失败可见；用户不看画布也能完成一次生成 | 自由搭复杂 DAG、长剧集、WebAV 编码导出、用户模板发布 |
| V1 单用户开放创作版 | 动态节点注册；强类型 graph；ArtifactVersion；Condition、Human Gate、有限 Map、OrderedMap/Fold；SeedV 视频模板节点化；广告图模板共用运行时；基础 Workflow Architect Agent | 市场、插件、多人协作、任意代码节点、电影级批处理 |
| V1.5 有限视频成片版 | 一个真实视频 provider；TTS/字幕；WebAV 有限导出；15 秒以内短视频成片 | 长视频和大规模剧集自动合成 |
| V2 长内容与生态版 | 长剧集、电影级分集生产、品牌资产库、模板生态和团队协作 | 另行立项 |

V0 是产品验证，不宣称开放平台已经完成。V1 才是本文核心架构的第一版。

### 3.2 V1 必须支持

- 单用户创作；
- 项目与多个工作流；
- 无限画布的节点增删、连接、复制、分组、缩放、自动布局和搜索；
- 动态节点注册，不需要修改画布主组件即可增加节点；
- 强类型输入输出端口和跨端口 lineage 约束；
- 文本、结构化数据、图片、视频、音频、文件、列表和控制信号；
- DAG 执行、条件分支、人工确认、合并、有限批处理、顺序 Fold、失败重试和回退；
- 运行单节点、运行到此节点、运行下游、运行整个工作流；
- 节点级提示词、模型、温度、尺寸、时长、并发和重试配置；
- 工作流草稿、不可变修订、运行记录、节点尝试记录和产物版本；
- 可回放的运行事件；
- Workflow Architect Agent 对话生成和修改工作流；
- SeedV 视频生产模板；
- 单条短视频和广告图片模板；
- SeedV 剧本专家节点可用，完整长篇模板允许在 V1 后半段交付；
- timeline JSON、片段包下载和浏览器预览。

### 3.3 V1 明确不做

- 多人实时协作；
- 工作流市场；
- 用户向其他用户发布模板；
- 高效工作流蒸馏、评分和推荐；
- 任意 `while` 循环和无限自迭代 Agent；
- 用户上传任意 Python、JavaScript 或系统命令节点；
- 第三方插件市场；
- 移动端完整编辑；
- SeedV 或 Toonflow 历史数据迁移；
- 大规模生产部署加固和企业级租户隔离；
- WebAV 长视频编码导出；
- 对外宣称完整电影或长剧集自动生成。

### 3.4 V1 的自由度边界

开放性不等于执行任意代码。第一版通过以下方式提供高自由度：

- 丰富、可扩展的节点注册表；
- 强类型端口；
- 通用 LLM、Agent、结构化输出和媒体节点；
- 条件、批处理、人工确认、子工作流和模板节点；
- 节点配置 JSON Schema；
- 工作流级变量和输入；
- 后端受控执行器。

任意代码节点和第三方运行时插件必须在后续版本经过隔离执行、安全审计和资源配额设计后再引入。

## 4. 架构原则与不可破坏约束

1. **工作流是数据，不是前端代码。** 节点和边必须持久化为版本化工作流定义。
2. **LLM 不拥有状态转换权。** 模型可以提出计划和产物，后端代码决定合法状态、依赖、重试、写入和失效传播。
3. **画布不是运行时真相。** 前端展示工作流；后端保存的工作流修订和运行快照才是执行真相。
4. **ArtifactVersion 是不可变 canonical truth。** 节点输入输出使用 ArtifactRef；人物表、分镜表等领域表是按 artifact/domain revision 物化的投影，不能反向覆盖历史 ArtifactVersion。
5. **领域写库有唯一所有者。** SeedV 领域表只能由对应服务或 writer 投影写入，不能由通用 Agent、API handler 或画布直接写入；仍被运行、任务或 Artifact 引用的旧投影不得物理删除。
6. **每次运行固定到工作流修订和源内容修订。** 运行中修改画布只产生新草稿，不改变正在执行的运行。
7. **上游变更必须使下游结果 stale。** 不能悄悄复用过期人物、分镜或视频。
8. **Agent 修改工作流必须先提案、再校验、再授权、再确认。** 禁止模型直接覆盖画布；应用时必须在事务内重新校验 draft hash、registry snapshot、权限和预算。
9. **核心结构化节点使用严格 Schema。** 提示词约束不能替代 Pydantic/JSON Schema 校验。
10. **富产物在专用工作台编辑。** 画布节点展示摘要、状态和关键操作，不把整部长篇小说或完整时间线塞进节点卡片。
11. **第一版运行图保持无环。** 独立重复任务使用有限 Map；有顺序记忆和 N+1 gate 的任务使用 OrderedMap/Fold；其余重复创作通过重试、人工退回和从节点重跑实现。
12. **运行时接口保持引擎中立。** 第一版使用本地 DAG Executor，同时保留未来 Langflow Executor 的适配边界，但不引入 Langflow 平台依赖。
13. **异步执行必须持久化。** V1 不允许仅依赖进程内队列或 Redis Pub/Sub；节点 attempt、任务绑定、outbox 和可回放 RunEvent 必须落库。
14. **Gate 有不同强度。** `domain_required_gate` 和 `policy_gate` 不能由用户删除；只有 `advisory_review` 可以自由移除或绕过。

## 5. SeedV 现有编排研究

### 5.1 SeedV 不是一套 workflow，而是两套不同工作流

#### A. 剧本专家工作流

当前入口由 `ScriptExpertOrchestrator`、`script_expert_v2` 节点包、对话意图路由和任务模块共同组成。

主要阶段：

```text
创作意图收集
  -> 意图确认
  -> Stage 0 初始核心骨架
  -> Stage 1 核心维度与主角/支线
  -> Stage 2 主线多轮重构
  -> Stage 3 支线衍生与网状结构
  -> Stage 4 问题诊断、修复和伏笔
  -> Stage 5 主题交付、回收和续作钩子
  -> 框架总评审
  -> 用户确认或修订
  -> 章节规划
  -> 用户确认章节规划
  -> 分章/分块扩写
  -> 记忆抽取
  -> 连续性检查
  -> 用户处理连续性报告
  -> 小说/框架转换为视频项目输入
```

其核心特征不是“一个写小说 Agent”，而是：

- 对话式意图收集；
- 六个强契约创作节点；
- 每阶段评审和人工 gate；
- 长篇内容的章节规划与有限批处理；
- 章节版本、全局记忆、人物状态、伏笔和设定事实；
- N+1 连续性检查；
- 暂停、继续、重试和从失败单元恢复；
- 转换为视频生产输入。

在新平台中，以上能力必须拆成可编排节点和控制节点，而不是包装成一个 `seedv_script_expert` 黑盒。

#### B. 视频文本与媒体生产工作流

当前 `VideoPipelineService` 阶段图：

```text
prepare_script
  -> act_split
  -> character_assets
  -> scene_visuals
  -> props
  -> main_shots
  -> storyboard_script
  -> storyboard_review_agno
  -> prompt_optimization_agno
  -> storyboard rendering / image tasks
  -> video tasks
```

其中 `character_assets`、`scene_visuals` 和 `props` 在数据依赖上可以并行，但当前 SeedV service 实际按 `stage_order` 顺序调度，并通过项目级 run fence 和领域 writer 写入。新平台可先并行 LLM 计算，再按 attempt fencing 条件化物化；不能把现有实现直接标记为可复用并行执行器。`main_shots` 按幕执行，`storyboard_script` 按主镜或幕执行。运行包含：

- immutable source revision；
- deterministic source index；
- run fencing；
- strict JSON Schema；
- cross-reference validation；
- writer 幂等替换；
- input/config hash；
- per-unit 状态与重试；
- 下游 stale；
- Agno Team 只做审查和提示词优化，不写核心表；
- 图片和视频异步任务；
- SSE 状态通知。

这些能力适合作为新平台节点执行器和运行时语义的直接参考。

### 5.2 历史 YAML 流水线的用途

`skills/agents/pipeline.yaml` 已不是 SeedV 视频核心运行时，但它仍提供有价值的模板参考：

- 标准短剧、长剧和电影；
- 广告/宣传片；
- 单幕内容；
- 按资产批处理；
- 按幕和按主镜运行；
- 图片参考和视频输出阶段。

新平台可以把这些变体转成内置工作流模板，但不能重新把 YAML AgentRunner 设为核心写库路径。

### 5.3 SeedV 能力到通用节点的映射原则

| SeedV 现有能力 | 新平台节点或机制 | 是否可复用现有实现 |
| --- | --- | --- |
| 意图收集 | `creative_intent` Agent 节点 | 高 |
| Stage 0-5 | 6 个严格结构化文本节点 | 高 |
| 阶段评审 | `artifact_review` 节点 | 高 |
| 人工确认 | 通用 `human_gate` 控制节点 | 新建 |
| 章节规划 | `chapter_plan` 节点 | 高 |
| 分章扩写 | `ordered_map/fold` + `chapter_writer` | 中；必须抽取 N+1 gate 和 accumulator 语义 |
| 记忆抽取 | `memory_extract` 节点 | 高 |
| 连续性检查 | `continuity_review` 节点 | 高 |
| 视频转换 | `script_to_video_brief` 节点 | 高 |
| 原文索引 | `source_index` 节点 | 高 |
| 分幕 | `act_split` 节点 | 高 |
| 人物/场景/道具 | 3 个并行领域节点 | 高 |
| 主镜 | `main_shots` 节点 | 高 |
| 分镜脚本 | `storyboard_script` 节点 | 高 |
| Agno 审查 | `storyboard_review_team` 节点 | 高 |
| 提示词优化 | `prompt_optimization_team` 节点 | 高 |
| 图片/视频任务 | 通用媒体生成节点 + SeedV provider adapter | 中；需要 attempt/task binding 和旧回写 fencing |
| SSE | 通用 run event stream | 中；现有 memory/Redis PubSub 不可回放，需要 RunEvent/outbox |

### 5.4 Toonflow 角色资产绑定与“防变脸”机制研究

Toonflow Web/App 当前没有实现真正的 2D/3D 骨骼绑定，也没有发现 OpenPose、ControlNet、InstantID、IP-Adapter、PuLID、FaceID、人脸 embedding 或身份一致性评分代码。产品体验中被理解为“骨骼绑定防变脸”的能力，实际由以下链路组成：

```text
角色主资产/衍生资产
  -> 分镜通过 associateAssetsIds 绑定角色、场景和道具
  -> 图片生成按稳定顺序加载绑定资产图片
  -> 图片编辑子画布通过连线组成 referenceList
  -> 视频工作台组合绑定资产、分镜图、首帧和尾帧
  -> provider adapter 以 reference_image / first_frame / last_frame 等角色提交
  -> 底层图片或视频模型执行多参考条件生成
```

因此该能力的准确名称是“角色资产锚定 + 分镜资产绑定 + 多参考图条件生成 + 首尾帧约束”。它可以降低人物外观漂移，但不能单独保证不变脸，最终效果取决于 provider 的参考图理解和身份保持能力。

新平台应吸收这套工作流思想，但将其升级为可审计、可版本化的正式能力：

- 角色锚点必须使用稳定 UUID 和不可变 ArtifactVersion，不能依赖显示名称；
- 明确区分角色身份参考、服装参考、场景参考、构图参考、首帧和尾帧；
- 保存实际提交给 provider 的参考图版本、顺序、用途和模型能力快照；
- NodeDefinition 声明最大参考图数量、支持的参考类型和降级策略；
- 角色或风格锚点变化时，依赖它们的分镜图片和视频必须 stale；
- 增加可选的 Character Identity Review，对生成结果做人脸/服装/关键特征一致性评分；
- 一致性低于阈值时进入 Retry、Fallback 或 Human Gate，而不是静默接受；
- 后续通过 provider adapter 接入 FaceID、PuLID、角色 LoRA 或同类能力，但不把某一模型私有参数写死在通用图模型中。

## 6. 目标总体架构

```text
┌─────────────────────────────────────────────────────────────────┐
│ Independent Vue 3 + Vue Flow Web                                │
│ Project Shell | Vue Flow Canvas | Node Library | Inspector      │
│ Artifact Workbench | Architect Chat | Selected Toonflow UI      │
│ WebAV Timeline                                                   │
└───────────────────────────────┬─────────────────────────────────┘
                                │ REST commands + SSE events
┌───────────────────────────────▼─────────────────────────────────┐
│ FastAPI Application                                             │
│ Workflow API | Artifact API | Media API | Architect Agent API   │
├─────────────────────────────────────────────────────────────────┤
│ Workflow Core                                                   │
│ Registry | Compiler | Validator | LocalDAGExecutor | Run Store   │
│ Durable Scheduler | Human Gate | Map/Fold | Retry | RunEvent     │
├─────────────────────────────────────────────────────────────────┤
│ Node Executors                                                  │
│ Generic AI | SeedV Script | SeedV Video | Image | Video | Audio │
│ Data Transform | Control | Export | Future Langflow Adapter      │
├─────────────────────────────────────────────────────────────────┤
│ SeedV Domain Services                                           │
│ Script Expert v2 | VideoPipelineService | Writers | Providers   │
│ Image/Video Tasks | Agno Teams | Source Revision | SSE           │
├─────────────────────────────────────────────────────────────────┤
│ PostgreSQL | durable task backend | Redis notification | OSS/local│
└─────────────────────────────────────────────────────────────────┘
```

### 6.1 唯一后端原则

第一版只保留 FastAPI 后端。Toonflow-app 的 Node/Express 后端只用于研究现有行为和复用无后端耦合的素材，不作为生产服务。

理由：

- SeedV 强结构化节点、writer、模型和任务代码均在 Python；
- 双后端会产生两个项目模型、两个任务系统、Socket.IO/SSE 两套事件和两套 provider 配置；
- Workflow Architect Agent 需要统一读取节点注册表、校验图并创建修订；
- 单后端更容易保证运行 fencing、幂等、权限和审计。

### 6.2 前端技术原则

新建独立的 Vue 3、Vue Flow 和 Pinia 前端核心。WebAV 作为独立媒体能力接入。Toonflow Web 组件只有在授权明确、依赖可隔离且通过适配测试后才移植。优先候选包括：

- 图片参考图编辑子画布；
- 角色、场景和道具资产选择器；
- 分镜查看与编辑工作台；
- 多参考图片/视频素材配置；
- WebAV 时间线预览和剪辑组件；
- 模型与媒体参数配置交互。

新核心不得继承以下固定生产流依赖：

- 固定 `flowBuilder.ts`；
- 固定 `FlowData`；
- `production/index.vue` 的固定 node slots；
- `productionAgent` store 对固定字段和 Socket.IO 的耦合；
- 直接面向 Toonflow-app 表结构的 API 调用。

每个移植组件必须经过 adapter 访问 ArtifactRef 和 FastAPI API，不能直接读取 Toonflow 业务表，也不能把局部图片编辑图误当成平台 WorkflowDefinition。

## 6A. 执行语义规范（Step 2 前必须冻结）

仅定义节点和边不足以构成可执行图。进入工作流存储和 Executor 实现前，必须通过 ADR 和 contract tests 冻结以下语义。

### 6A.1 控制 token 与数据可见性

- 节点只有在所有必需 data input 已绑定、且至少一个合法 control token 到达时进入 `ready`；
- 没有显式 control edge 的纯数据图，由数据依赖完成隐式触发；
- Condition/Switch 只向被选择的分支发送 token；未选择分支的节点记为 `skipped_by_branch`，其输出不存在；
- 下游节点不能读取未选择分支的 latest artifact 作为本次运行输入；所有输入必须绑定到当前 run 的明确 ArtifactVersion；
- data edge 可以跨控制分支，但消费者必须声明缺失策略：`required`、`optional` 或 `default`。

### 6A.2 Merge 与 Join

- `control_merge_any`：任一互斥分支到达即可继续；
- `control_join_all`：所有声明分支完成或明确 skipped 后继续；
- `data_merge_list`：按确定顺序合并多个列表；
- `select_first_success`：用于候选/fallback，返回首个成功 ArtifactVersion；
- Merge 不得通过“谁先完成”隐式决定业务结果，除非节点定义明确声明 race 语义。

### 6A.3 错误、重试和 Fallback

- 每个 executor 只能返回注册过的安全错误类别；
- error edge 必须声明可消费的错误类别；未被消费的错误向父 run 传播；
- Retry 创建新的 `NodeRunAttempt`，固定旧 attempt 的输入 ArtifactVersion，不自动读取最新草稿或 latest artifact；
- 用户选择“使用最新输入重新运行”时创建新的 NodeRun 或新的 workflow run slice，而不是伪装成 retry；
- Fallback 的输出必须通过与主路径相同的端口类型和 lineage 约束。

### 6A.4 取消传播

- 取消 WorkflowRun：停止新调度，标记活动 attempt 为 cancel requested，尽力取消 provider/task；
- worker 即使晚到，也必须因 attempt epoch/fence 失效而不能发布产物；
- 取消 Subworkflow 默认向子 run 传播；子 run 失败按父节点的 error edge 处理；
- 已完成 ArtifactVersion 不删除，只标记是否被当前 run 采用。

### 6A.5 Partial Run 闭包

- `selected`：只执行选中节点，所有必需输入必须已经 pin 到 ArtifactVersion；
- `upstream`：执行达到目标节点所需的最小祖先闭包；
- `downstream`：以选中节点的新输出为根，执行所有可达下游，遇到未满足的外部输入则停止并报具体端口；
- `full`：执行所有 workflow outputs 的依赖闭包；
- 任一模式都必须冻结成 `CompiledExecutionPlan`，不能边运行边读取草稿图。

### 6A.6 Map、OrderedMap/Fold 与 Batch

- `Map` 只用于相互独立的 item，输出按输入 index 确定排序；
- `Batch` 只是 Map 的调度策略，不改变业务语义；
- `OrderedMap/Fold` 用于章节、集或任何需要 accumulator 的顺序任务，固定 `concurrency=1`；
- Fold 每轮输入为 `item + previous_accumulator + pinned context`，每轮完成后持久化 checkpoint；
- SeedV 长篇扩写采用 Fold：章节写作 -> memory snapshot -> continuity decision -> 下一章；
- Map/Fold 都必须声明最大 item 数、最大嵌套深度和预算上限。

### 6A.7 Subworkflow

Subworkflow 在 V0 不开放给用户，V1 后半段再启用。启用时必须定义：

- 固定子 WorkflowRevision；
- 父子输入输出端口映射；
- 独立子 WorkflowRun 与父 NodeRun 关联；
- 取消、超时、错误和预算向父级传播；
- 子工作流内部 Artifact 的可见范围；
- 禁止运行中自动升级子工作流版本。

### 6A.8 CompiledExecutionPlan

WorkflowRevision 运行前编译为不可变计划，至少冻结：

- 解析后的 graph；
- 每个 NodeDefinition 的完整 schema 快照；
- config canonical hash；
- 端口转换器版本；
- executor package/image digest；
- provider policy version；
- 权限策略和预算上限；
- 分支、join、error、Map/Fold 和 partial-run 语义；
- compiler version。

只保存一个 `registry_snapshot_version` 不足以重放旧运行。节点被弃用后，系统必须能够按快照重放，或在运行前明确拒绝并提供迁移诊断。

## 7. 工作流核心数据模型

### 7.1 NodeDefinition

节点注册表是平台开放性的基础。每个节点定义至少包含：

```text
type_id                 全局稳定类型，例如 seedv.video.act_split
version                 节点定义版本
display_name            展示名
category                input / ai / story / video / image / control / output
description             给用户和 Workflow Architect Agent 的能力说明
executor                后端执行器标识
input_ports[]           名称、ArtifactType、是否必填、是否多值
output_ports[]          名称、ArtifactType、是否多值
port_constraints        schema identity、cardinality、lineage 一致性表达式
config_schema           JSON Schema
default_config          默认配置
ui_schema               节点摘要、图标、颜色、编辑器类型
capabilities[]          可搜索能力标签
constraints             依赖、互斥、最大批量、支持模型
retry_policy            默认重试策略
cost_hint               相对成本和可能的 token/media 成本
latency_hint            预估时延级别
permissions             是否允许网络、媒体、领域写库
policy_requirements     必需 gate、安全审查、预算和作用域要求
deprecated              是否停止新增实例
```

新增节点时，不允许修改画布主页面的 switch 或固定 slot。注册节点、执行器、配置表单和测试后即可出现于节点库。

### 7.2 WorkflowDefinition 与 WorkflowRevision

工作流草稿可以频繁保存。每次运行前必须冻结成不可变 `WorkflowRevision`：

```text
Workflow
  id
  project_id
  name
  current_draft

WorkflowRevision
  id
  workflow_id
  revision_number
  graph_json
  graph_hash
  compiled_execution_plan_id
  created_by                user / architect_agent / template
  created_at
```

`graph_json` 包含节点实例、端口边、控制边、画布位置、工作流输入和工作流输出。画布 UI 状态与执行配置应分区存储，避免移动节点改变执行 hash。运行绑定 `CompiledExecutionPlan`，不能依赖可变注册表重新解释旧修订。

### 7.3 NodeInstance 与 Edge

节点实例包含：

- `id`；
- `type_id` 和 `type_version`；
- `config`；
- `title`；
- `position` 和尺寸；
- 可选的工作流变量绑定；
- 禁用状态；
- 用户备注。

边分为：

- **data edge**：传递 ArtifactRef；
- **control edge**：条件、人工确认和流程控制；
- **error edge**：节点失败后进入显式 fallback 分支。

第一版禁止隐式按画布位置推断执行顺序。

### 7.4 Artifact 与 ArtifactVersion

所有节点输入输出统一为 `ArtifactRef`。建议基础表：

```text
Artifact
  id
  project_id
  artifact_type
  name
  latest_version_id

ArtifactVersion
  id
  artifact_id
  version_number
  payload_json
  blob_refs
  domain_refs
  content_hash
  lineage
  projection_status
  producer_node_run_id
  source_artifact_version_ids
  created_at
```

`lineage` 至少包含 source revision、style/asset anchor revision、producer attempt、父 ArtifactVersion 和领域 schema 版本。NodeDefinition 可以声明跨端口一致性约束，例如 `same(source_revision_id)`，编译期和执行期都必须检查，防止 source revision A 的幕与 revision B 的人物或场景混接。

`domain_refs` 可以指向 SeedV 的人物、场景、道具、主镜、分镜投影，但通用运行时只识别 ArtifactRef，不识别业务表。

`latest_version_id` 只用于编辑器默认展示，不能作为运行中的动态引用。草稿边可以声明 `resolve=latest_at_compile` 或显式 `pinned_version_id`；编译时两者都解析为确定 ArtifactVersion 并写入 CompiledExecutionPlan。用户 pin 优先于 latest。上游产生新版本时，只把依赖旧版本且未显式 pin 的草稿节点标记 stale；历史 WorkflowRun 和已 pin 节点不改变。

ArtifactVersion 是 canonical truth。SeedV writer 必须经过抽核层改造，在同一事务中完成：

```text
attempt fencing
  -> 写 ArtifactVersion 与 provenance
  -> 创建/更新带 artifact_version_id/domain_revision_id 的领域投影
  -> 写 transactional outbox
  -> commit
```

旧投影只能标记 `retired`，不能删除仍被 Artifact、NodeRun、媒体任务或用户 pin 引用的行。现有“删除再重建”writer 不能未经改造直接用于开放工作流节点。

### 7.5 WorkflowRun 与 NodeRun

```text
WorkflowRun
  id
  workflow_revision_id
  project_id
  status
  inputs
  source_revision_ids
  started_at / completed_at
  requested_by

NodeRun
  id
  workflow_run_id
  node_instance_id
  node_type_id / version
  status
  input_artifact_version_ids
  input_hash / config_hash
  output_artifact_version_ids
  error_category / safe_error
  metrics / usage / cost
  started_at / completed_at

NodeRunAttempt
  id
  node_run_id
  attempt_number
  execution_epoch
  status
  lease_owner / lease_expires_at / heartbeat_at
  idempotency_key
  pinned_input_artifact_version_ids
  provider_request_ids
  started_at / completed_at

WorkflowTaskBinding
  id
  workflow_run_id
  node_run_id
  attempt_id
  task_type / task_id
  provider_task_id
  status

RunEvent
  run_id
  seq
  event_type
  payload
  created_at

OutboxEvent
  id
  aggregate_type / aggregate_id
  event_type
  payload
  published_at
```

worker 发布节点结果前必须以 `run_id + node_run_id + attempt_id + execution_epoch` 做条件更新。取消或新 attempt 会提高 epoch，使旧 worker 即使晚到也不能写入产物。

SSE 只是 RunEvent 的投递方式，不是事实源。客户端重连时先读取 run snapshot，再以 `after_seq` 回放事件；现有 memory queue 或 Redis Pub/Sub 只能作为通知加速层。

状态至少支持：

```text
pending
ready
queued
running
waiting_user
completed
failed
cancelled
skipped
stale
```

## 8. 类型系统与端口

### 8.1 基础 ArtifactType

第一版至少定义：

- `text.plain`
- `text.markdown`
- `data.json`
- `data.list<T>`
- `file.document`
- `story.idea`
- `story.intent_profile`
- `story.framework`
- `story.chapter_plan`
- `story.chapter`
- `story.novel`
- `script.screenplay`
- `video.source_revision`
- `video.acts`
- `video.character_set`
- `video.scene_set`
- `video.prop_set`
- `video.main_shot_set`
- `video.storyboard_set`
- `media.image`
- `media.image_set`
- `media.video`
- `media.video_set`
- `media.audio`
- `media.subtitle`
- `media.timeline`
- `control.signal`
- `review.report`
- `prompt.package`

每个类型必须同时包含 `type_id + schema_id + schema_version`。`data.list<T>` 还必须固定 item schema 和 cardinality。类型系统需要支持受控、显式、版本化的兼容转换，例如 `story.chapter` 可以通过注册转换器变成 `text.markdown`，但不能自动连到 `media.video`。禁止仅按字符串前缀推断子类型。

### 8.2 图编译验证

运行前编译器必须检查：

- 节点类型和版本存在；
- 必需端口已连接或有默认值；
- 端口类型兼容；
- schema identity、cardinality 和跨端口 lineage 约束满足；
- 节点配置通过 JSON Schema；
- 图无非法环；
- 条件分支有默认出口；
- Map 有最大批量；
- Human Gate 有继续或拒绝路径；
- 所需 provider 可用；
- 所需领域模块可用；
- 没有不可达的关键输出；
- 没有悬空的 workflow output；
- 估算成本和节点数没有超过环境限制。

编译失败不能启动运行，错误必须映射回具体节点和端口。

## 9. 节点目录

节点目录采用“通用原语 + 领域节点”两层设计。不能为每个模板复制一套功能相同的节点。

### 9.1 V0 最小原语

V0 不追求节点数量，只证明不同内容模板可以复用同一运行时：

| 原语 | 用途 |
| --- | --- |
| Brief | 统一承载故事、产品、受众、平台和交付目标 |
| Asset Input | 上传产品、人物、场景和参考素材 |
| Constraint | 品牌、画幅、风格、预算、禁止项和素材授权约束 |
| Structured Generate | 严格 schema 的文本/计划生成 |
| Generate Variants | 生成多个创意方向、脚本、卖点或提示词候选 |
| Select / Rank | 用户、规则或评审模型选择候选 |
| Plan Units | 统一表示分幕、章节、集、镜头或图片变体计划 |
| Human Gate | 确认、驳回或带意见重新运行 |
| Limited Map | 对独立图片或媒体变体有限展开 |
| Media Generate | 图片、视频或音频任务的统一任务壳 |
| Content Safety Review | 内容安全、品牌、肖像和素材授权检查 |
| Package Export | 图片包、片段包、timeline JSON 或文档输出 |

V0 的领域能力可以由受控 adapter 支撑，但其演示图必须使用上述公开原语，不能调用模板专属隐藏 API。

### 9.2 V1 通用输入与数据节点

| 节点 | 主要作用 |
| --- | --- |
| Idea Input | 输入一句想法或创作目标 |
| Creative Brief | 类型、受众、时长、平台、风格、预算等结构化输入 |
| Text / Markdown Input | 粘贴文本 |
| Document Import | 导入 txt、md、docx |
| Reference Upload | 图片、视频、音频参考 |
| Product Brief | 产品卖点、品牌约束、受众和 CTA |
| Constraint | 品牌、平台、画幅、预算、禁止项和素材许可约束 |
| Template Render | 把结构化数据渲染为提示词或文档 |
| JSON Transform | 字段选择、重命名和简单映射 |
| List Select / Merge | 选择、合并列表 |
| Artifact Query | 读取当前项目已有产物 |

### 9.3 V1 通用 AI 节点

| 节点 | 主要作用 |
| --- | --- |
| LLM Generate | 通用文本生成 |
| Structured LLM | 使用用户选定 JSON Schema 的严格结构化生成 |
| Generate Variants | 生成多个候选并保留独立 ArtifactVersion |
| Select / Rank | 人工、规则或模型评分后选择候选 |
| Plan Units | 生成章节、集、幕、镜头或媒体任务计划 |
| Agent | 受控工具调用 Agent |
| Artifact Review | 对输入产物生成结构化审查报告 |
| Prompt Optimizer | 根据目标模型优化提示词 |
| Model Router | 按能力、成本或用户选择解析模型 |
| Content Safety Review | 输出可审计的内容与素材风险报告 |

### 9.4 V1 剧本专家领域节点包

| 节点 | 对应 SeedV 能力 |
| --- | --- |
| Creative Intent | 意图收集、补问和默认补全 |
| Framework Stage 0 | 初始核心骨架 |
| Framework Stage 1 | 主角、对立和情感支线 |
| Framework Stage 2 | 主线多轮重构 |
| Framework Stage 3 | 支线网状结构 |
| Framework Stage 4 | 诊断、修复和伏笔 |
| Framework Stage 5 | 主题、回收和续作钩子 |
| Framework Review | 阶段或全框架评审 |
| Chapter Planner | 章节计划 |
| Chapter Writer | 单章或单 chunk 扩写 |
| Memory Extract | 全局记忆、人物状态、设定和伏笔 |
| Continuity Review | 连续性检查 |
| Script Adaptation | 小说/框架转影视剧本或视频 brief |

### 9.5 V1 视频生产领域节点包

| 节点 | 对应 SeedV 能力 |
| --- | --- |
| Source Revision | 固定不可变原文修订 |
| Source Index | 确定性原文分段 |
| Act Split | 语义分幕和原文 span |
| Character Assets | 人物资产与固定锚点 |
| Scene Visuals | 场景视觉资产 |
| Props | 道具和生物资产 |
| Main Shots | 主镜和核心三要素 |
| Storyboard Script | 分镜脚本和资产引用 |
| Storyboard Review Team | 导演、摄影、连续性审查 |
| Prompt Optimization Team | 视频模型提示词包 |
| Storyboard Renderer | 确定性图片 prompt package |

### 9.6 V1 图片与视频节点

| 节点 | 主要作用 |
| --- | --- |
| Image Generate | 文生图、多参考图生图 |
| Image Edit | 局部或整体编辑 |
| Character Identity Anchor | 固定角色身份、服装、特征和参考图 ArtifactVersion |
| Reference Bind | 把角色、场景、构图、首帧和尾帧按明确用途绑定到媒体节点 |
| Character Identity Review | 对人脸、服装和关键特征输出结构化一致性评分 |
| Image Variant Batch | 生成多个广告图/角色图变体 |
| Product Image Compose | 产品主体、背景、文案区和比例约束 |
| Poster / Key Visual | 海报和主视觉 |
| Storyboard Image Batch | 分镜图片批量生成 |
| Video Generate | 单分镜视频生成 |
| Video Batch | 按分镜列表批量生成 |
| TTS | 旁白或对白语音 |
| Music / SFX | 音乐和音效生成或选择 |
| Subtitle Build | 字幕生成和时间信息 |
| Timeline Compose | 生成引擎中立的 timeline JSON 和片段包 |
| Timeline Export | 浏览器端预览和导出 |

### 9.7 V1 流程控制节点

| 节点 | 语义 |
| --- | --- |
| Condition | 布尔条件分支 |
| Switch | 多路枚举分支 |
| Human Gate | 等待用户接受、拒绝或要求修订；分 domain_required、policy、advisory 三类 |
| Merge | 合并互斥控制分支或数据列表 |
| Map | 对有限列表逐项执行子图，必须有最大数量 |
| Batch | 按固定批量并发执行 |
| OrderedMap / Fold | 按 index 顺序执行并传递 accumulator/checkpoint |
| Retry | 对可重试错误重试 |
| Fallback | 主节点失败后进入替代节点 |
| Wait For Tasks | 等待异步媒体任务完成 |
| Workflow Input / Output | 声明工作流公共接口 |

### 9.8 V1 后半段与 V1.5 节点

- Agent Team；
- Subworkflow；
- 多模型投票和裁判；
- 图片抠图、扩图、超分；
- FaceID、PuLID、角色 LoRA 等 provider-specific identity adapter；
- 口型同步；
- 视频插帧；
- 自动剪辑评分；
- 品牌资产库；
- Asset License Metadata；
- 外部 HTTP 工具节点；
- Langflow Subflow Adapter；
- 受控脚本表达式节点。

## 10. Workflow Architect Agent

### 10.1 职责

Workflow Architect Agent 负责把自然语言创作目标转换为合法、可运行、可解释的工作流。它不是内容生成 Agent，也不直接执行用户工作流。

示例目标：

- “帮我把一个都市复仇 idea 写成 30 集漫剧”；
- “用这本小说生成 60 秒预告片”；
- “给这个护肤品做一套小红书广告图和 15 秒视频”；
- “人物和世界观我自己确认，分镜之后全自动”；
- “预算有限，先做静态分镜，不生成视频”。

### 10.2 Agent 可用工具

Agent 只能通过后端受控工具工作：

- `search_node_registry`
- `get_node_definition`
- `search_builtin_workflows`
- `get_workflow_summary`
- `validate_workflow_draft`
- `estimate_workflow_cost`
- `propose_workflow`
- `propose_workflow_patch`
- `explain_validation_errors`
- `preview_workflow_diff`

第一版不提供可直接覆盖画布的 `apply_without_confirmation` 工具。

每个工具调用必须绑定 user、project、draft、registry snapshot 和 policy scope。工具返回结构化最小数据，不把未净化的外部网页、用户文件或模型文本直接拼进系统指令。敏感 Artifact 只按最小必要字段暴露。

### 10.3 对话到工作流的闭环

```text
用户目标
  -> 识别内容类型和期望输出
  -> 补问缺失约束
  -> 检索模板和节点注册表
  -> 生成 WorkflowProposal
  -> 后端静态编译和校验
  -> 成本/时延估算
  -> 画布缩略预览和 diff
  -> 用户选择“添加到画布”或“替换画布”
  -> 创建新草稿修订
```

必须询问或推断并展示的关键约束：

- 内容类型；
- 最终交付物；
- 时长、篇幅、集数或图片数量；
- 平台和画幅；
- 风格和受众；
- 用户希望人工确认的阶段；
- 可用模型；
- 预算和速度优先级；
- 现有输入资产。

### 10.4 WorkflowProposal

提案必须是严格结构化输出：

```text
name
summary
assumptions[]
workflow_inputs[]
nodes[]
edges[]
workflow_outputs[]
human_gates[]
estimated_cost_band
estimated_latency_band
warnings[]
base_draft_hash
registry_snapshot_id
policy_snapshot_id
permission_scope
budget_reservation_request
expires_at
```

模型生成的节点 ID、类型、端口、配置和边全部视为不可信输入。后端编译器校验通过后才能显示为可应用提案。

应用提案必须在单个事务中执行：

```text
校验 proposal 未过期
  -> WHERE draft_hash = base_draft_hash
  -> 重新执行 graph compile
  -> 重新执行权限和 capability 检查
  -> 预留本次运行预算或确认无运行预算
  -> 写入新 draft
  -> 写审计事件
```

静态校验不等于授权。即使图结构合法，Agent 也不能把敏感 Artifact 连接到无权限节点、超过项目预算、调用未启用 provider，或删除 `domain_required_gate`/`policy_gate`。

### 10.5 修改现有工作流

修改采用受限 Patch 操作：

- add node；
- remove node；
- connect；
- disconnect；
- configure node；
- rename node；
- move node；
- declare workflow input/output；
- wrap selection as subworkflow。

每次 Patch 必须基于当前草稿 hash。草稿已变化时返回冲突并重新生成 diff，不能盲目应用旧提案。

### 10.6 Gate contract

每个 Gate decision 必须持久化：

- gate 类型；
- actor；
- 输入 ArtifactVersion；
- approve/reject/revise；
- 用户反馈；
- idempotency decision key；
- 超时策略；
- 输出 control port；
- 创建和决定时间。

`domain_required_gate` 和 `policy_gate` 由节点/模板 policy 声明，用户和 Workflow Architect Agent 都不能删除；`advisory_review` 才允许自由移除。

## 11. 首批内置工作流模板

这里的模板是平台随代码交付的只读 `WorkflowRevision` seed 数据，不包含模板市场或用户发布能力。用户可以复制到个人草稿修改，但不能发布、上架、分享或创建公共模板。

### 11.1 SeedV 剧本专家模板

```text
Idea / Brief
  -> Creative Intent
  -> Human Gate
  -> Framework Stage 0
  -> Review -> Human Gate
  -> Stage 1 -> Review -> Human Gate
  -> Stage 2 -> Review -> Human Gate
  -> Stage 3 -> Review -> Human Gate
  -> Stage 4 -> Review -> Human Gate
  -> Stage 5 -> Framework Review -> Human Gate
  -> Chapter Planner -> Human Gate
  -> OrderedMap/Fold(Chapter Writer -> Memory Extract -> Continuity Review -> Gate)
  -> Novel Output
```

模板允许用户删除 advisory gate、替换模型、改变章节数量或只运行到故事框架。意图确认、章节计划确认和连续性阻断等 `domain_required_gate` 不允许删除，但可以在 policy 明确允许时配置自动批准条件。

### 11.2 SeedV 小说/剧本到视频模板

```text
Text / Novel / Script
  -> Source Revision
  -> Source Index
  -> Act Split
  -> [Character Assets, Scene Visuals, Props]
  -> Human Gate
  -> Main Shots
  -> Human Gate
  -> Storyboard Script
  -> Storyboard Review Team
  -> Human Gate
  -> Prompt Optimization Team
  -> Storyboard Renderer
  -> Image Batch
  -> Human Gate
  -> Video Batch
  -> TTS / SFX / Subtitle
  -> Timeline Compose
  -> Timeline JSON / Clip Package
```

WebAV 编码导出属于 V1.5，不作为 V1 模板完成条件。

### 11.3 单条短视频模板

Idea/Brief -> Script -> Main Shots -> Storyboard -> Images -> Videos -> Timeline。

默认弱化长篇记忆和复杂分幕，保留一个脚本确认和一个分镜确认 gate。

### 11.4 广告片模板

Product Brief + Reference Assets -> Creative Angles -> Script Variants -> Select -> Storyboard -> Product/Scene Images -> Video -> CTA/Subtitle -> Timeline。

支持 6 秒、15 秒、30 秒和 60 秒配置。

### 11.5 产品片模板

Product Brief -> Feature Prioritization -> Shot List -> Hero Image/Detail Images -> Product Video Clips -> Voiceover -> Timeline。

### 11.6 剧集分集规划与单集试跑模板

Story Framework -> Episode Plan -> OrderedMap/Fold(Episode Script -> Global Memory/Continuity -> Human Gate) -> Selected Episode Video Pipeline -> Episode Clip Package。

V1 不宣称完整电影或长剧集自动生成，只支持 3 集以内的分集规划和选定单集试跑；建议每集不超过 60 秒。完整剧集批处理属于 V2。

### 11.7 广告图片模板

Product Brief + Product Image + Brand Constraints -> Creative Directions -> Map(Image Prompt) -> Image Generate/Edit -> Human Select -> Size Variants -> Image Set Output。

应至少内置：

- 产品白底图；
- 场景化产品图；
- 电商主图；
- 海报/Key Visual；
- 小红书/Instagram 图；
- 横幅和封面图。

## 12. 前端体验设计

### 12.1 页面结构

面向 SeedV 的目标用户，默认第一屏不是空白画布，而是“选择目标 + Agent 问答 + 模板表单”。用户开始生成后可以展开高级画布查看和修改流程。空白画布只作为高级入口。

```text
默认入口：创作目标、模板、Workflow Architect Agent
顶部：项目、工作流、保存状态、运行控制、撤销重做
左侧：节点库、模板、项目资产
中间：Vue Flow 无限画布
右侧：节点配置与 Artifact Inspector
底部：运行日志、节点状态、成本和错误
可收起侧栏：Workflow Architect Agent 对话
全屏工作台：小说、剧本、人物、分镜、图片编辑、时间线；V1.5 接入 WebAV 导出
```

V0 用户即使不打开画布，也必须能完成产品 brief 到广告图或 idea/剧本到分镜的全过程。

### 12.2 节点卡片规则

节点卡片只展示：

- 名称和类型；
- 输入输出端口；
- 运行状态；
- 产物摘要或缩略图；
- 运行、停止、重试、打开工作台；
- 关键配置摘要。

长篇文本、复杂表格、图片编辑和时间线在右侧 Inspector 或全屏工作台打开。禁止把 Toonflow 当前的大型固定业务节点直接复制成几十个超大画布节点。

### 12.3 运行交互

用户可以：

- 运行选中节点；
- 运行至选中节点；
- 从选中节点运行下游；
- 运行整个工作流；
- 对失败节点重试；
- 对已完成节点重新生成一个新版本；
- 在 Human Gate 查看差异、接受、拒绝或写修订意见；
- 在节点输出版本间切换并固定某个版本作为下游输入。

### 12.4 Agent 提案交互

Workflow Architect Agent 生成提案后，界面必须展示：

- 工作流说明；
- 假设和补全项；
- 节点数和关键阶段；
- 人工确认点；
- 预计成本/时延等级；
- 小图预览；
- 与当前画布的 diff；
- 添加、替换、继续修改和放弃操作。

## 13. API 草案

### 13.1 节点注册表

```text
GET  /api/v1/node-definitions
GET  /api/v1/node-definitions/{type_id}/{version}
POST /api/v1/node-definitions/search
```

### 13.2 工作流

```text
POST  /api/v1/projects/{project_id}/workflows
GET   /api/v1/projects/{project_id}/workflows
GET   /api/v1/workflows/{workflow_id}
PATCH /api/v1/workflows/{workflow_id}/draft
POST  /api/v1/workflows/{workflow_id}/validate
POST  /api/v1/workflows/{workflow_id}/revisions
GET   /api/v1/workflows/{workflow_id}/revisions
```

保存草稿必须使用 `base_hash` 做乐观并发检查，即使第一版只有单用户，也要防止 Agent 提案和用户编辑相互覆盖。

### 13.3 运行

```text
POST /api/v1/workflow-revisions/{revision_id}/runs
GET  /api/v1/workflow-runs/{run_id}
POST /api/v1/workflow-runs/{run_id}/cancel
POST /api/v1/workflow-runs/{run_id}/resume
POST /api/v1/workflow-runs/{run_id}/nodes/{node_id}/retry
POST /api/v1/workflow-runs/{run_id}/gates/{node_id}/decisions
GET  /api/v1/events/workflow-runs/{run_id}?after_seq={seq}
```

运行请求支持 `mode=full|selected|upstream|downstream`，并明确传递目标节点 ID。

### 13.4 Artifact

```text
GET  /api/v1/projects/{project_id}/artifacts
GET  /api/v1/artifacts/{artifact_id}
GET  /api/v1/artifacts/{artifact_id}/versions
POST /api/v1/artifacts/{artifact_id}/versions/{version_id}/pin
```

### 13.5 Workflow Architect Agent

```text
POST /api/v1/workflow-architect/sessions
POST /api/v1/workflow-architect/sessions/{session_id}/messages
GET  /api/v1/workflow-architect/sessions/{session_id}/events
POST /api/v1/workflow-architect/proposals/{proposal_id}/validate
POST /api/v1/workflow-architect/proposals/{proposal_id}/apply
POST /api/v1/workflow-architect/proposals/{proposal_id}/dismiss
```

## 14. 后端包结构建议

```text
backend/app/
  api/v1/
    node_definitions.py
    workflows.py
    workflow_runs.py
    artifacts.py
    workflow_architect.py
  models/
    workflow.py
    workflow_revision.py
    workflow_run.py
    node_run.py
    node_run_attempt.py
    workflow_task_binding.py
    run_event.py
    outbox_event.py
    compiled_execution_plan.py
    artifact.py
    artifact_version.py
    human_gate.py
  schemas/workflow/
    graph.py
    registry.py
    run.py
    artifact.py
    architect.py
  services/workflow/
    registry.py
    compiler.py
    validator.py
    executor.py
    scheduler.py
    attempts.py
    leases.py
    outbox.py
    invalidation.py
    events.py
    artifacts.py
    projections.py
    human_gate.py
    map_executor.py
    errors.py
  services/workflow_architect/
    service.py
    contracts.py
    prompts.py
    tools.py
    proposal_validator.py
    patch_applier.py
  services/node_executors/
    generic/
    control/
    image/
    video/
    audio/
    seedv_script/
    seedv_video/
  services/video_pipeline/          # 从 SeedV 保留/抽取
  services/script_expert_v2/         # 从 SeedV 保留/抽取
```

## 15. 前端包结构建议

```text
frontend/src/
  views/workflow/
    index.vue
    components/
      WorkflowCanvas.vue
      NodeLibrary.vue
      NodeInspector.vue
      RunConsole.vue
      ArtifactPreview.vue
      ArchitectPanel.vue
  workflow/
    registry/
    graph/
    nodes/
      generic/
      control/
      story/
      video/
      image/
      output/
    stores/
      workflowStore.ts
      workflowRunStore.ts
      architectStore.ts
    api/
    types/
  workbenches/
    novel/
    screenplay/
    assets/
    storyboard/
    image-editor/
    timeline/
```

节点渲染器应由注册表映射，不在 `WorkflowCanvas.vue` 写固定 slot 列表。

## 16. 分阶段实施计划

每一步原则上对应一个可独立审查的 PR。新项目尚未建立 CI 时，可先直接提交，但仍应保持步骤边界和退出标准。

### 16.0 Step 0：授权、基线和新仓库建立

**目标**：冻结法律和技术基线，避免开发完成后才发现不能对外发布。

**任务**：

- 获取 Toonflow 书面商业授权或确认可接受的许可路径；
- 记录允许移除或替换 Toonflow 品牌的条款；
- 固定 Toonflow-web、Toonflow-app 和 SeedV 的 commit SHA；
- 创建第三方代码来源和修改记录；
- 创建新仓库，不在 SeedV 仓库中直接演变新产品；
- 写 ADR-001：独立 Vue Flow 前端核心 + Toonflow 选择性组件复用 + SeedV FastAPI 唯一后端；
- 写 ADR-002：本地 DAG Executor，Langflow 仅保留未来适配接口。

**授权 Go/No-Go 表**：

| 条款 | Go 条件 | No-Go 动作 |
| --- | --- | --- |
| SaaS 商用 | 书面允许向多个外部客户提供服务 | clean-room Vue Flow 核心 |
| 品牌替换 | 允许替换 Toonflow 品牌，或产品接受保留品牌 | 重建相关 UI 或明确保留署名 |
| 私有派生和托管 | 允许闭源修改和托管 | Toonflow 仅作研究参考 |
| 生成内容权利 | 用户可以商用生成内容 | 阻断付费发布 |
| NOTICE/归属 | 可以形成可执行的产品展示方案 | 发布前阻断 |

**验证**：

- `LICENSE`、`NOTICE`、授权文件和 commit SHA 均可追溯；
- 新仓库能启动空前端和 FastAPI 健康检查。

**退出标准**：授权状态明确，任何开发者可以从锁定 commit 重建初始代码。

**回滚**：授权未取得时停止复制受限代码，切换为 Vue Flow + WebAV clean-room 核心，Toonflow 仅作产品参考。

### 16.1 Step 1：新 Vue Flow 核心与 Toonflow 组件抽取验证

**目标**：在不继承 Toonflow 固定生产图的前提下，证明独立动态 Vue Flow 核心可行，并识别能够合法、低耦合复用的 Toonflow 工作台组件。

**上下文**：Toonflow 主生产画布的固定点位在 `flowBuilder.ts`，固定节点 slot 在 `production/index.vue`，固定业务数据在 `FlowData`。其图片编辑子画布、多参考素材选择、分镜工作台和 WebAV 组件具备局部复用价值，但仍耦合 Toonflow API 和表结构。

**任务**：

- 建立前端 NodeDefinition 类型；
- 建立动态节点渲染器；
- 新建不依赖固定 `NODE_IDS`、`FlowData` 和固定 slot 的 WorkflowCanvas；
- 支持从节点库动态添加至少 10 种节点；
- 支持任意合法端口连接和删除；
- 保存/加载通用 graph JSON；
- 建立后端最小 validator；
- 实现通用节点配置 Inspector；
- 接入一个 fake Artifact workbench；
- 为 Toonflow 图片参考图子画布建立 ArtifactRef adapter，验证多参考图连线和保存/恢复；
- 为 Toonflow 分镜/资产选择组件建立只读 fake adapter，验证不访问 Toonflow-app 表结构；
- 验证现有 WebAV 组件能够在新构建链中加载 timeline JSON 和预览，编码导出不作为本步骤阻塞；
- 为每个候选 Toonflow 组件记录来源、授权、依赖、移植成本和复用/重写/放弃结论；
- 视觉样式可以参考 Toonflow，但平台画布状态和运行状态必须来自新 contract。

**验证场景**：

用户从模板表单创建一个最小广告图流程，在画布上看到 Brief、Structured Generate、Human Gate、Image Generate 和 Package Export，修改配置并刷新，图和配置完全恢复；Artifact workbench 可以打开 fake 产物。

附加验证：用户把两个角色锚点和一个场景参考连接到 Image Generate，系统保存每张参考图的 ArtifactVersion、用途和顺序；删除或替换角色锚点后，下游结果标记 stale。

**退出标准**：

- 新增节点不需要修改画布主组件；
- graph JSON 不包含固定 `script/assets/storyboard/workbench` 字段；
- 非法端口连接被阻止；
- 后端可报告具体节点的校验错误；
- 图片参考图子画布只产生媒体编辑配置和 ArtifactRef，不产生第二套工作流真相；
- 至少完成图片编辑、分镜/资产选择、WebAV 三类组件的复用决策记录；
- 50 个节点、100 个 fake Artifact 缩略图、运行状态更新和 Inspector 打开可用。

**停止条件**：两周仍不能满足动态注册、通用 graph JSON 和后端校验时，停止扩大 Toonflow 组件移植范围，优先完成纯净 Vue Flow 核心。任一组件必须继续依赖固定 `FlowData`、Socket.IO 或 Toonflow-app 表结构时，该组件改为重写或放弃，不允许反向污染新核心。

### 16.1A Step 1A：执行合同冻结

**依赖**：Step 0；可以与 Step 1 的 UI spike 并行，但必须在 Step 2 编码前完成。

**任务**：

- 为第 6A 节每条语义写 ADR；
- 冻结 graph JSON Schema、ArtifactRef、lineage、Gate、attempt 和 RunEvent contract；
- 用 contract tests 表达 Condition、Join、Fallback、Map、Fold、cancel 和 partial run；
- 冻结 `CompiledExecutionPlan` 内容；
- 定义投影 writer 和 ArtifactVersion 的事务边界。

**退出标准**：前后端、Executor、Workflow Architect Agent 和节点开发都引用同一份 versioned contract；没有依靠文档自然语言才能理解的关键运行语义。

### 16.2 Step 2：工作流存储、Artifact 和运行骨架

**依赖**：Step 0、Step 1A。

**任务**：

- 添加 Workflow、Revision、CompiledExecutionPlan、Run、NodeRun、NodeRunAttempt、WorkflowTaskBinding、Artifact、ArtifactVersion、HumanGate、RunEvent 和 OutboxEvent 表；
- 实现 draft hash、revision freeze 和 graph hash；
- 实现 NodeDefinition 后端注册表；
- 实现图编译器和静态验证；
- 实现最小 LocalDAGExecutor；
- 实现运行和节点状态机；
- 实现 attempt epoch、lease/heartbeat 和幂等 key；
- 实现 transactional outbox 与持久化 RunEvent；
- 实现 SSE `after_seq` 回放；
- 实现取消和单节点重试；
- 加入 fake executor 供测试。

**验证**：

- 用纯 fake 节点运行分支图；
- 运行固定到 revision；
- 运行中修改 draft 不影响当前 run；
- 重启服务后可以读取 run 和 node run 状态；
- 旧 attempt worker 晚到不能发布产物；
- SSE 断线后可以按 seq 回放；
- 非法状态转换失败关闭。

**退出标准**：不调用任何真实模型也能完整演示工作流编译、运行、失败、重试、取消和事件恢复。

### 16.3 Step 3：控制流与有限展开

**依赖**：Step 2。

**任务**：

- Condition、Switch、Merge；
- 三类 Human Gate 和持久化 decision；
- Retry/Fallback；
- Map/Batch 和 OrderedMap/Fold，设置最大 item 数、顺序、checkpoint 和并发；
- Wait For Tasks；
- stale 传播和 input/config hash；
- 运行选中、上游、下游和全图模式。

**验证**：

- Map 中一个 item 失败不覆盖已完成 item；
- Fold 从最近 checkpoint 顺序恢复，不跳过 continuity gate；
- Human Gate 重启后仍停在 `waiting_user`；
- 上游 artifact 新版本使下游旧 node run stale；
- Fallback 只处理声明允许的错误类别；
- domain/policy gate 不能被用户或 Agent 删除。

### 16.4 Step 4：通用 AI、图片和视频节点

**依赖**：Step 2；可以与 Step 1 后半段并行。

**任务**：

- LLM Generate 和 Structured LLM；
- Agent 与受控工具接口；
- Model Router；
- Image Generate/Edit/Variant；
- Video Generate/Batch；
- TTS、字幕和基本音频；
- 复用 SeedV provider、task、存储和 SSE 适配层；
- 把媒体任务绑定到 WorkflowTaskBinding/NodeRunAttempt；
- 节点级使用量、成本和安全错误。

**验证**：

- mock provider 全覆盖；
- V0 至少一个真实图片 provider 和 OSS/MinIO 存储闭环；V1.5 再把真实视频 provider 设为阻断项；
- 媒体任务超时、失败、取消和重试不会产生错误写回；
- 运行日志不泄露密钥和完整敏感请求。

**V0 成本门**：

| 指标 | 上限/要求 |
| --- | --- |
| 单次广告图 Demo | 最多 3 张图，展示预估成本和实际成本 |
| 单次短视频 mock | 最多一个 5-10 秒任务壳，V1.5 才要求真实视频 |
| 失败处理 | timeout、cancel、retry 和 safe error 全链路记录 |
| 输出路径 | 真实图禁止 dummy URL、mock CDN 和未声明 local fallback |

### 16.4A Step 4A：V0 垂直 Demo Gate

**依赖**：Step 1、Step 2 的最小运行骨架、Step 4 的真实图片节点。

**目标**：在项目开始后 6 至 8 周内获得用户可体验结果，而不是只交付工程底座。

**必须演示**：

1. 用户不打开画布，选择“15 秒护肤品广告”模板；
2. 填写 Product Brief、产品图、受众和风格；
3. 系统生成脚本/创意方向 mock 或真实文本、3 张真实广告图、一个视频任务 mock；
4. 用户在 Human Gate 选择图片或要求重做；
5. 系统展示成本、时延、失败重试和刷新恢复；
6. 用户可以展开画布，轻量修改 3-5 个关键节点配置。

**Go 条件**：真实图片 provider、存储、ArtifactVersion、运行恢复和非技术用户流程全部通过。只完成空画布、节点拖拽或全 mock 时为 No-Go。

### 16.5 Step 5：SeedV 剧本专家节点化

**依赖**：Step 3、Step 4 的 Structured LLM。

**任务**：

- 为意图、Stage 0-5、评审、章节计划、章节写作、记忆、连续性和转换建立 NodeDefinition；
- 把现有 `script_expert_v2` 函数包装为 executor；
- 把原 orchestrator 的状态转换拆为控制节点、domain required gate 和 executor guard；不得把现有持久化状态机假装成无状态函数；
- 保留强契约和 provenance；
- 把章节扩写映射为 OrderedMap/Fold，固定 concurrency=1，并保留 accumulator、N+1 gate 和 checkpoint；
- 把章节计划确认和连续性处理映射为 Human Gate；
- 生成内置“SeedV 剧本专家”模板。

**验证**：

- 模板可以从 idea 运行到故事框架；
- 可以只运行到任一阶段；
- 用户可删除或增加 gate；
- 章节批处理可暂停、继续和从失败章节恢复；
- 记忆和连续性不会由前端状态代替；
- 原 SeedV 关键 contract 测试继续通过或被等价测试覆盖。

### 16.6 Step 6：SeedV 视频流水线节点化

**依赖**：Step 3、Step 4。

**任务**：

- 先建立 SeedV 抽核层：每阶段变为“pinned input snapshot -> validated domain command -> ArtifactVersion + projection”；
- 移除节点执行对 `project.config.workflow_status.video_pipeline` 作为运行真相的依赖；旧状态只保留兼容读取；
- 改造现有删除重建 writer，使其按 artifact/domain revision 物化并保留仍被引用的旧投影；
- 包装 source revision/index、act split、人物、场景、道具、主镜、分镜、Agno review 和 prompt optimization；
- 保留 run fencing 和 immutable source；
- 保留 strict schema、cross-reference validator 和 writer ownership；
- 人物、场景、道具先允许并行 LLM 计算，再按 attempt fence 条件化物化；
- 主镜和分镜使用 Map/per-unit 运行；
- 输出 ArtifactVersion 和领域引用；
- 生成“SeedV 小说/剧本到视频”模板。

**验证**：

- SeedV 原始固定流程可完全由图编排复现；
- 每个阶段可独立调用且不推进隐藏 stage order；
- 修改 act split 后下游人物、场景、主镜和分镜 stale；
- 失败单元可重试而不删除成功单元；
- Agno Team 只能输出 review/prompt package；
- 资产 UUID 引用通过 canonical validator。

**诚实性约束**：如果抽核层没有完成，VideoPipelineService 只能暂时作为一个明确标记的 legacy black-box node，不能通过“普通节点完整复现”验收，也不能把其内部阶段伪装为可自由重排节点。

### 16.7 Step 7：领域工作台和 WebAV

**依赖**：Step 1、Step 5 或 Step 6 的 Artifact。

**任务**：

- 把 Toonflow 小说、剧本、资产、分镜和工作台组件改为 Artifact 驱动；
- 节点卡片只显示摘要；
- Inspector 打开对应编辑器；
- 编辑保存为新 ArtifactVersion；
- 接入 Timeline Artifact；V1 生成 timeline JSON、片段包和浏览器预览；
- V1.5 再实现 WebAV 有限编码导出；
- 支持图片广告的多尺寸变体查看和选择。

**验证**：

- 刷新后编辑结果和版本存在；
- 编辑上游 artifact 会触发下游 stale；
- V1 的 timeline JSON 和片段包可重建预览；
- V1.5 WebAV 在目标 Chromium 上通过 `<=15s、<=5 clips、720p` 非空画面导出；
- 大篇幅小说和大量分镜不在画布一次性渲染。

### 16.8 Step 8：Workflow Architect Agent

**依赖**：Step 1A contract、Step 2 compiler、Step 3 控制流、权限/预算策略、稳定注册表、至少 20 个可用节点和模板兼容测试。

**任务**：

- 实现 Agent 会话、严格 Proposal/Patch contract；
- 暴露节点检索、模板检索、校验、估算和 diff 工具；
- 实现必要约束补问；
- 实现后端 proposal validator；
- 实现 project/user/capability policy 和预算 reservation；
- 实现画布缩略图和 add/replace/dismiss；
- 使用 base hash 防止覆盖用户新编辑；
- 增加 prompt injection 和恶意节点 ID 测试；
- 禁止 Agent 绕过确认应用图。
- apply 使用事务内 compare-and-swap，并重新编译和授权。

**验证场景**：

1. 用户说“做一个 15 秒护肤品广告视频，同时输出三张小红书广告图”；
2. Agent 询问产品资料、受众、画幅和人工确认点；
3. Agent 提出包含产品 brief、广告脚本、图片分支、视频分支和 Human Gate 的工作流；
4. 后端校验并展示预览、成本和警告；
5. 用户添加到画布；
6. 工作流可运行到 mock 输出。

### 16.9 Step 9：模板、端到端 Demo 和收口

**依赖**：Step 5-8。

**任务**：

- 完成第 11 节的 V1 模板；剧集模板只交付分集规划和选定单集试跑；
- 建立模板版本和兼容测试；
- 完成 idea -> 短视频；
- 完成小说 -> 视频；
- 完成产品 brief -> 广告图 + 广告视频；
- 完成 3 集以内分集规划和选定单集试跑 Demo；
- 完成错误恢复、刷新恢复和浏览器兼容验证；
- 编写新节点开发指南和工作流调试手册。

**退出标准**：第 20 节验收矩阵全部通过，且所有模板都由普通节点组成，没有模板专属隐藏后端路径。

### 16.10 Step 10：V1.5 有限视频成片

**依赖**：V1 验收通过；真实视频 provider 的成本、时延和失败率可接受。

**任务**：

- 接入一个真实视频 provider；
- 完成视频 attempt/task binding、轮询、取消和超时；
- 接入 TTS 和字幕；
- 完成 WebAV 有限导出；
- 建立服务端合成接口边界，但本步骤不要求实现长视频合成。

**退出标准**：15 秒以内、5 个片段以内、720p、目标 Chromium 和 2GB 可用内存设备上可稳定得到非空导出；成本、时延和失败率有真实记录。

## 17. 依赖和并行关系

```text
Step 0
  ├─> Step 1 Vue Flow 核心/组件抽取 ──────┐
  └─> Step 1A 执行合同 -> Step 2 -> Step 3 ─┼─> Step 5 ─┐
                         └─> Step 4 ───────┼─> Step 6 ─┼─> Step 7 ─┐
Step 1 + Step 2 + Step 4 -> Step 4A V0 Gate          │           │
Step 1A + Step 2 + Step 3 + 稳定节点目录 ─────────────┴─> Step 8 ─┼─> Step 9 -> Step 10
```

适合并行：

- Step 1 前端核心/组件抽取验证与 Step 1A 执行合同；
- Step 1 通过后，前端开放画布与 Step 2 后端运行骨架；
- Step 4 通用媒体节点与 Step 3 控制流；
- Step 5 剧本专家节点与 Step 6 视频节点；
- Step 7 的小说/剧本工作台与时间线工作台。

不适合并行：

- 未冻结 graph、Artifact、attempt 和运行语义就同时开发大量节点；
- 未完成 compiler 就开发 Workflow Architect Agent；
- 未明确 ArtifactVersion 语义就改造所有领域工作台。

## 18. 工作量和团队建议

开放平台目标明显大于“在 Toonflow 中接入 SeedV 固定流程”。以下估算包含测试和集成，不包含多人协作、模板市场、蒸馏和生产加固。

| 交付层级 | 有效新增/重改代码 | 工程人月 | 推荐团队 | 日历周期 |
| --- | ---: | ---: | ---: | ---: |
| Vue Flow 核心与 Toonflow 组件抽取验证 | 3k-6k | 1-2 | 2-3 人 | 最多 2 周 |
| V0 垂直 Demo：广告图 + 分镜 mock | 15k-25k | 6-9 | 3 人 | 8-10 周 |
| V1 单用户开放创作 | 60k-95k | 25-36 | 5-6 人，含 QA/设计 | 6-9 个月 |
| 商用 Beta 加固 | 视故障数据决定 | 8-12 | 4-6 人 | 额外 2-3 个月 |
| V1.5 有限视频成片 | 10k-20k | 5-8 | 3-4 人 | 2-3 个月，可与 Beta 部分并行 |

建议团队：

- 1 名后端/工作流架构工程师；
- 1 名 AI/Agent 与 SeedV 领域工程师；
- 1-2 名 Vue/画布/领域工作台工程师；
- 1 名媒体/WebAV/任务链工程师，可在 V1 中后期加入；
- 1 名 QA/自动化工程师；
- 0.5-1 名产品设计支持。

五人以上不会线性加速，因为 graph contract、Artifact 模型、运行语义和 Agent proposal contract 都存在关键路径。

### 18.1 总体 Go / No-Go

**Go**：

- 如果复用任何 Toonflow 代码，其 SaaS 商用、品牌替换、私有派生和 NOTICE 条款必须书面明确；未获授权时项目仍可按 clean-room Vue Flow 核心继续，但不得移植 Toonflow 代码；
- 两周内独立 Vue Flow 核心通过，且图片编辑、分镜/资产选择和 WebAV 组件均形成复用、重写或放弃结论；
- 6-8 周内 V0 可演示产品 brief 到真实广告图、idea/剧本到分镜 mock、运行恢复和成本展示；
- 至少一个真实图片 provider 和 OSS/MinIO 存储闭环通过；
- 非技术用户不看画布也能完成一次生成；
- 模板由公开节点复现，没有模板专属隐藏后端路径。

**No-Go 或必须改线**：

- 在授权不清或不允许目标 SaaS 模式时仍复制、修改或发布 Toonflow 代码；
- 两周后仍依赖固定 `FlowData` 和固定 node slots；
- 6-8 周只能展示空画布或全 mock；
- 真实 provider 仍返回 dummy URL 或未声明 fallback；
- 把 WebAV 编码导出设为 V0 阻塞项；
- Artifact 与领域表出现无法解释的双真相源；
- SeedV 固定 orchestrator 被伪装成自由可重排节点。

## 19. 测试与质量门

### 19.1 工作流核心

- 节点定义 schema 测试；
- 端口兼容矩阵测试；
- DAG/条件/Map/Subworkflow 编译测试；
- 状态机非法转换测试；
- revision fencing；
- input/config hash 和 stale 传播；
- retry、cancel、resume；
- attempt epoch、lease 超时和旧 worker 晚到；
- outbox 重复投递和 RunEvent `after_seq` 回放；
- Condition/Join/Fallback 的 token 和缺失输入语义；
- OrderedMap/Fold checkpoint 恢复；
- 服务重启恢复；
- 并发重复启动幂等。

### 19.2 节点 contract

- 每个节点至少有 mock 成功、schema 失败、provider 失败、重试和取消测试；
- SeedV writer 失败不能删除已存在合法数据；
- ArtifactVersion 的 source refs 和 producer refs 完整；
- 领域 UUID 引用验证；
- Map 单元级恢复。

### 19.3 Workflow Architect Agent

- 不存在的节点类型；
- 错误端口名；
- 类型不兼容；
- 图环；
- 超大 Map；
- 缺少默认分支；
- 旧 base hash；
- prompt injection 要求绕过确认；
- 越权读取敏感 Artifact；
- 删除 domain/policy gate；
- 超预算 proposal 和 reservation 失败；
- replace 与 add 的差异；
- 提案被拒绝后不改变画布。

### 19.4 前端

- 50 个节点、100 个 Artifact 缩略图、运行状态流和 Inspector 交互；
- 长文本不撑大节点；
- 节点和按钮文本不溢出；
- 大量分镜使用虚拟化或按需加载；
- SSE 断线重连后从 API 恢复状态；
- Agent 提案预览和 diff；
- V1 时间线 JSON/片段包预览；V1.5 WebAV 非空画面、播放和有限导出；
- 目标桌面和最小移动视口无重叠。

### 19.5 E2E

- idea -> 短视频 mock；
- idea -> 小说框架 -> 章节；
- 小说 -> 剧本 -> 分镜；
- 产品 brief -> 三张广告图；
- V1：产品 brief -> 广告图 + 15 秒视频任务壳/片段包；
- V1.5：产品 brief -> 真实 15 秒视频成片；
- 内容安全或素材授权 gate 阻断高风险导出；
- Human Gate 拒绝并退回上游；
- 失败节点重试；
- 刷新和后端重启恢复运行。

## 20. 最终验收矩阵

| 能力 | 必须证明 |
| --- | --- |
| 开放节点 | 新节点不修改画布主组件即可注册 |
| 自由连接 | 合法类型可连接，非法类型有明确错误 |
| 工作流 Agent | 对话生成的图必须经过预览、校验和确认 |
| SeedV 剧本专家 | 由普通节点和控制节点完整搭出 |
| SeedV 视频流水线 | 由普通节点和控制节点完整搭出 |
| 多内容类型 | 视频模板和广告图片模板共用同一运行时 |
| 人工参与 | 任意关键阶段可插入 Human Gate |
| 可恢复 | 失败、刷新和服务重启后可继续；旧 attempt 不能污染新运行 |
| 版本 | 工作流和产物都有不可变修订 |
| V0 媒体 | 真实图片、视频任务 mock、timeline JSON 和片段包 |
| V1.5 媒体 | 真实视频、音频、字幕和 WebAV 有限导出 |
| 无隐藏捷径 | 内置模板不调用模板专属固定后端流程 |
| 默认体验 | 非技术用户不打开画布也能完成 V0 生成 |
| 安全与权利 | 高风险内容或无授权素材可在导出前被 policy gate 阻断 |

## 21. 主要风险与应对

### 21.1 Toonflow 固定结构耦合过深

**风险**：不断在固定 `FlowData` 上补字段，最终仍不能自由编排。  
**应对**：不再把固定生产画布作为候选核心；Step 1 直接新建 WorkflowCanvas。通用 graph JSON 和动态 registry 是硬验收项，Toonflow 仅按组件抽取。

### 21.2 “节点很多”但原语不完整

**风险**：堆出几十个领域节点，仍缺少 Map、Human Gate、Subworkflow、Artifact 版本和错误分支。  
**应对**：先完成控制流和 Artifact，再开发大量模板。

### 21.3 把 SeedV 工作流包装成黑盒

**风险**：用户不能替换阶段、插入确认、改变模型或只运行部分流程。  
**应对**：按第 5.3 节拆分，模板只能由公开节点组成。

### 21.4 Agent 直接修改图

**风险**：模型幻觉节点、错误端口或覆盖用户编辑。  
**应对**：Proposal/Patch contract、后端 compiler、base hash、diff 和用户确认。

### 21.5 通用 Artifact 与 SeedV 领域表冲突

**风险**：形成两个互相不一致的真相源。  
**应对**：ArtifactVersion 是 canonical truth；运行时只传 ArtifactRef；领域 writer 只负责按 artifact/domain revision 创建投影，旧投影不得破坏性覆盖。

### 21.6 长篇和剧集无限展开

**风险**：一个工作流运行产生无界节点或任务。  
**应对**：Map/Batch 强制上限；章节和集使用 OrderedMap/Fold checkpoint；按页/批运行；设置预算和配额 gate；V1 只允许选定单集试跑。

### 21.7 WebAV 浏览器能力限制

**风险**：长视频内存、编码性能和浏览器兼容问题。  
**应对**：V1 不以浏览器编码导出为阻塞；V1.5 限定 Chromium、15 秒、5 个片段和 720p；保留未来服务端合成接口。

### 21.8 许可证

**风险**：当前 Toonflow 补充条款要求对多个第三方提供产品前取得书面商业授权，并限制品牌移除。  
**应对**：授权是 Step 0 gate，不以“仓库公开”推断可闭源商用。

### 21.9 真实 provider 与成本

**风险**：mock 工作流成功，但真实图片/视频 provider 的超时、失败率、轮询、密钥安全和成本不可接受。  
**应对**：V0 强制一个真实图片 provider 和真实存储；V1.5 才将真实视频设为阻断项；运行前做预算 reservation，运行后记录实际成本和偏差。

### 21.10 用户认知负担

**风险**：无限画布和节点库与 SeedV 面向“缺技术、缺时间”用户的定位冲突。  
**应对**：模板表单和 Agent 问答是默认入口，画布是高级展开视图；V0 必须证明用户不看画布也能完成一次生成。

### 21.11 内容安全与素材权利

**风险**：广告图、产品片、肖像和参考素材可能触发版权、品牌、内容安全和商用授权问题。  
**应对**：V0/V1 增加 Content Safety Review 和 Asset License Metadata；导出前对高风险结果做 policy gate；保留素材来源和授权元数据。

### 21.12 把参考图绑定误认为确定性“防变脸”

**风险**：把 Toonflow 的角色资产引用和多参考图生成宣传为骨骼绑定或确定性身份保持，导致产品承诺超过真实模型能力；provider 忽略参考图或在复杂镜头中发生身份漂移时，系统没有检测和恢复路径。  
**应对**：产品和技术统一使用“角色身份锚定/多参考条件生成”表述；持久化参考图用途和版本；增加 provider capability、Character Identity Review、阈值、Retry 和 Human Gate；没有一致性检测证据时不得宣称“保证不变脸”。

## 22. 禁止的实现方式

- 在 `WorkflowCanvas.vue` 中为每个节点增加固定 template slot；
- 继续扩展固定 `FlowData` 作为工作流定义；
- 由前端决定节点完成和工作流完成；
- Agent 输出 JSON 后直接 `setNodes/setEdges`；
- 让 LLM 返回数据库表名或直接写表；
- 用节点显示名称作为资产引用；
- 把角色参考图绑定宣传或实现为不存在的确定性骨骼绑定；
- 丢弃实际提交给 provider 的参考图版本、用途或顺序；
- 把 Human Gate 实现为前端弹窗而不持久化；
- 运行中读取最新草稿而不是固定 revision；
- 为每个模板复制 executor；
- 同时保留 Toonflow Socket.IO 状态和 SeedV SSE 状态作为双真相；
- 第一版引入任意代码节点；
- 为了“自由”允许无限 Agent 循环；
- 先做 Workflow Architect Agent，再做节点注册表和 compiler。

## 23. 计划变更协议

执行过程中允许修改计划，但必须：

1. 在文档末尾增加变更记录；
2. 说明触发证据，而不是只写“实现困难”；
3. 标记受影响步骤和依赖；
4. 更新验收标准和工作量；
5. 不删除原决策，改为 superseded；
6. 触及图模型、Artifact 真相源、运行状态机、授权或双后端时必须写 ADR。

步骤可以拆分，但不能跳过 Step 1、Step 1A、Step 2、Step 3 和 Step 8 的安全校验部分。

## 24. 新会话启动提示词

可在新的规划会话中使用：

```text
请阅读 SeedV 代码库的 AGENTS.md、VideoPipelineService、script_expert_v2、
ScriptExpertOrchestrator、媒体任务和 SSE 实现，并完整阅读：
docs/2026-07-11-toonflow-open-creation-platform-blueprint.md。

目标是创建一个新项目，不迁移 SeedV 数据。前端从一开始建立独立 Vue 3 + Vue Flow 动态工作流核心，
Toonflow Web 只作为锁定授权版本的交互参考和选择性组件供体。必须先执行 Step 1 两周核心/组件验证，
验证图片参考图子画布、分镜/资产选择和 WebAV 等组件能否通过 ArtifactRef/FastAPI adapter 解耦；失败则重写或放弃对应组件。
后端以 SeedV FastAPI 能力为基础。不要照搬 Toonflow 固定 FlowData，也不要把 SeedV
两套 workflow 做成黑盒。先审核蓝图中的 Step 0、Step 1、Step 1A 和 Step 2，给出新仓库目录、ADR、数据库
迁移、API contract 和前两个 PR 的执行计划。发现蓝图假设与当前代码不一致时，先
列证据并更新蓝图，不要静默改变架构。
```

## 25. 研究基线与参考位置

### Toonflow

- Toonflow Web：`https://github.com/HBAI-Ltd/Toonflow-web`
- Toonflow App：`https://github.com/HBAI-Ltd/Toonflow-app`
- 固定生产图：`src/views/production/utils/flowBuilder.ts`
- 固定画布 slot：`src/views/production/index.vue`
- 固定前端 store：`src/stores/productionAgent.ts`
- 图片参考图子画布：`src/views/production/components/editImage/`
- 分镜资产引用装配：Toonflow App `src/routes/production/storyboard/batchGenerateImage.ts`
- 视频多参考素材装配：Toonflow App `src/routes/production/workbench/getGenerateData.ts`
- WebAV 工作台：`src/views/production/components/workbench/`
- 当前许可证：两个仓库根目录 `LICENSE`

本次研究本地 commit：

- Toonflow Web：`9c4cb0e`
- Toonflow App：`bc61ec7`

正式立项时必须重新记录实际 fork 的完整 SHA，不应依赖本文的短 SHA。

### SeedV

- `backend/app/services/script_expert_orchestrator.py`
- `backend/app/services/script_expert_v2/`
- `backend/app/services/video_pipeline/`
- `backend/app/services/agno_agent_runner.py`
- `backend/app/tasks/script_expert_tasks.py`
- `backend/app/tasks/image_tasks.py`
- `backend/app/tasks/video_tasks.py`
- `backend/app/services/sse_manager.py`
- `backend/app/api/v1/events.py`
- `skills/agents/pipeline.yaml`，仅作历史模板参考
- `skills/project/project-types.yaml`
- `skills/prompts/shot-presets.yaml`

### 可借鉴但第一版不直接依赖

- Langflow 的节点注册、图校验和 Assistant Flow Proposal 模式；
- Infinite Canvas 的 Agent canvas operations 和用户确认模式；
- Vue Flow 的动态图编辑能力；
- WebAV 的浏览器媒体处理能力。

## 26. 多模型对抗评审记录

### 26.1 gpt-5.6-terra：架构评审

评审结论是初稿只能作为方向说明，不能直接进入实现。已采纳的 Critical/Major 项：

- 增加图执行 token、Merge/Join、error、cancel、retry 和 partial-run 语义；
- 章节扩写从普通 Map 改为 OrderedMap/Fold；
- ArtifactVersion 改为 canonical truth，领域表改为投影；
- 增加 lineage 和跨端口一致性约束；
- 增加 NodeRunAttempt、epoch、lease、task binding、outbox 和 RunEvent；
- 增加 CompiledExecutionPlan；
- 区分可删除和不可删除 Gate；
- Workflow Architect Agent 增加权限、预算和事务 CAS；
- SeedV 视频节点化前增加抽核层，禁止伪装固定 stage loop；
- Toonflow 从“已选底座”降级为“两周验证的候选 UI 供体”。

### 26.2 gpt-5.5：产品与交付评审

评审结论是初稿把完整平台 V1 当成 MVP，无法尽早获得用户反馈。已采纳的 Critical/Major 项：

- 分离 V0、V1、V1.5 和 V2；
- 两周 Toonflow spike，6-8 周用户内容 Demo gate；
- 默认入口改为模板表单和 Agent 问答，画布作为高级展开；
- V0 只要求一个真实图片 provider，真实视频和 WebAV 导出移至 V1.5；
- 增加授权 Go/No-Go；
- 增加 Generate Variants、Select/Rank、Plan Units、Constraint 等通用原语；
- 内置模板定义为只读 seed revision，不包含发布和市场；
- 长剧集降级为分集规划和选定单集试跑；
- 增加真实成本、内容安全和素材授权 gate；
- 调整团队、周期和商用 Beta 加固估算。

两位评审均为只读检查，没有修改工作区。最终修订由本文档作者统一完成。

## 27. 变更记录

| 日期 | 变更 | 原因 |
| --- | --- | --- |
| 2026-07-11 | 创建初稿 | 明确开放创作平台、SeedV 节点化、Workflow Architect Agent 和 Toonflow 适配边界 |
| 2026-07-11 | gpt-5.6-terra 架构对抗评审后修订 | 补充执行 token/merge/error/cancel/partial-run 语义、OrderedMap/Fold、Artifact canonical truth、attempt fencing、outbox、RunEvent、lineage、Gate policy 和 SeedV 抽核层 |
| 2026-07-11 | gpt-5.5 产品交付对抗评审后修订 | 收紧 V0/V1/V1.5/V2，增加两周 Toonflow spike、6-8 周用户 Demo gate、默认模板入口、真实 provider 成本门、授权 Go/No-Go 和 WebAV 降级 |
| 2026-07-11 | 补充 Toonflow Web/App 代码复核后修订 | 前端策略改为独立 Vue Flow 核心和 Toonflow 选择性组件复用；确认所谓“防变脸”实际为角色资产锚定、分镜引用、多参考图和首尾帧约束；增加身份一致性 Artifact、Review、provider capability 和风险边界 |
