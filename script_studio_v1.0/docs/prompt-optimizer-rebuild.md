# 提示词优化重做（2026-10-26）

用户否定上一版混合实验室，明确撤下一源多创，重心改为可操作、可验证的提示词持续优化。本文覆盖原MVP对应产品范围，不再按原四页签继续扩展。

主流程只保留：选择写稿用途与问题 → 冻结样例 → 一次启动有预算的GEPA搜索 → 独立来源验收 → 展示具体改动、新旧文案、退步和淘汰理由 → 用户采用或保持原版。评价与人工修订在成稿工具栏直接展示。

实际依赖为PyPI `gepa==0.1.4`，使用 `gepa.optimize` 和自定义GEPAAdapter；GEPA负责反思循环、候选接纳、验证集选择与Pareto保留。工作室负责现有生成器、事实/格式保护、正文证据评审、模型调用总预算和停止。不是将原有单候选生成函数改名GEPA。

样例先按共享竞对来源连通分组，再划分优化/验证/独立验收。独立验收正文、反馈和结果不进入GEPA或反思模型，最后仅比较种子与选出的候选。历史人工稿不自动标为标准答案；只使用明确审稿和本次问题，不伪造偏好。分用途优化对应mode块，保留其他模板及输出契约；实际渲染无变化的候选直接淘汰。

将运行输出、规则和质量评语导出为Promptfoo离线回放配置/自定义provider；回放不重新付费生成，不把“JSON可回放”冒充独立质量评估。工程核验须真实调用Promptfoo CLI并记录版本与报告。界面分清实际接入、只借鉴方法和暂未接入的项目。

保留原稿/人工稿分离、来源范围、普通四种写稿模式、旧批次和历史数据。撤下原一源多创入口；旧数据保留，新规划/生成入口停止接单。新实现仍在 `codex/prompt-quality-mvp`，正式服务不另起；加载需按现有保存/空闲流程。

官方接口依据：[GEPAAdapter](https://github.com/gepa-ai/gepa/blob/main/src/gepa/core/adapter.py)、[GEPA Quick Start](https://gepa-ai.github.io/gepa/guides/quickstart/)、[Promptfoo配置](https://www.promptfoo.dev/docs/configuration/guide/)、[自定义provider](https://www.promptfoo.dev/docs/providers/custom-api/)。固定依赖的本机接口优先于默认分支可能变化的示例。
