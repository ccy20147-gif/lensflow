# 工作流模板与依赖包

## 1. 元数据

- ID：TF-WF-009
- 标题：工作流模板与依赖包
- 状态：in_delivery
- 目标版本：V0 -> V1 Core
- 优先级：P0
- 全局位置：模板入口/平台内核
- 直接依赖：TF-GOV-002、TF-WF-002、TF-WF-003、TF-WF-004、TF-WF-005、TF-SEC-001
- 责任域：模板产品/工作流平台
- 个人 DRI：main-agent

## 2. 背景与问题

非技术用户需要从模板直接创作，高级用户需要复制并修改普通工作流。若模板依赖隐藏后端路径、私有资源、secret 或动态 latest，模板无法复现，也不能安全进入社区。

V0 先交付平台内置模板，V1 Core 冻结 typed package、依赖闭包与替换槽；社区上架由 TF-COM-003 管理。

## 3. 目标与非目标

目标：

- V0 用普通公开节点交付可复制内置模板。
- V1 定义 WorkflowPackageManifest 和 typed PackageDependency。
- 导入、复制和实例化时校验版本、权限、能力和替换槽。
- 保留模板来源与派生 lineage。

非目标：

- 不在本需求实现社区 listing、搜索或交易。
- 不把 secret、CredentialBinding 或私有 Blob 打进包。
- 不允许模板专属隐藏 API 冒充公共节点。

## 4. 用户与权限

- 平台模板维护者可以创建和更新内置模板 Revision。
- 项目 owner 可以实例化有权模板并得到个人 WorkflowDraft。
- 包导入者只能使用当前有 entitlement 的可复用依赖。
- 安全审核者批准 managed preset 和需要替换的敏感依赖。

## 5. 用户场景与主流程

1. 用户从目标入口选择内置模板并填写参数。
2. 系统读取固定模板 WorkflowRevision 和依赖清单。
3. 校验节点、schema、资源、Provider 能力和许可。
4. 对 replacement slot 要求用户选择合法替代项。
5. 创建项目 WorkflowDraft，记录模板 revision 和映射。
6. 用户可以不打开画布运行，也可以展开普通节点修改。

## 6. 功能需求

- FR-1：每个模板必须固定 WorkflowRevision，不动态追随 latest。
- FR-2：V0 内置模板必须由注册公共节点和明确参数组成。
- FR-3：模板实例化必须创建独立 WorkflowDraft，不修改模板 Revision。
- FR-4：WorkflowPackageManifest 必须列出全部 PackageDependency。
- FR-5：依赖必须声明 kind、revision、schema、inclusion_mode、grant 和 capability 要求。
- FR-6：包依赖闭包必须无循环且可验证。
- FR-7：无法内嵌或直接复用的依赖必须声明 typed replacement slot。
- FR-8：包不得包含明文 secret、CredentialBinding 或未授权私有内容。
- FR-9：实例化前必须重新计算 entitlement、Provider 能力和当前安全策略。
- FR-10：导入包视为不可信输入，先进入 Draft 并经 compiler 校验。
- FR-11：实例化结果保存模板来源、依赖映射和 attribution manifest。
- FR-12：缺失依赖必须阻断或由明确 replacement 解决，不能静默删除节点。

### 逐版本切片矩阵

| 能力 | V0 | V1 Core |
| --- | --- | --- |
| 模板来源 | 平台内置固定 Revision | 内置及可导入包 |
| 依赖 | 平台 managed preset 和基础检查 | typed dependency、闭包、授权 |
| 替换 | 简单 Provider/输入参数 | schema 化 replacement slot |
| 实例化 | 创建个人 Draft | 保留完整 lineage/attribution |
| 发布 | 不含社区上架 | 只准备包合同，社区由 COM-003 |

## 7. 交互与展示

- 模板卡展示目标、输入、预估步骤、Provider 要求和是否需人工确认。
- 实例化表单只暴露用户可理解的业务参数。
- 依赖检查页分为已满足、需替换、无权和不可用。
- 用户可预览主阶段和成本范围，再创建项目。
- 展开画布后看到普通节点，不出现模板专属黑盒。

## 8. 数据、类型与公共接口

严格使用主表第 8.4 节 PackageDependency 与 WorkflowPackageManifest。

TemplateRecord 引用 template_id、workflow_revision_id、parameter_schema、default_mapping、visibility 和 provenance。

实例化记录包含 source_template_revision、created_workflow_id、dependency_resolution 和 attribution_manifest。

## 9. 状态机与业务规则

模板内部 revision 使用 RevisionStatus；是否社区可见由 ListingStatus 管理，但不在本需求交付。

模板更新创建新 Revision，已实例化 WorkflowDraft 不自动升级。

依赖解析结果与实例化请求绑定；解析后目标 revision 变化时必须重新校验。

## 10. 失败、降级与恢复

- 依赖缺失或无权时不创建可运行半模板。
- 项目创建中断时使用事务或补偿清理孤儿 Draft。
- Provider 暂不可用时可保存 Draft，但运行前仍需通过能力校验。
- 导入包 schema 版本未知时拒绝并提供迁移诊断。
- replacement 中途取消不保留敏感候选缓存。

## 11. 安全、隐私、内容与授权

- 包扫描未知文件、脚本、外链和 secret。
- 跨 owner 资源必须有 GrantSnapshot，实例化时重新计算 entitlement。
- attribution manifest 不得被用户无痕删除。
- managed preset 由平台固定，不允许包覆盖其实现。
- 模板预览不泄露私有 prompt、资源或凭证。

## 12. 观测与运营

- 记录模板查看、实例化、依赖失败、replacement 和首次运行转化。
- 监控隐藏依赖、循环、secret 扫描和不可重放包。
- 按 template revision 跟踪错误率和成本偏差。
- 保留实例化 lineage 便于下架和安全通知。

## 13. 验收标准

- AC-1：V0 内置模板实例化后可在画布看到全部普通节点并成功保存。
- AC-2：含私有、缺失或循环依赖的包被阻断并定位具体 dependency。
- AC-3：包含 secret 或 CredentialBinding 的包导入失败。
- AC-4：满足 replacement slot 后，实例化图通过编译且保存替换 mapping。
- AC-5：模板发布新 Revision 后，既有项目仍固定原 revision 与依赖。
- AC-6：任一实例化项目可追溯模板、包、依赖和 attribution。

## 14. 测试场景

- 正常：内置模板、managed preset、可复用依赖和 replacement。
- 边界：无依赖模板、大依赖闭包、旧 schema 和 retired revision。
- 失败：循环、缺失、无权、secret、Provider 不可用和创建补偿。
- 权限：跨 owner 资源、私有 Agent/Recipe 和被撤权依赖。
- 并发/恢复：模板升级与实例化竞争、重复请求和中断恢复。

## 15. 交付与回退

- V0 交付模板目录、参数表单、实例化和至少两个总体发布门模板入口。
- V1 Core 增加包导入、typed 依赖和 replacement slot。
- 包解析器使用版本化 schema，可回退上一解析器处理其支持版本。
- 回退不修改已实例化 WorkflowDraft 或历史模板 Revision。

## 16. 已决策事项与开放问题

已决策：模板只能由公开节点组成；社区上架属于 TF-COM-003。

开放问题：包的物理归档格式可选择 JSON bundle 或压缩包，但不得改变 typed manifest、secret 排除和依赖闭包。
