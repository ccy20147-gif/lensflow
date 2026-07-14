# 控制流、批处理、子工作流与局部运行

## 1. 元数据

- ID：TF-WF-007
- 标题：控制流、批处理、子工作流与局部运行
- 状态：in_delivery
- 目标版本：V1 Core
- 优先级：P0
- 全局位置：平台内核/主画布
- 直接依赖：TF-WF-002、TF-WF-003、TF-WF-006
- 责任域：运行时平台
- 个人 DRI：main-agent

## 2. 背景与问题

专业创作需要条件分支、候选 fallback、批量媒体任务、顺序章节扩写和局部重跑。只有节点与边而没有控制 token、缺失输入、join、取消和 checkpoint 语义，会产生不可预测结果。

V1 允许有界控制流和固定 Revision 子工作流，不允许任意环、递归或无限自迭代。

## 3. 目标与非目标

目标：

- 冻结 Condition、Join、Fallback、Map、OrderedMap/Fold 和 SubworkflowCall 语义。
- 支持 selected、upstream、downstream 和 full 局部运行。
- 为批量与顺序任务提供上限、checkpoint、取消和恢复。
- 保持输出排序、错误归属和 lineage 确定。

非目标：

- 不支持 while、动态递归或无界嵌套。
- 不用完成先后顺序隐式决定业务选择。
- 不把每个 Map item 展开为主画布节点。

## 4. 用户与权限

- 工作流作者可配置允许的控制节点和有界参数。
- 编译器强制 Map 数量、Subworkflow 深度、预算和 policy gate。
- 用户可发起局部运行，但必须有权访问其固定输入。
- 系统管理员不能通过运行参数提升业务上限而绕过编译计划。

## 5. 用户场景与主流程

1. 用户连接 Condition 到多个分支并设置默认出口。
2. 编译器验证 control token、data dependency 和 join 规则。
3. 运行时只向选中分支发送 token，其他节点标记 skipped。
4. Map 对独立 item 并发执行；Fold 按 index 顺序保存 accumulator checkpoint。
5. SubworkflowCall 创建固定子 Revision 的独立 child run。
6. 用户从失败节点选择局部闭包重跑。

## 6. 功能需求

- FR-1：节点只有在必需数据可用且合法 control token 到达时进入 ready。
- FR-2：Condition/Switch 只激活选中分支，未选分支输出在本 run 中不存在。
- FR-3：Join 必须显式声明 any、all 或数据合并语义。
- FR-4：Fallback 必须声明可消费错误类别并输出兼容类型。
- FR-5：Map 必须声明最大 item 数、并发和每项失败策略，输出按输入 index 排序。
- FR-6：OrderedMap/Fold 固定顺序执行并在每项后持久化 accumulator checkpoint。
- FR-7：SubworkflowCall 必须固定 WorkflowRevision、typed port mapping、最大深度和预算。
- FR-8：编译器必须禁止递归依赖和超过允许深度的调用链。
- FR-9：selected 模式要求所有必需输入已固定。
- FR-10：upstream、downstream 和 full 模式必须生成确定依赖闭包。
- FR-11：取消、超时和错误必须按计划传播到 Map item、child run 和父节点。
- FR-12：每个展开单元保留独立 attempt、输入、输出和 lineage。

## 7. 交互与展示

- 控制节点显示分支、join、上限和失败策略摘要。
- Map/Fold 在节点工作台展示 item 进度，不把 item 铺到主画布。
- Subworkflow 节点可打开固定子 Revision 的只读预览和 child run。
- 局部运行前展示将执行、复用和跳过的节点集合。
- 缺失输入和不可运行闭包定位到具体端口。

## 8. 数据、类型与公共接口

CompiledExecutionPlan 记录 control edge、data edge、branch token、join policy、error edge、map bounds、fold schema、subworkflow revision 和 partial-run closure。

MapItemRun 与 FoldCheckpoint 引用父 NodeRun、item index、固定输入和输出 ArtifactRef。

SubworkflowBinding 关联父 NodeRun、child WorkflowRun、固定 revision 和端口映射。

## 9. 状态机与业务规则

未选分支使用 NodeRunStatus.skipped，不能读取历史 latest 作为本次输出。

Map item 状态独立；父节点完成条件由失败策略决定。Fold 只能从最近有效 checkpoint 的下一 index 恢复。

Child run 终态映射到父节点成功、可消费错误或失败。父取消默认传播到 child run。

## 10. 失败、降级与恢复

- 分支表达式无结果时使用显式默认分支，否则运行失败。
- Map 单项失败按 fail_fast、collect_errors 或 configured_fallback 处理。
- Fold checkpoint 损坏时回到最近可验证 checkpoint，不跳过连续性 Gate。
- Child run 不可创建时父节点失败且不产生输出。
- 局部运行遇到缺失外部输入时停止并报告端口，不读取 latest。

## 11. 安全、隐私、内容与授权

- 每个展开 item 和 child run 继承并收紧父 owner_scope 与预算。
- Subworkflow 不可扩大工具、Provider 或 Resource 权限。
- 分支表达式使用受控条件语言，不执行任意代码。
- 错误边只暴露安全错误类别，不传内部堆栈。
- 超限图在编译阶段拒绝，避免资源耗尽。

## 12. 观测与运营

- 指标包括分支选择、Map item 数、Fold checkpoint、child run 深度和局部运行规模。
- 监控超限拒绝、stuck item、递归检测和 checkpoint 恢复。
- trace 保留父子 run、item index 和错误传播关系。
- 成本按 item、child run 和父节点聚合，避免重复记账。

## 13. 验收标准

- AC-1：互斥分支只运行一个出口，未选分支标记 skipped 且无本次输出。
- AC-2：control_join_all 在所有分支完成或明确 skipped 后才继续。
- AC-3：Map 并发完成顺序随机时，最终输出仍按输入 index 排列。
- AC-4：Fold 在第 N 项崩溃后从 N 的前一有效 checkpoint 继续，不重写早期产物。
- AC-5：递归 Subworkflow 或超过最大深度在编译期被拒绝。
- AC-6：selected/upstream/downstream/full 四种模式产生与定义一致的闭包。

## 14. 测试场景

- 正常：Condition、Join、Fallback、Map、Fold、Subworkflow 和四种局部运行。
- 边界：空列表、最大 item 数、最大深度、全部 skipped 和可选输入。
- 失败：表达式错误、item 失败、checkpoint 损坏、child run 超时和缺失输入。
- 权限：child run 越权资源、超预算和扩大工具 scope 被拒绝。
- 并发/恢复：Map 乱序完成、Fold 重启和父子取消竞争。

## 15. 交付与回退

- V1 Core 分阶段启用控制节点，每种语义先通过 compiler 与 runtime contract tests。
- Subworkflow 初期设置保守最大深度和总节点上限。
- 功能开关关闭某控制类型时，旧 Revision 只读并提供迁移诊断。
- 回退不能把 Fold 改成并行 Map 或丢弃 checkpoint。

## 16. 已决策事项与开放问题

已决策：V1 只允许有界、无递归控制流；章节连续性使用 OrderedMap/Fold。

开放问题：各环境默认 Map 并发和 Subworkflow 深度由容量测试后设定，但必须进入编译计划。
