# AI、媒体与交互质量评测基线

## 1. 元数据

- ID：TF-QLT-001
- 标题：AI、媒体与交互质量评测基线
- 状态：in_delivery
- 目标版本：Foundation -> V1 Core
- 优先级：P0
- 全局位置：全局质量治理
- 直接依赖：TF-GOV-001、TF-OPS-005
- 责任域：QA/AI 评测
- 个人 DRI：main-agent

## 2. 背景与问题

AI 文本、身份一致性、镜头控制和广告图质量不能用单张成功样例证明。模型和 Provider 持续变化，若没有固定样本、rubric、自动指标和回归容差，产品升级后无法判断质量是否退化。

本需求建立跨领域的评测合同，具体模型阈值在真实 provider spike 后以版本化基线冻结。

## 3. 目标与非目标

目标：

- 建立覆盖文本、媒体、镜头、身份和交互的固定测试集。
- 组合结构化校验、自动指标和盲评人工 rubric。
- 对模型、prompt、Recipe、Provider 和 UI 变更执行可比较回归。
- 保存输入、实际调用、输出、评分和裁决证据。

非目标：

- 不宣称自动指标等于艺术质量。
- 不用平均分掩盖安全、schema 或关键身份失败。
- 不保证角色绝对不变脸。

## 4. 用户与权限

- QA/AI 评测负责人管理测试集版本、rubric 和门槛。
- 领域专家维护小说、摄影、广告和交互评审维度。
- 评审员只能看到完成任务所需数据，敏感真人素材需特别授权。
- 开发者可运行测试，但不能改写已冻结结果。
- 发布负责人消费签名评测报告。

## 5. 用户场景与主流程

1. 责任域提交功能变更和受影响评测套件。
2. 系统固定代码、模型、Provider policy、CapabilitySnapshot 和输入集版本。
3. 执行生成与交互任务，保存所有候选和失败。
4. 自动校验 schema、控制遵从、性能和可复现字段。
5. 至少两名评审员按盲评 rubric 打分，重大分歧由第三人裁决。
6. 与已批准基线比较并生成通过、阻断或需复审结果。

## 6. 功能需求

- FR-1：测试集必须版本化并记录来源、授权、适用范围和内容 hash。
- FR-2：Foundation 至少定义文本、身份、镜头控制、51 镜头、广告图和核心交互六类套件。
- FR-3：每类套件必须定义不可平均豁免的 critical failure。
- FR-4：人工 rubric 使用明确分档与示例，不允许只写“质量好”。
- FR-5：主观项目至少双人盲评；分差超过一个等级时触发第三人裁决。
- FR-6：报告必须固定 ProviderInvocationRecord、输入 Revision、输出 ArtifactVersion 和成本。
- FR-7：真实 provider 基线建立后，回归容差和最低门槛作为不可变评测版本发布。
- FR-8：fallback 或模型版本变化必须单独分组，不得与原调用混算。
- FR-9：单次运行中的失败样本必须计入通过率。
- FR-10：评测结果必须可关联到需求 AC、实现版本和发布候选。

### 逐版本切片矩阵

| 能力 | Foundation | V1 Core |
| --- | --- | --- |
| 测试集 | 冻结六类最小样本和授权 | 扩充真实生产边界与 51 镜头集 |
| 指标 | schema、失败率、基础自动指标 | 身份、控制、连续性和交互综合门 |
| 人评 | rubric、盲评和裁决流程 | 稳定评审池与发布门 |
| 基线 | provider spike 后首个签名基线 | 版本/模型变更回归 |
| 报告 | 离线报告 | CI、预发布与定期漂移报告 |

## 7. 交互与展示

- 评测控制台按套件、版本、模型和功能显示结果。
- 报告同时展示平均值、分布、失败样本和 critical failure。
- 对比视图显示输入一致性、模型变化、成本和回归差异。
- 评审界面隐藏不必要的模型品牌和候选顺序，降低偏差。
- 发布门提供可下载的签名摘要和失败样本入口。

## 8. 数据、类型与公共接口

核心对象包括 EvaluationDatasetRevision、EvaluationCase、RubricRevision、EvaluationRun、EvaluationJudgement 和 QualityGateDecision。

生成输入输出通过 ArtifactRef/ResourceRef 固定；实际模型与成本引用 ProviderInvocationRecord。评测数据不得复制或改写业务 Revision。

QualityGateDecision 记录门槛版本、critical failure、统计摘要和裁决人。

## 9. 状态机与业务规则

EvaluationRun 使用 queued、running、completed、failed、cancelled 等运行状态，但不与 Workflow Run 共用记录。

Rubric 和 Dataset 只有冻结 revision 才能用于发布比较。修改样本或门槛创建新 revision。

基线更新必须说明旧基线不可比项，不得用新样本重算旧报告后覆盖历史。

## 10. 失败、降级与恢复

- Provider 故障计为失败样本，并可另标基础设施原因。
- 评审员中断后保存逐项进度，不暴露其他人评分。
- 输入授权撤回时停止新评测并隔离历史证据。
- 自动指标不可用时对应质量门保持未完成，不能默认为通过。
- 长评测从已完成 case 继续，重复 case 使用幂等键去重。

## 11. 安全、隐私、内容与授权

- 测试素材必须有评测、模型处理和保留授权。
- 真人肖像、声音和未成年人数据默认排除，除非有明确同意与安全方案。
- 评审员只能访问分配 case，导出内容带审计。
- prompt、provider 响应和日志在展示前净化 secret 与个人信息。

## 12. 观测与运营

- 记录套件通过率、评审一致率、critical failure、成本和时延趋势。
- 监控模型漂移、Provider fallback 比例和评测队列积压。
- 每次发布候选生成 QualityGateDecision。
- 定期检查样本泄漏、过拟合和 rubric 失效。

## 13. 验收标准

- AC-1：六类 Foundation 套件均存在不可变 dataset 和 rubric revision，且每个样本有来源与授权。
- AC-2：同一发布候选重复运行时，报告能区分随机波动、模型变化和基础设施失败。
- AC-3：出现任一 critical schema、安全或授权失败时，质量门必须阻断，不受平均分影响。
- AC-4：主观样本双评差异超过一档时自动进入第三人裁决。
- AC-5：任取一个评分可追溯到输入 Revision、输出 ArtifactVersion、ProviderInvocationRecord、评审者和门槛版本。

## 14. 测试场景

- 正常：真实 Provider 完成六类套件并生成签名报告。
- 边界：51 镜头、长文本、身份遮挡、复杂广告版式和移动视口。
- 失败：模型超时、输出 schema 错误、指标服务不可用和评审中断。
- 权限：未授权评审员无法查看真人或私有样本。
- 并发/恢复：并行 runner 不重复 case；中断后从已完成样本恢复。

## 15. 交付与回退

- Foundation 交付数据集、rubric、runner、报告格式和首个 provider spike 基线。
- V1 Core 将质量门接入预发布流程并扩充专业套件。
- 新门槛先 shadow 运行，再替换旧门槛。
- 回退到旧 rubric 或 dataset revision 时保留新旧报告，不重写历史裁决。

## 16. 已决策事项与开放问题

已决策：不能以单张样例或单一自动分数验收 AI/媒体质量。

开放问题：各套件绝对阈值在真实 provider spike 后冻结；冻结前必须记录候选值和证据，不能以未定阈值发布相关功能。
