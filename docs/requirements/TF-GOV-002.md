# 第三方代码来源、许可与 clean-room Gate

## 1. 元数据

- ID：TF-GOV-002
- 标题：第三方代码来源、许可与 clean-room Gate
- 状态：in_delivery
- 目标版本：Foundation
- 优先级：P0
- 全局位置：全局治理/工程基线
- 直接依赖：TF-GOV-001
- 责任域：法务/工程治理
- 个人 DRI：main-agent

## 2. 背景与问题

Toonflow Web/App、SeedV 和其他开源或参考项目具有不同许可、品牌和托管条件。仓库可读不等于允许复制、闭源修改、SaaS 托管或移除品牌。

新产品必须能在任何候选组件未获授权时继续以 clean-room 方式建设，避免许可结论反向绑架平台架构。

## 3. 目标与非目标

目标：

- 对每个候选第三方组件建立可审计来源与许可裁决。
- 在复制代码或资产前明确 reuse、rewrite 或 abandon。
- 保证 NOTICE、署名、品牌和分发义务进入发布门。
- 验证 clean-room 回退不依赖受限实现。

非目标：

- 不裁决用户上传素材或社区资源权利，后者属于 TF-SEC-001。
- 不因参考代码存在而继承其后端、数据库或运行时。
- 不为缺少书面依据的商业使用作推定。

## 4. 用户与权限

- 法务负责人批准许可解释和书面授权证据。
- 工程治理负责人维护组件来源、SHA、文件映射和修改记录。
- 组件负责人提交复用价值、耦合、替代成本和 clean-room 方案。
- 发布负责人只能消费已批准决策，不能临时豁免 P0 许可阻断。
- 许可原文和法律意见按敏感级别限制访问。

## 5. 用户场景与主流程

1. 工程师登记候选组件、仓库 URL、commit SHA、许可证和拟复用范围。
2. 自动扫描与人工审查识别代码、视觉资产、字体、品牌和传递依赖。
3. 法务核对 SaaS、闭源修改、品牌替换、NOTICE 和生成内容条款。
4. 团队记录 reuse、clean-room rewrite 或 abandon 决策。
5. reuse 项建立文件级 provenance；rewrite 项隔离参考材料和实现人员记录。
6. 发布前生成 NOTICE 与组件清单并执行回退演练。

## 6. 功能需求

- FR-1：每个候选必须固定仓库、commit SHA、许可证文本 hash 和获取日期。
- FR-2：台账必须细化到组件或文件集合，禁止只写仓库级“可用”。
- FR-3：裁决必须覆盖 SaaS、托管、闭源派生、再分发、品牌替换、署名和 NOTICE。
- FR-4：依赖扫描必须记录直接与传递许可证及潜在冲突。
- FR-5：reuse 决策必须关联书面依据、允许范围和持续义务。
- FR-6：rewrite 决策必须定义 clean-room 输入、禁止复制内容和验收者。
- FR-7：abandon 决策必须记录替代能力或明确删除范围。
- FR-8：任何未裁决组件不得进入生产构建或发布资产包。
- FR-9：修改第三方文件必须记录原文件、修改摘要和目标文件。
- FR-10：发布产物必须自动生成或校验 LICENSE、NOTICE 和署名清单。

## 7. 交互与展示

- 台账按组件显示来源、版本、许可状态、决策和阻断原因。
- 详情页展示拟使用范围、传递依赖、书面证据和义务清单。
- 构建检查结果必须定位到具体包、文件或资产。
- clean-room 状态展示参考审阅、独立实现和相似性复核三个检查点。
- 不向普通产品用户展示内部法律意见正文。

## 8. 数据、类型与公共接口

核心记录包括 ThirdPartyComponent、SourceSnapshot、LicenseEvidence、ReuseDecision、ProvenanceEntry 和 NoticeEntry。

ReuseDecision 使用 candidate、under_review、approved_reuse、clean_room_rewrite、abandoned、blocked 等治理值，不得复用 RevisionStatus 或 ListingStatus。

SourceSnapshot 必须保存可验证 SHA；ProvenanceEntry 关联源文件集合、目标文件集合、决策 ID 和修改者。

## 9. 状态机与业务规则

候选组件先进入 candidate，再进入 under_review，最终只能选择 approved_reuse、clean_room_rewrite、abandoned 或 blocked。

许可证、使用范围、上游版本或商业模式发生变化时，原裁决失效并重新评审。旧裁决保留，不能原地覆盖。

只有 approved_reuse 可以复制受审范围；clean_room_rewrite 只能使用允许的功能观察和公开接口证据。

## 10. 失败、降级与恢复

- 无法确认许可证或品牌条款时默认 blocked，不以沉默视为允许。
- 自动扫描失败时构建保持阻断，并要求人工清单。
- 上游删除标签或修改许可证时，以已固定 SourceSnapshot 为证据并启动复评。
- 发现未登记代码时隔离相关构建，定位提交并选择移除或补审。
- clean-room 验证不通过时删除受影响实现并从独立规格重做。

## 11. 安全、隐私、内容与授权

- 法律意见、合同和联系人信息只向授权人员开放。
- 台账导出必须净化签名、地址和非必要个人数据。
- 不允许将私有仓库凭证写入 provenance 或构建日志。
- 生成内容权利与模型条款由 Provider 和安全需求另行检查，不能由本项一并假设。

## 12. 观测与运营

- 指标包括候选数量、未裁决数量、复用/重写比例、扫描阻断和 NOTICE 漂移。
- 每次依赖升级和发布候选都执行许可差异扫描。
- 对生产构建出现未知包、未知字体或未知资产立即告警。
- 每季度抽查 approved_reuse 组件的实际使用范围是否越界。

## 13. 验收标准

- AC-1：任取一个复用组件，可查到固定 SHA、许可证 hash、书面依据、文件范围和 NOTICE 义务。
- AC-2：将未登记依赖加入生产构建时，CI 必须失败并报告包名与引入路径。
- AC-3：把 approved_reuse 改为 blocked 后，clean-room 构建仍可启动 FastAPI 与独立 Vue Flow 核心。
- AC-4：发布包中的 LICENSE/NOTICE 与台账 approved_reuse 集合逐项一致。
- AC-5：对 Toonflow 候选组件可分别给出复用、重写或放弃结论，不存在仓库级模糊批准。

## 14. 测试场景

- 正常：有明确许可组件完成审查并生成 NOTICE。
- 边界：同仓库不同目录适用不同条款，台账分别裁决。
- 失败：许可证缺失、品牌替换受限或传递依赖冲突导致构建阻断。
- 权限：普通工程师不能把 under_review 直接改为 approved_reuse。
- 并发/恢复：审查期间上游升级产生新 SourceSnapshot，旧裁决不自动继承；回退到旧 SHA 可重建。

## 15. 交付与回退

- Foundation 交付第三方台账、扫描规则、裁决模板、NOTICE 生成和 clean-room 演练。
- Toonflow 组件复用必须在具体文件进入产品前完成本 Gate。
- 回退路径是停止复制、移除受限实现并启用独立 Vue Flow/FastAPI 替代。
- 发布证据包含锁定 SHA 清单、构建扫描报告和一次受限组件移除演练。

## 16. 已决策事项与开放问题

已决策：未获许可不阻塞独立核心建设；不得从仓库公开性推定商业授权。

开放问题：具体 Toonflow 组件的最终 reuse/rewrite/abandon 结论由逐组件证据决定，不在本需求中预判。
