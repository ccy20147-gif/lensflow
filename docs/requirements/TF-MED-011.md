# TF-MED-011 音频、配音、音乐、音效与字幕

## 1. 元数据

- ID：TF-MED-011
- 标题：音频、配音、音乐、音效与字幕
- 状态：defined
- 版本：V1 Core -> V1.5
- 优先级：P1
- 全局位置：影视工作区
- 直接依赖：TF-STY-003、TF-WF-006、TF-OPS-001、TF-OPS-003、TF-SEC-001
- 责任域：音频产品/媒体平台
- 个人 DRI：待指派

## 2. 背景与问题

对白、旁白、音乐、音效和字幕具有不同来源、权利、时间与混音语义。把它们合成一个不可编辑音轨会阻断角色音色复用、字幕校对、撤回处理和片段替换。

真人声音克隆具有高冒充风险，必须建立可验证同意、用途范围、披露、撤回和敏感派生数据清理，而不是仅用勾选框声明。

## 3. 目标与非目标

- 目标：输出分轨、可定位、可替换的对白/旁白/音乐/SFX/字幕 Artifact 与时间映射。
- 目标：V1 Core 提供托管合成音色、基础音乐/SFX 和字幕；V1.5 增加经授权自定义声音与更强对齐/混音。
- 目标：用 TF-QLT-001 评测可懂度、角色匹配、发音、同步、响度、音乐/SFX 适配与字幕准确率。
- 非目标：V1 Core 不开放无同意真人声音克隆，不提供完整 DAW 或电影级最终混音。
- 非目标：音乐生成不自动授予商业权，导出仍按当前 entitlement 检查。

逐版本切片：

| 切片 | 功能 | 数据兼容 | 独立验收证据 |
| --- | --- | --- | --- |
| V1 Core | 托管 TTS、角色音色绑定、对白/旁白、基础音乐/SFX、字幕时间与分轨包 | 公共 ArtifactVersion 和稳定 track schema | 多角色对白、字幕、音乐/SFX、失败恢复和权限 E2E |
| V1.5 | 有凭证的自定义声音、精细对齐、替换、基础混音/ducking 与交付 stems | V1 Core tracks 原样可读；新增 consent/voice revision 和 mix 字段可选 | 同意/撤回、对齐/混音质量与旧项目兼容 E2E |

## 4. 用户与权限

- V1 私有项目只有项目 owner 可分配托管音色、生成分轨、校对字幕、选择结果和导出；共享编辑/审阅/只读角色后置到 TF-TEAM-001。
- Character owner 控制角色音色/声音资源修订及可用范围；跨 owner 使用固定 revision/grant。
- 自定义真人声音仅由通过身份/同意验证的主体或合法代理创建，运营审批不能代替权利人同意。
- 项目 owner 导出时必须拥有所有音乐、SFX、声音和文本的当前使用/商业权限。

## 5. 用户场景与主流程

1. 用户从固定剧本/ShotPlan 提取对白、旁白、音乐提示、SFX cue 和字幕文本。
2. 用户为角色绑定托管 VoiceRevision；V1.5 可选择带有效 ConsentEvidence 的自定义声音。
3. 系统校验发音词典、语种、情绪、时间目标、授权、安全和 provider 能力，固定 CapabilitySnapshot 与 ProviderCompilationReport，并在网络前同一事务提交 NodeRunAttempt、ProviderInvocationAttempt 与 `purpose=provider_dispatch` 的 OutboxEvent。
4. dispatcher 使用稳定 provider idempotency key 提交；无法确认 provider 是否接收时进入 unknown 并只查询/回调/账单/人工对账，不盲目重提。
5. 结果以 execution epoch/fencing token 条件验证，在同一事务保存独立轨道 ArtifactVersion、最多一条 ProviderInvocationRecord、多条 ProviderOutputBinding、实际用量/成本与 `purpose=result_publish` 的 OutboxEvent，并生成 word/phoneme/subtitle timing 与质量报告。
6. 用户试听、校对、局部重生和替换；音乐/SFX 与对白分轨保持独立。
7. 时间线消费固定轨道 refs；同意撤回后阻断新行为并标记受影响草稿，不破坏隔离历史审计。

## 6. 功能需求

- FR-1：轨道类型至少为 dialogue、narration、music、sfx、subtitle，均有稳定 track_id、source refs 和时间范围。
- FR-2：TTS 请求固定文本/剧本修订、speaker/VoiceRevision、语言、发音词典、情绪/表演、速度、目标时长和 provider policy。
- FR-3：托管音色与自定义声音分离；V1.5 自定义 VoiceRevision 必须关联 consent evidence、主体、用途、期限和披露要求。
- FR-4：同一角色跨镜头固定 VoiceRevision，不按显示名读取 latest；换声生成新 Revision 并传播 stale。
- FR-5：支持句子/台词级局部重生，保留前后 handle 与 timing；不得重写未选中的其他台词 Artifact。
- FR-6：字幕输出至少含 cue_id、start/end、text、speaker、source span 和 language；时间单调且不重叠规则可配置。
- FR-7：音乐/SFX 请求或导入必须记录情绪/场景/时长、来源、授权、循环/淡入淡出和实际 provider/文件信息。
- FR-8：V1.5 支持基础 gain、pan、fade、ducking 与 stems，不提供完整插件链或任意音频代码。
- FR-9：每次真实外部请求在网络副作用前必须于同一数据库事务提交 NodeRunAttempt、ProviderInvocationAttempt 与 provider_dispatch OutboxEvent；每个输出保存独立音频 ArtifactVersion/ProviderOutputBinding、波形代理和质量/安全报告，一个 Attempt 最多形成一条 ProviderInvocationRecord。
- FR-10：输出媒体元数据含 codec、sample_rate、channels、duration_ms、loudness_lufs、true_peak_dbtp、checksum。
- FR-11：TF-QLT-001 固定集评测 ASR 字错率、专名发音、说话人相似/合规、同步误差、响度和人工自然度。
- FR-12：撤回同意后阻断新克隆/生成/重跑/导出，撤销 provider 可撤模型并清理本地敏感派生数据。
- FR-13：provider 提交使用稳定 idempotency key（支持时传递）；发送后响应丢失或无法确认接收时 AttemptStatus 进入 unknown，只允许查询、回调、账单或人工对账，禁止按超时盲目重提。
- FR-14：ProviderInvocationAttempt 的 attempt_status 复用关联 NodeRunAttempt 的公共 AttemptStatus；provider task 内部 submitted/queued/processing 等阶段只作为事件或 binding 明细，不建立任何 provider 专用公共状态枚举。
- FR-15：fallback 必须从原始固定输入重新获取 CapabilitySnapshot、重新编译声音/音乐/控制、重新验证授权与同意并重新估算成本；确认需要新外部请求后创建新的 NodeRunAttempt、ProviderInvocationAttempt 与 dispatch OutboxEvent，原 attempt/record 保留。
- FR-16：结果发布必须以 execution epoch/fencing token 条件更新，并在一个事务内写入输出 ArtifactVersion、每 Attempt 最多一条 ProviderInvocationRecord、一个或多个 ProviderOutputBinding、实际用量/成本与 result_publish OutboxEvent；事务失败不得形成部分轨道或部分计费。

## 7. 交互与展示

- 工作台按轨道类型显示波形、片段、角色、语言、授权和质量状态，不把所有声音烘焙成一条不可编辑轨。
- 台词编辑显示源 span、文本、发音、情绪、目标时长和实际时长；局部重生前显示预计成本。
- 字幕可在播放器内逐 cue 校对，长文本自动换行且不遮挡相邻控件。
- 自定义声音显著展示“合成声音”披露、同意主体/范围/到期与撤回入口，不以默认勾选隐藏。
- 移动端支持试听、字幕校对和候选选择；精细波形/混音保留桌面端。

## 8. 数据、类型与公共接口

- `VoiceRevision` 是专用 ResourceRevision，内容含 voice_kind(managed/custom)、speaker_subject_ref、language/style capabilities、consent_evidence_ref 和 provider_voice_ref 加密引用。
- `AudioGenerationRequest` Artifact 含 track_type、source_ref、voice_ref、performance、timing_target、provider_policy_ref 和 grant refs。
- `AudioTrackArtifact` 内容含 track_id、media_ref、time_range、speaker/source refs、media metadata、quality/moderation refs。
- `SubtitleArtifact` 含 cues、language、source refs、alignment_revision 和 format variants；仍是 ArtifactVersion。
- `ConsentEvidence` 由 TF-SEC-001 定义权威结构，本需求只保存引用和运行时 decision，不复制同意真相。
- 时间线只引用固定 AudioTrack/Subtitle ArtifactRef；任何重生或校对产生新版本。
- provider 调用严格沿用主表 8.4 的 CapabilitySnapshotRef、ProviderCompilationReport、ProviderInvocationAttempt、OutboxEvent、ProviderInvocationRecord 和 ProviderOutputBinding；一个 ProviderInvocationAttempt 最多一条 ProviderInvocationRecord，一条 ProviderInvocationRecord 可通过一个或多个 ProviderOutputBinding 关联多条音频候选或 stem。

## 9. 状态机与业务规则

- 音频任务随公共 Run/NodeRun 状态；同意使用 Grant/Policy 状态，不创建混合 `voice_published` 枚举。
- VoiceRevision 激活不等于社区上架；自定义声音 consent 过期/撤回时新调用即阻断。
- 相同 request fingerprint/attempt 的回调幂等；重复 provider 结果不得重复 Artifact 或记账。
- ProviderInvocationAttempt 直接复用公共 AttemptStatus；unknown 在对账前不是可重试终态，只有 waiting_external/unknown 已对账并收敛到公共终态后才可形成 ProviderInvocationRecord；晚到或被新 execution epoch 取代的 attempt 使用 superseded 且不得发布结果。
- 字幕 cue 编辑生成新 ArtifactVersion，不能原地修改已进入 CreativeWorkRevision 的字幕。
- 轨道替换按 track_id/time range 传播 stale；历史时间线和合法运行记录保持不变。

## 10. 失败、降级与恢复

- provider 不支持目标语种、情绪、时长或 voice 时按 blocked/degraded/ignored_with_warning 报告，声音同意不可降级忽略。
- TTS 部分台词失败时保留成功片段；失败句只能在原 attempt 已明确终结或 unknown 对账收敛后，以显式新请求按完整快照/编译/授权/估算流程补齐，拼接必须检测间隙、爆音和时间偏差。
- 对齐失败时轨道可试听但字幕标“未对齐”且不能通过强制同步 Gate。
- 回调丢失、刷新或服务重启从 ProviderInvocationAttempt、dispatch OutboxEvent、provider binding/Artifact/RunEvent 恢复；unknown 只对账，取消或过期 epoch 的晚到结果隔离且不得发布 Record/OutputBinding/成本。
- fallback 重新快照、编译、授权/同意校验和估算并创建新 attempt/outbox；不得将两次真实调用合并为一条 Record，也不得把一次多输出调用拆成多条 Record。
- 自定义声音 provider 撤销失败时平台先阻断所有调用并告警人工跟进，不等待外部删除才生效。

## 11. 安全、隐私、内容与授权

- 真人声音克隆必须验证主体身份、明确同意、用途、期限、撤回方式和合成披露；未成年人默认阻断，例外需监护同意与人工审核。
- 禁止无同意模仿名人、公众人物或私人个体进行冒充、欺诈或误导；高风险文本/声音组合在生成和导出双重 Gate。
- 声纹、训练样本、provider voice id 和人声裁剪作为敏感数据加密、最小访问并按 TF-NFR-002 清理。
- 音乐/SFX 必须记录来源和商业使用范围；“AI 生成”不等于无权利限制。
- 同意撤回后新行为立即阻断，历史访问可因法律/安全处置受限但隔离审计证据保留。

## 12. 观测与运营

- 事件：voice_bound/consent_verified/revoked、audio_dispatch_requested/submitted/unknown/reconciled/completed/failed、audio_result_published、subtitle_aligned/edited、track_selected/replaced。
- 指标：TTS 成功率、P95 时延、ASR WER、专名错误、同步绝对误差、响度越界、重生率、unknown 对账时长、outbox 积压、fencing 拒绝和撤回生效时延。
- V1 Core 预览混音目标为 -16 LUFS ±2 LU、true peak <= -1 dBTP；其他交付目标由导出 preset 明示。
- 质量看板引用 TF-QLT-001 数据集/rubric/provider/model/voice revision；支持链保留 run、consent、cost 和 correlation_id。

## 13. 验收标准

- AC-1：Given 三角色固定 VoiceRevision，When 生成对白，Then speaker/文本/音频/timing/成本/来源可逐句追踪且角色不串轨。
- AC-2：Given 60 秒字幕样本，When 自动对齐，Then cue 单调、无非法负时长，时间误差达到 TF-QLT-001 批准阈值。
- AC-3：Given 基础混音，When 输出预览，Then integrated loudness 在 -18 至 -14 LUFS 且 true peak <= -1 dBTP。
- AC-4：Given 真人 consent 过期/撤回，When 新生成、重跑或导出，Then 服务端立即阻断并创建清理/撤销任务。
- AC-5：Given 第 8 句 provider 明确失败并服务重启，When 以新 attempt 显式补齐，Then 其余句 Artifact 不变、失败句完成重新快照/编译/授权/估算且无重复计费。
- AC-6：Given TF-QLT-001 音频固定集，When 回归，Then WER、发音、自然度、同步和音乐/SFX 适配达到批准阈值。
- AC-7：Given 一个真实 TTS 请求返回三条候选，When 当前 epoch 发布，Then 一个 ProviderInvocationAttempt、最多一条 InvocationRecord、三个 Audio Artifact/OutputBinding、一笔实际成本和一个同事务 result_publish OutboxEvent；事务注入失败时全部不提交。
- AC-8：Given provider 已接收请求但响应丢失，When 服务重启和重复回调后对账，Then unknown 收敛且没有盲目重提、重复 Artifact/OutputBinding 或重复计费。
- AC-9：Given 主 provider 已明确失败且 policy 允许 fallback，When 发起新请求，Then 能力快照、编译报告、授权/同意决策和成本估算全部重新生成，并创建新的 Attempt/dispatch OutboxEvent。

## 14. 测试场景

- 正常：多角色 TTS、旁白、音乐/SFX、字幕对齐、局部重生、轨道选择、替换和基础混音。
- 边界：零对白、重叠对白、极短 cue、长专名、多语种切换、静音、mono/stereo 和 51 镜头轨道。
- 失败：provider 限流、对齐失败、音频损坏、响度异常、部分台词失败、撤销 provider voice 失败。
- 权限：跨 owner VoiceRevision、无 consent、未成年人、名人冒充、音乐无商业权、非 owner 生成/校对/选择/导出和未授权播放。
- 并发/恢复：同句双重生、重复回调、撤回与运行竞态、取消晚到、刷新和服务重启。

## 15. 交付与回退

- TTS、音乐、SFX、字幕、自定义声音和混音独立功能开关；关闭自定义声音立即阻断新调用但保留合规历史证据。
- V1 Core track/subtitle schema 向 V1.5 兼容；新增 consent/mix 字段可选，未知自定义声音只读。
- 发布证据包括真实音频 provider、TF-QLT-001、响度/同步、同意/撤回、权限和恢复 E2E。
- provider voice 功能回退时项目可换托管音色形成新修订，不自动迁移或伪装原声音。

## 16. 已决策事项与开放问题

- 已决策：对白、旁白、音乐、SFX 和字幕保持独立轨道；真人声音克隆必须有同意证据。
- 已决策：V1 Core 先交付托管音色，V1.5 扩展合规自定义声音与混音，不建设完整 DAW。
- 已决策：V1 私有项目采用 owner-only；provider 调用统一遵守 AttemptStatus、dispatch/result outbox、fencing、unknown 对账和多 OutputBinding 合同。
- 开放问题：音频 provider spike 与 TF-QLT-001 评审后冻结各语种 WER/同步阈值和敏感数据保留期。
