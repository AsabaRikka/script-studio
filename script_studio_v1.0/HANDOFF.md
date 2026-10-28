# Script Studio v1.0 当前交接

更新时间：2026-10-28（Asia/Shanghai）

## 当前状态（新对话只需先读这里）

- 项目：`E:\codex project\project 27\script_studio_v1.0`
- 正式入口：`http://127.0.0.1:8501/`
- 当前分支：`codex/prompt-quality-mvp`
- 最近功能提交：`9b0003a fix: feed editorial diagnostics into prompt optimization`；既有两阶段融合改动已单独检查点提交 `4f68609`。最新文档与发布提交以 `git log` 为准。
- GitHub 私有库：`https://github.com/AsabaRikka/script-studio`；只发布 `codex/github-publish` 的无旧历史快照，开发仍在 `codex/prompt-quality-mvp`。
- 正式服务：8501 当前已有项目进程 PID67504（2026-10-28 10:23启动）；9b0003a 尚未加载，禁止另起第二个8501或直接重启。
- 当前产品范围：只维护「提示词优化」；旧“一源多创/创意规划”入口已撤下，旧 POST 返回410。主流程是“写问题 → 预览冻结样例 → GEPA搜索 → 查看诊断/新旧文案 → 独立来源验收 → 手动采用或回滚”。

## 本轮已完成

- **多篇竞对-多向融合抽样与两阶段仿写架构重构**：
  1. **分镜库跨竞对组件抽选**：`build_slot` 在 `多篇竞对-多向融合` 模式下不再抽取整篇竞对库（`rival_struct`），而是直接从分镜库（`rival_shots`）按组件类型逻辑时序抽选跨竞对分镜序列（`开头钩子` → `痛点/冲突` → `过程/机制` → `爽点/高潮` → `行动号召/CTA`），优先选择来自不同竞对视频的分镜，形成结构符合爆款短视频逻辑的分镜装配链。
  2. **两阶段仿写与生成**：
     - **阶段一（逐镜仿写与第一稿拼接）**：对仗仿写模式下分段 1:1 模仿每镜竞对原句的句式结构、语气、口头禅与标点停顿，仅替换我方游戏事实；意译仿写模式下脱离具体字面句式，按该镜情绪与语境意译转述；各镜仿写句拼接成第一稿 `stage1_stitched_script` 与 `stage1_shots`。
     - **阶段二（AI 整体优化成稿）**：AI 以第一稿拼接文案为基础进行全局逻辑打通与语句润色，消除跨视频分镜拼接的生硬感，形成自然连贯、以句号为主的最终成稿口播全文 `full_script` 与分镜 `shots`。
  3. **前端双层呈现**：在结果展示区首先展示**「第一稿 · 竞对逐镜一比一仿写拼接原文」**（支持展开逐镜 1:1 竞对原句与仿写对照表），紧接着展示**「第二稿 · AI 整体优化成稿口播全文」**及最终分镜。
- `prompt_builder.py`：支持两阶段分镜蓝本注入与对仗/意译仿写引导；`prompt_defaults.json` 激活 `v6_two_stage_fusion`。
- `prompt_optimizer.py`：增加重点问题原稿、参考改写、感叹号/强程度词/转折/信息堆叠/武器说明重复诊断；GEPA同时记录编辑质量分和事实安全门；模型无有效生成/评审时返回 `model_unavailable`。
- `prompt_optimizer_api.py`：新增 `POST /api/quality/optimizer/diagnose`。
- `static/prompt-optimizer.js`：增加重点样例、问题原稿、参考改写输入，并显示诊断。
- `docs/prompt-optimizer-usage.md`、`tests/test_prompt_optimizer.py` 已同步更新。
- 证据目录：`output/prompt-optimizer-rebuild-20261026/focus-gepa-20261028/`；本轮没有写正式草稿、提示词或飞书。

用户华容道样例诊断：原稿5个感叹号、9个强程度词、0个转折；参考改写0个感叹号、0个强程度词、10个转折。首次隔离真实GEPA调用9次，候选口吻指标改善但有事实疑点，未采用；第二次生成规则失败，已标记模型结果无效。不能把这两次称为正式提示词质量提升。

## 验证与运行约束

- `python -m unittest discover -s tests -q`：136项通过。
- `PYTHONPATH=tests python -m unittest tests.test_prompt_optimizer -q`：9项通过。
- `python -m py_compile prompt_optimizer.py prompt_optimizer_api.py`、`node --check static/prompt-optimizer.js`：通过。
- 8501只读核对：旧后端已有GEPA路由，但新 `/api/quality/optimizer/diagnose` 当前返回404，证明本轮代码尚未加载。
- 加载前必须确认页面已保存、生成/改写/补库/飞书写入均空闲；随后只用既有安全入口 `python launch_studio.py --check-restart --no-browser`，确认后才可 `--restart --no-browser`。不得手动杀进程、不得另起同端口服务。
- 预览不调用模型，点击开始才计调用；候选不会自动改正式提示词，必须手动采用。

## 关键入口与数据契约

- 后端：`server.py`；优化器：`prompt_optimizer.py`、`prompt_optimizer_api.py`；页面：`static/prompt-optimizer.js`。
- 质量数据：`data_cache/quality/{datasets,jobs,bundles,traces,feedback,state}`。dataset冻结样例/提示词/模型连接；train、validation、holdout按来源组隔离；holdout不得进入GEPA反思。
- 保护边界：不提交API Key、Base Token或本地写回结果；飞书写入必须先身份/权限/schema/代表记录只读确认，写后读回；不要借提示词优化顺便修改旧稿或同步。
- 本轮 GitHub 备份前，原有5个代码/测试修改已单独提交 `4f68609`，未修改其实现；原有 HANDOFF 两阶段说明保留并纳入交接文档提交。运行中的8501未重启。

## 当前未完成

- 用户确认页面保存且任务空闲后，加载9b0003a到正式8501，再只读核对页面和新诊断接口。
- 真实模型仍可能因生成硬校验或事实疑点失败；先修输入/事实，不自动重复付费尝试。
- 长期质量仍需积累用户人工“采用/待修改/弃用”反馈；GEPA分数是编辑代理指标，不等于投放效果。
- 旧独立待办（关卡/manifest差异、人工稿真实飞书写后读回、旧同步失败保护、开始菜单）保留，除非用户另行指定。

## 最近交接日志

### 2026-10-28 · GitHub 私有库与首页说明
用户授权创建私有库并上传。新建根目录 `README.md`，首屏说明用途、受众与素材到成稿流程，补充功能表、安装、配置、目录、文档入口与已知限制；重写应用目录README，移除含真实Base Token的旧配置示例；扩展 `.gitignore` 排除虚拟环境和环境文件，更新 AGENTS 的远程与发布边界。

验证：Python语法检查和 `get_overview()` 本地缓存冒烟通过；当前136项测试135通过，1项 `test_multirival_draw_does_not_fill_primary_shot_gaps_from_other_videos` 失败，旧测试要求的提示词字符串与既有跨视频分镜抽样改动不符，本轮未修改实现或测试，不得继续声称当前全部通过。日志位于本地 `output/github-publish-tests.log`，未上传。未调用真实生成模型、未写飞书、未重启服务。

历史检查发现真实 Base Token 曾进入旧 README，因此保留本地历史，但不上传旧历史；仅发布清洁当前tree。真实 `config.json`、`prompt_overrides.json`、`data_cache/`、`output/` 不上传。下一步若继续功能开发，应处理上述测试与新抽样约定的冲突；后续发布禁止推送旧开发分支历史，按AGENTS发布快照规则操作。

远端验证：已创建私有库，设置默认分支 `codex/github-publish`，首次干净根提交为 `000c808`（无父提交）；76个源码/文档文件检查未含当前模型密钥和Base Token，私有配置与业务目录未被跟踪。GitHub API读回README与本地一致，README本地链接检查通过；GitHub插件再次读回确认 `visibility=private` 及当前账号有push权限。`origin` 已配置，默认push refspec仅指向发布分支，开发分支未切换。此段验证记录将作为发布分支的后续文档提交同步。

### 2026-10-28 · 诊断驱动优化
修改提示词优化后端、页面、测试和使用说明；136项回归通过；真实隔离GEPA结果与正式服务未加载边界见上文。下一步是安全加载并做正式只读核验。

### 2026-10-26 · GEPA重做版加载
已撤下旧混合实验室和一源多创，接入GEPA 0.1.4与Promptfoo离线回放；正式8501曾在用户确认保存且空闲后安全加载旧版重做代码。详细证据和历史状态见 `HANDOFF_HISTORY.md`。

完整旧日志、旧工时、旧PID和旧MVP方案已迁移到 [`HANDOFF_HISTORY.md`](./HANDOFF_HISTORY.md)，除非追查历史，不要在新对话中读取。

