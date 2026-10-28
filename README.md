# 守桥脚本工作室 · Script Studio

**把游戏素材、竞对口播和真实玩法，整理成可生成、可精修、可追溯的短视频广告脚本。**

这是一个面向游戏买量编导与文案人员的本地网页工作台。选择武器、玩法、关卡和参考文案，让 AI 生成口播全文与文字分镜，再逐句修改、保存草稿，审核后写回飞书多维表格。项目目前围绕《家园防御战》配置，也保留了替换模型服务与素材库的入口。

`Python / FastAPI` · `原生 JavaScript` · `飞书 Base` · `OpenAI 兼容接口` · `GEPA 提示词优化`

## 一次完整的工作流程

**选素材 → 生成口播与分镜 → 人工精修 → 保存草稿 → 预览并写入飞书**

写出来的稿子不满意时，可以先留下具体评价，再用「提示词优化」冻结测试样例、比较候选规则，经过独立来源验收后，手动采用或回滚。生成内容与人工修改稿分别保留，便于回看改动。

## 能做什么

| 工作环节 | 工作室提供的能力 |
| --- | --- |
| 素材选择 | 浏览武器、强化、竞对、分镜、情绪组件、福利、玩法和关卡；支持限定范围、低使用次数优先与纯随机抽样。玩法与关卡需要明确勾选。 |
| 脚本创作 | 多篇竞对融合、单篇定向仿写、竞对骨架原创、本体事实原创四种模式；输出口播全文、分镜台词、画面与游戏动作描述。 |
| 批量生成 | 按素材范围生成多篇脚本，查看进度、失败原因，保存与恢复本地任务。 |
| 沉浸式精修 | 对照 AI 原稿编辑全文及逐镜台词；支持整篇改写、单镜候选、人工采用和草稿管理。 |
| 素材补库 | 读取 XLSX / CSV 或粘贴文案，预览结构、组件与文字分镜；审核后分步或批量写入飞书。 |
| 提示词管理 | 编辑规则、预览实际提示词、切换版本；用 GEPA 搜索候选，保留诊断、独立验收与回滚记录。 |
| 飞书回写 | 写入前预览和核验来源、字段及关联，写后读回；人工稿与 AI 原稿分开保存。 |

本项目生成的是**文案和文字分镜**；视频拍摄、游戏录屏、配音与成片剪辑需要后续制作。编辑评分用于辅助判断，不代表已经验证投放效果。

## 快速启动

建议使用 Python 3.10+ 与 Chrome / Edge。Windows 是当前主要使用环境。

```powershell
git clone https://github.com/AsabaRikka/script-studio.git
cd script-studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r .\script_studio_v1.0\requirements.txt
Copy-Item .\script_studio_v1.0\config.example.json .\script_studio_v1.0\config.json
cd script_studio_v1.0
python launch_studio.py
```

打开 **[http://127.0.0.1:8501/](http://127.0.0.1:8501/)**。已有同项目服务时，启动器会复用它。首次创建 `config.json` 即可，已有配置不要重复覆盖。

Linux / macOS 可在安装依赖、复制配置后，从应用目录执行 `python server.py`；这两个平台的完整飞书流程尚未验证。

### 首次使用需要准备什么

- **模型服务**：在右上角「模型配置」填写 OpenAI 兼容 API 地址、密钥和模型名称。实际生成、改写与优化需要可用模型服务，可能产生模型费用。
- **业务素材**：仓库只保存代码与文档，不含现有素材库、草稿、任务结果和业务缓存。新克隆项目不会自带演示素材，也不会自动复制原电脑内容；需要另行准备 `data_cache/` 或接入自己的飞书表。
- **飞书集成**：配置 `config.json` 的 `feishu.base_token` 和 `feishu.table_ids`，安装并登录有权限的 `lark-cli`，准备符合现有字段契约的表。当前 `server.py` 的 `LARK_CMD` 仍使用原开发电脑的绝对路径，换电脑时需要调整；内置表 ID 也需换成自己的配置。

未接入飞书时，可以先配置模型和本地素材使用创作流程。**暂不要使用旧「同步飞书」全量同步入口**；其失败保护仍是待办。素材补库的写后读回与增量更新是独立流程。

## 提示词优化如何工作

**写明问题 → 预览冻结样例 → GEPA 搜索 → 比较新旧文案与编辑诊断 → 独立来源验收 → 手动采用 / 回滚**

GEPA 0.1.4 直接参与候选搜索；训练、验证与独立验收按来源隔离。预览不调用模型，开始运行后才计调用，并受总请求预算限制。Promptfoo 用于已执行结果的离线回放，不是再做一次独立模型评估。详见[提示词优化指南](script_studio_v1.0/docs/prompt-optimizer-usage.md)。

候选不会自动覆盖正式提示词。风格指标改善也不能代替游戏事实核对；存在事实疑点或独立验收退步时，应继续人工判断。

## 项目结构

```text
.
├── README.md                     # 项目介绍与首次运行
├── AGENTS.md                     # 工作区接手入口
└── script_studio_v1.0/
    ├── server.py                 # FastAPI 服务与创作接口
    ├── launch_studio.py          # 本地启动与安全重启
    ├── prompt_builder.py         # 动态提示词组装
    ├── prompt_defaults.json      # 默认规则及版本
    ├── prompt_optimizer*.py      # GEPA 优化与诊断接口
    ├── material_intake.py        # 素材解析与补库
    ├── feishu_writeback.py       # 飞书写入、关联及读回
    ├── static/                   # 创作台与精修界面
    ├── tests/                    # 自动化测试
    ├── docs/                     # 使用指南、设计及调研
    ├── config.example.json       # 不含真实密钥的配置模板
    └── HANDOFF.md                # 当前状态、验证与待办
```

`config.json`、`prompt_overrides.json`、`data_cache/` 和 `output/` 都是本地私有状态，不随仓库上传。源码备份不能代替这些业务数据的单独备份。

## 开发与验证

在 `script_studio_v1.0` 目录执行：

```powershell
python -m py_compile server.py prompt_builder.py prompt_optimizer.py
python -m unittest discover -s tests -q
```

自动测试与离线冒烟不能代替真实模型生成、飞书权限及写后读回验证。当前版本的验证结果与未完成项见 [HANDOFF.md](script_studio_v1.0/HANDOFF.md)。

后端修改不会自动加载。先保存网页、等待生成与写入任务结束，再运行 `python launch_studio.py --check-restart --no-browser`；确认空闲后使用已有安全重启入口。

## 文档导航

- [运行与日常操作](script_studio_v1.0/README.md)
- [提示词优化使用指南](script_studio_v1.0/docs/prompt-optimizer-usage.md)
- [提示词优化设计与边界](script_studio_v1.0/docs/prompt-optimizer-rebuild.md)
- [当前交接与已知问题](script_studio_v1.0/HANDOFF.md)
- [开发约定](script_studio_v1.0/AGENTS.md)

这是私有项目仓库，未声明开源许可证。GitHub 首次发布使用经检查的当前源码快照；原电脑的既有开发历史保留在本地。
