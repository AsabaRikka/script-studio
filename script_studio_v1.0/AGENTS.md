# Agent 接手入口

先读本目录的 [`HANDOFF.md`](./HANDOFF.md)，再修改代码。它是本项目当前状态、运行方式、数据契约、已知问题和验证证据的唯一交接入口。

## 新增功能工作分支

用户指定“提示词持续优化与竞对一源多创”新增功能的后续更新统一放在 `codex/prompt-quality-mvp` 分支。

- 开始该功能的开发、测试或文档更新前，先核对当前分支；相关提交均留在该分支，不直接提交到 `master`。
- 分支从 `6193852` 创建，已包含调研与历史MVP。用户后续明确撤下一源多创、重做提示词优化；当前范围以 `docs/prompt-optimizer-rebuild.md`、`docs/prompt-optimizer-usage.md` 和HANDOFF顶部为准，不能继续按旧混合实验室扩展。
- 创建分支时的既有未提交改动已保留；提交前区分本轮改动与此前改动，不把无关内容一并提交。
- 合并、发布或推送按用户指令处理。2026-10-28 用户授权上传 GitHub 私有库，远程为 `https://github.com/AsabaRikka/script-studio.git`。
- **发布历史边界**：本地旧历史 README 含 Base Token，禁止推送 `master`、`codex/prompt-quality-mvp` 或使用 `--all` / `--mirror`。GitHub 仅使用脱离旧历史的 `codex/github-publish` 当前快照分支。后续发布应把经检查的开发分支 tree 通过 `git commit-tree` 作为发布分支的新提交（父提交仅为上一发布提交），再推送该分支；不要把本地旧历史合入发布分支。

## 快速检查

```powershell
cd 'E:\codex project\project 27\script_studio_v1.0'
python -m py_compile server.py
python -c "import server; print(server.get_overview()['counts'])"
python server.py
```

浏览器打开 `http://127.0.0.1:8501/`。如 8501 已被占用，可使用临时端口运行 `python -m uvicorn server:app --host 127.0.0.1 --port 8517`，但注意 `config.json` 的 server.port 目前不会驱动 `__main__` 启动端口。

## 修改原则

- 先看 `server.py` 的路由和 `static/index.html` 的对应 `fetch`，保持请求/响应字段兼容。
- `data_cache/*.json` 是本地快照；`.manifest.json` 记录来源和字段统计，更新缓存后要同步检查两者是否一致。
- 不把 API Key、Base Token 或本地写入结果提交到公共仓库。当前 `config.json` 含真实配置，修改时先确认使用场景。
- 涉及飞书写入前先做身份、权限、表 ID、字段名和代表记录的只读确认，再写入并读回核验。
- 改动后至少执行 Python 语法检查、后端缓存冒烟；涉及网页时使用真实浏览器打开并检查控制台和关键流程。
- 每次交付更新 `HANDOFF.md` 的当前状态与交接日志，记录已完成、验证证据、仍未验证和下一步。不要把“建议修复”写成已完成。
