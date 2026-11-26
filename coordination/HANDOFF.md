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

## 2026-11-26T09:49:00+08:00 协作状态同步与 Phase-3 任务锁登记（epoch: 3）

- 时间、交出人、接收人、原因：2026-11-26T09:49:00+08:00，主负责人 `gemini-deputy` 同步集中式协作状态至 `epoch: 3`，副负责人 `codex-lead`，独立实现成员 `opencode-worker`，按人类负责人最新明确顺序推进：① Oracle/GitLab 备份 -> ② 隔离还原演练 -> ③ 最后统一跑 MD5。
- 仓库提交与分支：`origin/main` 位于 `43cdcfd`；本同步分支 `ai/gemini/coord-sync-20261126` 仅更新 `coordination/`。
- 已完成及证据：`UI-01`、`FIX-NEXUS-01`、`FIX-SVN-01` (PR #13 / `83de48f`)、`FIX-NEXUS-02` (PR #17 / `51c3be4`)、`FIX-KINGDEE-02/03` (PR #18/#19 / `43cdcfd`) 均已通过独立复审合入 `main` 并完成生产上线；金蝶 12 账套于 2026-11-26 05:19 首轮实备成功并在看板转绿；`ops253` 14/14 连接检查全绿 `ok`；每日 `08:50` 企业微信汇报正常触发且无刷屏。
- 正在运行的任务、主机、进程或 systemd 单元：
  - `ops253`：`oracle46`（`win254` 收尾阶段）、`oracle41`（`nas240`/`win254` 同步中）、`GitLab 52`（`10.5.8` `--network none` 隔离容器内正在执行 `gitlab-rake gitlab:backup:restore`）；`10.5.4-ce.0` 与 `10.0.3-ce.0` 镜像已拉取就绪，后续顺次执行 `247/191/16/14` 隔离恢复；后台 MD5 巡检已按用户顺序暂停以让出 NAS 带宽。
  - `192.168.0.29`：`CoreBackup-SVN29` 无人值守 `SYSTEM` 任务按计划执行中（12 仓库全量 hotcopy）。
- 不可重复执行的操作：不得在 `oracle46`/`oracle41`/`svn29` 运行期间重复触发同类全量任务；不得在备份与还原完成前重启高并发 MD5 扫描；不得直连生产 `10.10.10.46`/`10.10.10.41` 执行恢复写入。
- 未提交改动及所属人：各进行中任务严格按 `coordination/tasks.json` 隔离目录与分支：`UI-03-DATA-01` (`apps/board/`, `gemini-deputy`)、`SYNC-3COPY-01` (`platform/sync/`, `tests/platform/`, `opencode-worker`)、`RESTORE-ORACLE-01` (`components/oracle/restore*`, `platform/restore/`, `tests/oracle/`, `codex-lead`)，无路径重叠。
- 生产授权范围、原值和回滚条件：本分支仅更新 `coordination/` 元数据（`production_change: false`），回滚方式为丢弃分支或 `git revert`。
- 阻塞和待确认事项：1) `10.10.10.251` 需管理员启用 WinRM 并确认 Oracle `11.2.0.4` Win64 安装介质来源；2) `SYNC-3COPY-01` r1 复审 `CHANGES_REQUESTED` 待 OpenCode 交付 r2；3) PR #15 (`UI-03-DATA-01`) 待按 Codex 复审意见修订并二次复审。
- 下一项任务及验收标准：优先完成 `GitLab 52/247/191/16/14` 顺次隔离恢复、审核合入 `SYNC-3COPY-01` r2 并补齐 `Oracle46 -> nas240`、审核并落地 `RESTORE-ORACLE-01` 到 `10.10.10.251`，最后统一完成 GitLab 3 端 MD5 复核。
- 接收人复核与确认时间：`gemini-deputy` 于 2026-11-26T09:49:00+08:00 记录并提交审核。
