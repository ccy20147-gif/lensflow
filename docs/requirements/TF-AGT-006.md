# Skill 定义、修订与受控装配

## 1. 元数据

- ID：TF-AGT-006
- 标题：Skill 定义、修订与受控装配
- 状态：in_delivery
- 版本：V1 Core
- 优先级：P0
- 全局位置：Agent Studio/资源库/平台内核
- 直接依赖：TF-WF-002、TF-WF-005、TF-SEC-001
- 责任域：Agent 平台/知识资源
- 个人 DRI：main-agent

## 2. 背景与问题

自定义 Agent 需要复用写作方法、领域规范、示例和知识片段，但若把 Skill 当作可执行插件，就会绕过 Tool 权限、网络策略和 V1 任意代码禁令；若只复制提示词，又无法版本化、授权、冲突检查和审计实际装配内容。

Skill 必须是一等 ResourceRevision，只提供非可执行的指令与知识上下文，由 Agent 编译器在固定 token 预算内装配。

## 3. 目标与非目标

- 目标：创建、试验、冻结和引用不可变 SkillRevision。
- 目标：在 Agent 编译时完成 schema、适用范围、token 预算、冲突、安全和授权检查。
- 目标：运行可追溯实际采用、裁剪或拒绝的 Skill 内容与顺序。
- 非目标：Skill 不执行代码、网络、模型、Provider、Tool、Agent、Workflow 或 Media Recipe。
- 非目标：V1/V1.5 不提供独立 Skill 社区 listing；只能私有使用、官方托管或作为已授权 Agent 包依赖。

## 4. 用户与权限

- V1 项目 owner 可创建和编辑自己 owner_scope 下的 SkillDraft，并在 Agent 中引用固定 SkillRevision。
- 平台 Skill 维护者可发布 managed SkillRevision；普通用户不能修改其内容。
- 跨 owner Skill 必须以 ResourceRef 使用，并具备当前 entitlement 与 GrantSnapshot；裸 ArtifactRef 不得跨 owner 装配。
- Skill 作者不能借 Skill 获取 CredentialBinding、工具 scope 或其他项目内容。

## 5. 用户场景与主流程

1. 用户在 Agent Studio 创建 Skill，声明用途、适用 Agent 角色、输入上下文、指令、示例和只读知识引用。
2. 系统扫描任意代码、秘密、越权数据请求、提示注入和与平台策略冲突的内容。
3. 用户在固定样例上预览装配顺序、token 占用、冲突和最终上下文摘要。
4. 提交时冻结 SkillRevision；AgentDraft 只保存固定 skill revision refs 与优先级。
5. Agent 编译器解析全部 Skill、SOP 和系统规则，生成可审计 SkillAssemblyPlan。
6. 运行 trace 保存实际采用/裁剪/拒绝项；Skill 更新或撤权只影响新编译，不改历史运行。

## 6. 功能需求

- FR-1：Skill 是 Resource，SkillDraft 可变，SkillRevision 通过专用 schema 的 ResourceRevision 固定内容。
- FR-2：Skill 内容至少支持 instructions、examples、read_only_knowledge_refs、applicable_agent_roles、required_context_schema 和 evaluation_notes。
- FR-3：SkillRevision 必须声明估算 token、最大装配 token、优先级范围、冲突标签、语言和内容安全分类。
- FR-4：Skill 不得包含脚本、可执行文件、任意 URL 抓取、ToolInvocation、CredentialBinding 或外部副作用声明。
- FR-5：知识内容必须是同 owner ArtifactRef 或固定 ResourceRef；跨 owner Artifact 必须先提升为 ResourceRevision。
- FR-6：Agent 编译固定所有 SkillRevision，禁止按名称或 latest 解析。
- FR-7：装配顺序必须确定：平台安全规则 > Agent managed policy > SOP step context > 显式 Skill priority > 创建顺序稳定键。
- FR-8：冲突检测必须覆盖相反指令、重复角色、输出 schema 冲突、超预算、越权数据要求和禁止行为。
- FR-9：超 token 预算时按版本化裁剪策略生成报告；required Skill 不得静默裁剪，可选 Skill 可拒绝并说明原因。
- FR-10：SkillAssemblyPlan 必须记录输入 revision、顺序、包含片段 hash、裁剪/拒绝、最终 token 预算和安全决策。
- FR-11：Skill 撤权、retired、内容安全暂停或知识引用失效时，新 Agent 编译阻断或按声明替换；历史运行保持可审计。
- FR-12：Agent/Workflow 包内嵌 Skill 需要 redistribute action；安装后执行仍需当前 reference/execute 相关 entitlement，且不复制 secret。

## 7. 交互与展示

- Studio 提供概览、指令、示例、知识、适用范围、预算/冲突、试装配和版本页。
- Skill 选择器显示修订、作者、token 估算、权限、冲突和是否 managed，不只显示名称。
- 装配预览按最终顺序展示来源与裁剪原因，敏感知识只显示有权摘要。
- 指令编辑使用结构化段落和示例项；不提供代码编辑器、终端或自定义网络入口。
- 移动端支持查看和选择固定 Skill；复杂编辑和冲突处理保留桌面端。

## 8. 数据、类型与公共接口

- `SkillContent` 包含 purpose、instructions[]、examples[]、knowledge_refs[]、applicability、assembly_policy 和 evaluation_notes。
- `SkillRevision` 是专用 ResourceRevision；AgentRevision 只保存 ordered `skill_revision_refs[]` 与 per-agent priority override。
- `SkillAssemblyPlan` 是 ArtifactVersion，包含 agent_revision_id、skill refs、resolved sections、token accounting、conflicts、security decisions 和 final_context_hash。
- Skill 本身没有 invoke 接口；唯一消费方是 Agent 编译器。Tool 能力继续由 TF-AGT-005 管理。
- PackageDependency 使用 `dependency_kind=skill`，并固定 revision、schema、inclusion_mode 和授权要求。

## 9. 状态机与业务规则

- SkillRevision 使用 RevisionStatus；安全暂停使用独立 policy/moderation 记录，不复用 revision 状态。
- SkillDraft 保存以 draft_version compare-and-swap；提交产生新 Revision，不覆盖旧内容。
- 相同 AgentRevision、Skill refs、SOP 和策略版本必须生成相同 assembly fingerprint；随机模型输出不参与编译。
- required Skill 任一阻断使 Agent 编译失败；optional Skill 被裁剪后必须进入报告和运行详情。
- Skill 激活不等于社区上架，V1.5 Agent listing 也不自动创建独立 Skill listing。

## 10. 失败、降级与恢复

- schema、授权、安全或冲突校验失败时定位 Skill/section，不生成半合法 AgentRevision。
- token 估算服务不可用时使用保守上限；无法证明预算时 required Skill 编译 fail closed。
- 知识 Blob 暂不可用时 optional Skill 可明确跳过，required Skill 阻断；不得替换为 latest 或未知内容。
- 并发编辑冲突返回三方 diff；服务重启后从 SkillDraft 和装配 Artifact 恢复。
- 撤权事件投递失败由 outbox 重放，Agent 每次新编译仍执行权威 entitlement 检查。

## 11. 安全、隐私、内容与授权

- 扫描提示注入、数据外传指令、凭证诱导、恶意角色覆盖、违法内容和隐藏编码载荷。
- 私有知识按最小字段装配，普通日志不保存完整正文、签名 URL 或敏感样例。
- Skill 不能声明“忽略系统规则”、扩大 Tool scope、访问未连接资源或关闭安全 Gate。
- 复制/导出 Agent 时 Skill 依赖按 LicenseOffer/GrantSnapshot 处理，禁止无权再分发。

## 12. 观测与运营

- 事件：skill_draft_saved、skill_validated、skill_revision_activated、assembly_compiled/blocked、skill_retired/revoked。
- 指标：装配成功率、平均 Skill 数/token、冲突率、裁剪率、撤权阻断、提示注入命中和 Agent 质量变化。
- 审计可从 AgentRun 追到 AgentRevision、全部 SkillRevision、最终上下文 hash、裁剪和安全决定。
- TF-QLT-001 可按 SkillRevision 比较固定任务质量，但评分不能绕过安全或 schema 阻断。

## 13. 验收标准

- AC-1：Given 三个合法 SkillRevision，When 编译 Agent，Then 顺序、token、内容 hash 和最终 assembly fingerprint 可重现。
- AC-2：Given Skill 包含代码、任意网络或 ToolInvocation，When 提交/编译，Then 阻断并定位违规 section。
- AC-3：Given required Skill 超预算，When 编译，Then Agent 不可运行；改为 optional 后可按报告裁剪且不静默。
- AC-4：Given 跨 owner Skill 无 redistribute/reference 权限，When 打包或运行，Then 相应动作被当前 entitlement 阻断。
- AC-5：Given SkillRevision 撤权，When 重放历史运行与创建新运行，Then历史 trace 可读，新编译阻断或要求替换。
- AC-6：Given 两个相反输出格式 Skill，When 装配，Then 冲突报告指出双方 revision，不能按最后写入覆盖。

## 14. 测试场景

- 正常：创建、试装配、提交、Agent 引用、包内嵌和固定 revision 运行。
- 边界：零 Skill、最大允许 Skill 数、长知识、重复示例、多语言和 token 临界。
- 失败：schema、冲突、知识缺失、超预算、提示注入、撤权和安全暂停。
- 权限：跨 owner reference/redistribute、无权知识、私有样例泄漏和 managed Skill 修改。
- 并发/恢复：双端草稿 CAS、重复提交、撤权与编译竞态、服务重启和 outbox 重放。

## 15. 交付与回退

- V1 Core 先开放官方 managed Skill 和 owner 私有 Skill；独立社区 listing 保持关闭。
- Skill schema、装配策略和安全规则版本化；旧 AgentRevision 继续引用原 SkillRevision。
- 回退装配器时未知 Skill schema 只读并阻断新编译，不进行有损重存或自动 latest 替换。
- 交付证据包括装配确定性、token/冲突、安全、授权、撤权、包依赖和恢复测试。

## 16. 已决策事项与开放问题

- 已决策：Skill 是非可执行指令/知识 Resource；任何副作用只能由 TF-AGT-005 Tool 产生。
- 已决策：V1/V1.5 不提供独立 Skill listing；随 Agent 包分发必须具备 redistribute 权限。
- 开放问题：首批 managed Skill 目录和默认 token 预算由官方 Agent 基准评测后冻结，不改变本合同。
