# 提示词持续优化项目调研

核查日期：2026-10-26，Asia/Shanghai。本文件记录调研与选型，不代表相关功能已经接入或效果已经验证。实施设计见 [提示词质量与竞对复用规划](../prompt-quality-and-source-reuse-plan.md)。

## 筛选方法与维护状态

优先考察高 Star 项目，再核对仓库是否归档、默认分支最近提交和官方文档。Star 衡量关注度，不能证明适合中文游戏买量文案。`updated_at` 可能只是有人点星，`pushed_at` 也可能来自其他分支，因此表中额外列出默认分支提交日期。最近提交包含文档和依赖更新，表示有维护活动，不等于近期核心算法升级。

以下 Star 为本次 GitHub REST API 读到的快照；提交日期按 API 的 UTC 日期记录。完整元数据、提交 SHA、作者和原始链接保存在 [核查数据](./prompt-optimization-projects-20261026.json)。

| 项目 | Star | 默认分支最近提交 | 可以借鉴的思路 | 对本项目的取舍 |
|---|---:|---|---|---|
| [Prompt Optimizer](https://github.com/linshenkx/prompt-optimizer) | 39,266 | 2026-10-25 | 分析问题、迭代提示词、实测结果、多结果比较、保存可复现的提示词资产 | 借鉴产品交互，把“优化前后结果”与反馈一起展示；不能仅凭提示词文字更完整就认定效果更好 |
| [DSPy](https://github.com/stanfordnlp/dspy) | 38,792 | 2026-10-25 | 把提示词优化变成对任务指标的搜索；MIPROv2联合搜索指令和示例组合，GEPA支持反思式优化 | 参考模块化与按数据优化；首期保留现有生成链，后续按需要加适配器 |
| [Langfuse](https://github.com/langfuse/langfuse) | 36,502 | 2026-10-25 | 将提示词版本、运行轨迹、反馈、数据集和实验连起来；版本与部署标签分离 | 直接借鉴数据模型：版本不可变，启用指针可切换；团队协作需求增长后再考虑平台部署 |
| [Promptfoo](https://github.com/promptfoo/promptfoo) | 26,446 | 2026-10-25 | 多提示词在相同测试集上比较，规则断言与模型评审结合，结果可导出 | 作为首选离线评测适配器；从现有应用导出冻结输入和实验配置，再把结果接回工作室 |
| [Opik](https://github.com/comet-ml/opik) | 22,928 | 2026-10-23 | 以数据集、指标和运行证据驱动实验；Agent Optimizer可替换优化算法并保留试验过程 | 借鉴优化运行账本与算法接口；现有本地工作台首期不新增整套平台运维 |
| [DeepEval](https://github.com/confident-ai/deepeval) | 19,108 | 2026-10-23 | 使用自定义评测维度、明确的评审步骤和分档标准，输出分数及原因 | 重点参考评分器设计；作为Python评测替代接入选项，首期避免并行维护两套评测引擎 |
| [GEPA](https://github.com/gepa-ai/gepa) | 7,148 | 2026-10-22 | 阅读失败轨迹与文字反馈，提出有针对性的候选修改，并保留在不同样本上有优势的候选 | 第二阶段自动优化的优先算法；通过自定义adapter或`optimize_anything`复用现有生成函数 |

上述七个仓库本次均未归档。另核查了 [Microsoft PromptWizard](https://github.com/microsoft/PromptWizard)：4,022 Star，未归档，但默认分支最新提交为2025-08-04；仓库`pushed_at`为2025-10-13。由于本次希望采用仍活跃的方案，不列为主选。这是本次维护状态判断，不声称该算法已经失效。

## 官方资料体现的方法

### 从失败原因提出小步修改

[GEPA Quick Start](https://gepa-ai.github.io/gepa/guides/quickstart/)与[候选选择策略](https://gepa-ai.github.io/gepa/guides/candidate-selection/)说明：评估函数可以提供文字反馈，优化器据此修改候选；可设置最大评测调用数、超时和无改进停止条件。Pareto候选选择可保留在不同评测样本上表现突出的版本。

对本项目的设计推论：记录“第二段在列装备而没有推进事件”“三个来源贡献实际一样”等具体缺陷，优先修改相关模板块；按任务与风格保留擅长的版本。GEPA本身不会自动理解广告风格或保证多样性，这些仍要写入本项目的评测目标。

[DSPy官方MIPROv2文档](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/api/optimizers/MIPROv2.md)把流程分为示例候选、指令候选、验证集搜索。可迁移的是“有标准、有样本、有搜索”，不能把数学问答上的公开收益视为本项目文案质量的收益。

### 相同素材比较结果，评价标准保持固定

[Promptfoo配置说明](https://www.promptfoo.dev/docs/configuration/guide/)以提示词、provider、测试样本和断言组成实验；[`select-best`](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/select-best/)可在同一测试样本的多个输出中按指定标准比较。

本项目应在各版本之间冻结竞对原稿、我方事实、时长、模型配置和创意任务。若改提示词时也换了武器或来源，就无法归因。`select-best`选出赢家仍不足以判断绝对质量；所有版本都差时必须允许“均不合格”。

[DeepEval G-Eval文档](https://deepeval.com/docs/metrics-llm-evals)支持显式评审步骤、分档rubric及原因记录，并指出固定评审步骤有助于控制评测波动。对本项目应把口语感、事件推进、来源迁移和事实一致性拆开评价，避免一个模糊“高级感”分数统领全部。

### 将版本、启用状态、反馈和实验拆开

[Langfuse版本控制](https://langfuse.com/docs/prompt-management/features/prompt-version-control)将版本ID和标签分开；[UI实验](https://langfuse.com/docs/evaluation/experiments/experiments-via-ui)支持在数据集上比较提示词与模型；[评测概览](https://langfuse.com/docs/evaluation/overview)把代码评测、模型评审与人的反馈都纳入。

本项目由此应增加不可变的提示词内容快照、按任务选择的生效指针和独立反馈事件。“淘汰”表示退出默认路由并保留回滚证据，不能删除旧版本后使历史稿件失去来历。

[Opik Agent Optimization](https://www.comet.com/docs/opik/development/optimization-runs/overview)先准备数据与指标，再选择优化器，最后检查结果并发布。它说明闭环依赖评测数据，而不是安装优化器后就能自动判断好文案。

[Prompt Optimizer测试与评估](https://docs.always200.com/en/user/testing-evaluation/)提供分析、结果评价和比较交互。适合借鉴“看得到具体改进”的体验；项目中仍需单独补齐固定测试集、版本门槛和失败样本积累。

### 让AI评审逐步贴近真实审稿偏好

[OpenAI评测最佳实践](https://developers.openai.com/api/docs/guides/evaluation-best-practices)建议持续纳入真实数据、用人的反馈校准自动评测，并考虑成对比较、顺序偏差和长度偏差。本次仅引用其方法，不依赖特定平台服务。

本项目以AI初评和推荐为默认，用户只需做“采用、弃用、原因修正、关键句修改”等正常审稿动作。人的反馈用于发现评分器偏差；没有投放对照数据时，评价结果只能称编辑质量代理指标，不能称点击率或转化率提升。

## 选型结论

在现有FastAPI和单页前端里增加提示词实验室、反馈记录和竞对创意覆盖。先完成版本溯源与一源多创，再引入固定样本评测；Promptfoo负责可选的离线矩阵比较适配，GEPA负责后续有预算上限的候选优化。Langfuse与Opik作为未来团队协作/观测平台的扩展目标，DeepEval作为Python评测替代选项。

本轮没有安装这些项目、上传脚本、执行付费评测或修改生效提示词。具体接入依赖版本、许可证和Python3.14兼容性应在接入阶段单独确认；GitHub元数据中的`NOASSERTION`不应被解读为已完成许可证核查。
