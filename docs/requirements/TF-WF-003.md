# 图编译、执行计划与策略校验

## 1. 元数据

- ID：TF-WF-003
- 标题：图编译、执行计划与策略校验
- 状态：verified
- 目标版本：Foundation
- 优先级：P0
- 全局位置：平台内核
- 直接依赖：TF-WF-002、TF-WF-004、TF-WF-005、TF-OPS-001、TF-SEC-001
- 责任域：工作流平台/安全
- 个人 DRI：main-agent

## 2. 背景与问题

合法 JSON 图不等于可执行、安全或可重放的计划。运行时若动态读取 latest 节点、资源、模型或策略，同一 WorkflowRevision 会产生无法解释的行为。

编译器必须在启动运行前解析全部版本、权限、预算、控制流和 Provider 能力，并生成不可变 CompiledExecutionPlan。

## 3. 目标与非目标

目标：

- 对图结构、类型、配置、权限、预算和能力做统一后端校验。
- 固定运行所需定义、转换器、资源和策略 revision。
- 将错误映射回具体节点、端口、配置或依赖。
- 支持旧计划重放，不能重放时明确拒绝并给出迁移诊断。

非目标：

- 不边运行边解释草稿或解析 latest。
- 不执行节点业务逻辑。
- 不以静态合法代替当前 entitlement 和运行前 Provider 健康检查。

## 4. 用户与权限

- 项目 owner 可以编译其有权访问的 WorkflowRevision。
- 宿主平台可为 owner 发起的 Workflow Architect 提案请求 dry-run 校验和估算；Agent 本身不能直接调用编译器或绕过策略。
- V1 私有项目不存在项目编辑者、审阅者或只读成员；这些项目角色统一后置 TF-TEAM-001。
- 后端编译服务读取注册表、资源、Provider、预算和安全策略的最小字段。
- 管理员不能通过客户端参数关闭强制 policy gate。

## 5. 用户场景与主流程

1. 用户将 WorkflowDraft 冻结为 WorkflowRevision。
2. 编译器读取固定 registry snapshot 和 graph。
3. 解析端口类型、转换器、ResourceRevision、Agent/Recipe/Subworkflow revision。
4. 校验控制流、依赖闭包、权限、entitlement、预算和 Provider 能力。
5. 生成不可变计划、canonical hash、估算和编译报告。
6. 运行服务只接受已成功编译且仍通过启动前策略复核的计划。

## 6. 功能需求

- FR-1：任何运行启动前必须存在 CompiledExecutionPlan。
- FR-2：编译必须验证节点与版本、端口连接、schema、cardinality 和 config。
- FR-3：必须拒绝非法环、不可达关键输出、悬空输出和缺失默认分支。
- FR-4：Map/Fold、Subworkflow 和批处理必须有上限、深度与预算声明。
- FR-5：所有 latest_at_compile 引用必须解析为固定 Revision 或 ArtifactVersion。
- FR-6：计划必须快照 NodeDefinition、转换器、executor、compiler 和 policy revision。
- FR-7：计划必须固定 ProviderSelectionPolicyRef 和使用的 CapabilitySnapshotRef。
- FR-8：编译必须检查 owner_scope、当前 EntitlementDecision 和素材权利 Gate；ArtifactRef 仅允许同 owner_scope，跨 owner 内容仅接受带授权证据的固定 ResourceRef。
- FR-9：预算、Provider 缺失和安全阻断必须产生结构化错误。
- FR-10：错误必须包含 node_instance_id、port_id 或 config path。
- FR-11：计划 hash 不受纯画布位置变化影响。
- FR-12：旧计划不可重放时必须拒绝并提供明确迁移路径，不能静默升级。

## 7. 交互与展示

- 编译结果在节点、端口和全局层展示阻断与非阻断诊断。
- 用户可查看解析后的关键 Revision、估算成本和控制降级摘要。
- 阻断、可降级和信息提示使用不同严重度。
- Workflow Architect 提案预览必须展示同一编译结果。
- 高级用户可下载净化后的计划摘要，不包含 secret 或内部凭证。

## 8. 数据、类型与公共接口

CompiledExecutionPlan 至少包含 workflow_revision_id、registry_snapshot、resolved_graph、definition_snapshots、converter_revisions、resolved_input_refs、executor_refs、provider_policy_ref、capability_snapshots、policy_revisions、budget_limits 和 compiler_version。

ProviderCompilationReport 遵循主表第 8.4 节。编译计划只引用 CredentialBinding ID，不包含 secret。

控制项的 `unsupported_policy` 只允许 `block | degrade | ignore_with_warning`；ProviderCompilationReport 的完整最终 outcome 为 `applied | transformed | degraded | ignored_with_warning | blocked`。触发 unsupported policy 时分别映射为 `blocked | degraded | ignored_with_warning`，不得另建缩写或平行枚举。

CompileDiagnostic 包含 code、severity、node_instance_id、port_id、config_path、safe_message 和 remediation。

## 9. 状态机与业务规则

CompileStatus 为 pending、succeeded、failed 和 invalidated。计划内容不可变。

仅纯 UI layout 变化可复用执行 hash；graph、config、schema、revision、权限策略或预算变化必须重新编译。

启动运行前重新计算当前 entitlement 和 Provider availability；变化造成新阻断时不得使用旧成功状态强行运行。

## 10. 失败、降级与恢复

- 注册表、权限或 Provider 能力服务不可读时编译失败，不猜测默认值。
- 可选控制项不受支持时，`unsupported_policy=degrade` 生成 `outcome=degraded`，`unsupported_policy=ignore_with_warning` 生成 `outcome=ignored_with_warning`；禁止写入上述集合之外的 outcome。
- required 控制项不受支持时产生 item `outcome=blocked` 并增加 `summary_counts.blocked`，禁止建立平行阻断错误字段。
- 编译超时不保存半成品计划；相同输入可幂等重试。
- 缓存丢失时从持久 WorkflowRevision 与固定快照重新生成。

## 11. 安全、隐私、内容与授权

- 编译器按 actor 与 project scope 读取资源，不能越权枚举。
- 所有跨 owner ResourceRef 必须有 GrantSnapshot，并计算当前 entitlement。
- ArtifactRef 只允许同 owner_scope；跨 owner ArtifactRef 即使附带 grant 字段也必须拒绝，内容须先提升为 ResourceRevision。
- 计划和诊断不暴露密钥、私有 URL 或原始安全策略细节。
- 恶意 config、超大图、深层 Subworkflow 和超大 Map 在解析阶段限额。
- 编译服务不执行用户代码或 Provider 请求。

## 12. 观测与运营

- 记录编译量、成功率、延迟、错误码、图规模和缓存命中。
- 监控权限阻断、预算阻断、旧版本不可重放和 Provider 降级趋势。
- 每个计划关联 compiler build SHA 与 policy revision。
- 对相同输入产生不同 plan hash 的情况立即告警。

## 13. 验收标准

- AC-1：同一 WorkflowRevision、registry snapshot 和策略输入重复编译得到相同 canonical hash。
- AC-2：修改节点位置不改变执行 hash，修改配置或固定 Revision 必须改变 hash。
- AC-3：构造类型错误、非法环、超限 Map 和无权 ResourceRef 时分别定位到具体节点或端口。
- AC-4：Provider 不支持 required 控制时以 `blocked` 阻断；可选控制只按策略形成 `degraded` 或 `ignored_with_warning`，ProviderCompilationReport 的所有条目均属于冻结 outcome 集合。
- AC-5：运行请求引用未成功编译计划时被拒绝。
- AC-6：旧 executor 不可用时返回迁移诊断，不自动替换为 latest。

## 14. 测试场景

- 正常：线性、分支、Map、Agent、Recipe 和 WorkbenchTask 图编译。
- 边界：50 节点、最大允许深度、可选输入和显式转换器。
- 失败：schema、权限、预算、Provider、依赖闭包和 compiler 超时。
- 权限：非 owner 项目访问、跨 owner 裸 ArtifactRef、缺授权 ResourceRef、撤权资源和无权节点均被阻断。
- 并发/恢复：并发相同编译去重；服务重启后读取或重建固定计划。

## 15. 交付与回退

- Foundation 交付 compiler、plan schema、diagnostic schema 和 contract tests。
- 新 compiler 先双跑比较 hash 与诊断，再切换为主版本。
- 回退 compiler 时只运行其明确支持的计划版本。
- 发布证据包含非法图矩阵、权限预算演练和旧版本重放/拒绝报告。

## 16. 已决策事项与开放问题

### 实施与独立验收证据

- 2026-07-16：固定 WorkflowRevision 编译、不可变 CompiledExecutionPlan、Registry/Provider 能力快照、结构化诊断、latest/secret 拒绝与 hash 确定性已实现。
- 编译 gate 以 canonical ArtifactVersion、Resource 与 ResourceRevision 的数据库 owner_scope 为准，不信任图中声明的 owner_scope；跨 owner ArtifactRef 一律拒绝，跨 owner ResourceRef 必须通过当前 GrantSnapshot/entitlement 重算。
- 独立验收：PostgreSQL 专项及 Compiler/Workflow/Artifact/Resource/Authorization 回归 143 passed；`alembic upgrade head`、`ruff check src tests` 与 `mypy src` 通过。

已决策：运行前冻结完整计划；Provider 不支持控制时不能静默忽略，策略值使用 `ignore_with_warning`，报告结果使用 `ignored_with_warning`。

开放问题：计划长期归档格式可在存储 ADR 中确定，但必须保存足够快照以重放或明确拒绝。
