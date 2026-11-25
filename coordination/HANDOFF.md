# 负责人交接记录

每次接管追加一节，不覆盖上一位负责人的结论。日期使用含时区的 ISO 8601。

## 交接模板

- 时间、交出人、接收人、原因：
- 仓库提交与分支：
- 已完成及证据：
- 正在运行的任务、主机、进程或 systemd 单元：
- 不可重复执行的操作：
- 未提交改动及所属人：
- 生产授权范围、原值和回滚条件：
- 阻塞和待确认事项：
- 下一项任务及验收标准：
- 接收人复核与确认时间：

首次项目上下文见 docs/HANDOFF.md。此文件不保存凭证。

## 2026-11-25T18:55:00+08:00 副负责人接管（codex-lead -> gemini-deputy）

- 时间、交出人、接收人、原因：2026-11-25T18:55:00+08:00，由 `codex-lead` 交接给 `gemini-deputy`，原因 `explicit_handoff`（人类负责人告知 Codex 休息，指令 Gemini 接管并带队推进首项任务）。
- 仓库提交与分支：远端 `origin/main` 位于 `5bcb5b5`（`Update ai-team.example.json`）；协作规则同步分支 `ai/gemini/collab-rules` 已推送至远端（`e60ab84`）；本次工作分支 `ai/gemini/UI-01` 基于 `origin/main` 建立。
- 已完成及证据：已只读核查 Codex 工作区工作树干净、无未提交改动；已向 GitHub 推送手工模式 4 条硬规则分支 `ai/gemini/collab-rules`（`e60ab84`）。
- 正在运行的任务、主机、进程或 systemd 单元：本地未连接生产服务器，生产主机实时运行任务未知；本阶段不访问、不重启、不修改任何生产备份任务。
- 不可重复执行的操作：不得在未核对生产实时状态前重跑任何长耗时备份或清理任务；不得覆盖 Codex 本地工作区。
- 未提交改动及所属人：Codex 本地工作区无未提交改动，但本地 `main` 领先 `origin/main` 1 个未推送提交 `dad1c0c`（`Keep machine paths and session identities in private local configuration`，涉及 `.gitignore`、`config/ai-team.example.json`、`docs/AI协作说明.md`），与 `UI-01` 路径不冲突。
- 生产授权范围、原值和回滚条件：本任务 `production_change: false`，无生产变更授权；代码变更完全隔离在 `ai/gemini/UI-01` 分支，回滚方式为丢弃分支或 `git revert`。
- 阻塞和待确认事项：Codex 休息期间需由 `opencode-worker` 或人类负责人担任 `UI-01` 独立审核人，作者不自审合并；Codex 的 `dad1c0c` 待其恢复或人类负责人确认后推送。
- 下一项任务及验收标准：`UI-01`（重构 `apps/board/static/` 展示页样式并标明每项数据来源与统计口径，暂不做历史数据库与日志查询接口），详见 `coordination/tasks.json`。
- 接收人复核与确认时间：`gemini-deputy` 于 2026-11-25T18:55:00+08:00 复核并递增 `state.json` 至 `epoch: 2`。

## 2026-11-25T20:45:00+08:00 主副负责人正式调整（Gemini 升任主负责人，Codex 改为副负责人）

- 时间、交出人、接收人、原因：2026-11-25T20:45:00+08:00，经人类负责人明确决策，由 `gemini-deputy`（Gemini）正式担任主负责人统筹协调，`codex-lead`（Codex / GPT）调整为副负责人，`opencode-worker`（OpenCode）继续担任独立实现与测试审核成员。
- 仓库提交与分支：`PR #1`（4 条硬规则，`6c25d4b`）与 `PR #2`（`UI-01`，`1f2e40f`）已合入 `main`；本轮角色调整与第二阶段任务分配提交在分支 `ai/gemini/lead-switch-phase2`。
- 已完成及证据：`UI-01`（`c079e35`）经 OpenCode 独立审核与人类负责人验收，已旁路灰度上线至 `ops253:8100`（容器 `core-backup-board-ui01-20261125`，旧容器 `core-backup-board-acceptance-20261125` 保留秒回滚，账号库已备份至 `/var/lib/core-backup-ops/audit-20261125/board-before-ui01-20261125.dump`），生产浏览器 1920×1080 实测 0 报错/0 CSP 违规。
- 正在运行的任务、主机、进程或 systemd 单元：`ops253` 上 `core-backup-board-feed.timer`、`backup-board-access.service` 均为 `active`；Oracle46 每日定时任务 `CoreOracle46-Daily` 已启用，计划于 `2026-11-26T00:00:00+08:00` 首跑。
- 不可重复执行的操作：不得在未核对生产实时状态前重跑任何长耗时备份或清理任务；不得盲目覆盖 `/opt/backup-board/` 或重跑旧 `install.sh`；不得提交本机私有路径或凭证到公开仓库。
- 未提交改动及所属人：Codex 工作区干净，但本地 `main` 曾存在未推送提交 `dad1c0c`（私有配置占位脱敏），本分支已将公开模板 `config/ai-team.example.json` 同步脱敏为 `LOCAL_ONLY`，Codex 恢复后请先 `git pull --rebase origin main`。
- 生产授权范围、原值和回滚条件：本阶段任务 `FIX-KINGDEE-01` 与 `OPS-AUDIT-01` 均为 `production_change: false`（仅仓库代码、本地测试与只读排查），任何生产改动须另获人类负责人确认。
- 阻塞和待确认事项：Codex 休息中，恢复后按 `OPS-AUDIT-01` 接入；OpenCode 已分配 `FIX-KINGDEE-01`（锁定 `components/kingdee/`、`tests/`，与 Codex 路径完全隔离）。
- 下一项任务及验收标准：详见 `coordination/tasks.json` 中的 `FIX-KINGDEE-01`（OpenCode）与 `OPS-AUDIT-01`（Codex），`DATA-01` 继续暂缓。
- 接收人复核与确认时间：`gemini-deputy` 于 2026-11-25T20:45:00+08:00 复核并递增 `state.json` 至 `epoch: 3`。
