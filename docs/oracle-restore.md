# Oracle 隔离恢复核验（RESTORE-ORACLE-01）

本工具在 **10.10.10.251 专用 Win2008R2 测试机**执行恢复，只读 NAS243 的已经完成备份集。代码提交、计划生成、文件校验均不等于实际恢复成功。本任务 `production_change: false`，不连接生产 46/41、不修改生产参数或定时任务、不执行任何备份清理。

## 交付与边界

| 文件 | 运行位置 | 用途 |
| --- | --- | --- |
| `components/oracle/restore/restore_plan.py` | ops Python 3.8+ 或本地 Python | 校验配置与备份清单，生成静态 SQL/RMAN/参数；验收后发布标准结果 |
| `components/oracle/restore/invoke-restore.ps1` | 251，管理员 Windows PowerShell 2 | 目标机守卫、只读获取备份到本地、SHA256、原生恢复、表与对象核验、结果记录 |
| `components/oracle/restore/native-process.cs` | 251，.NET 2.0+ | 独立接管原生命令 stdout/stderr，避免旧 PowerShell 重定向句柄错位；保留退出码 |
| `components/oracle/json-compat.ps1` | 随 Oracle 组件保留相对目录 | PS2 JSON 编解码，零 System.Web.Extensions 依赖 |
| `tests/oracle/` | 纯本机 | 配置拒绝矩阵、计划与结果契约、原生子进程、真实 PS2 兼容测试 |

源固定为 `\\10.10.11.243\资源备份\<对应资产>\<日期>\<运行号>`，不接受 `.partial`、父级目录、通配符、另一资产或未完成清单。所有 SQLPlus 都是本机 OS 认证，RMAN DUPLICATE 仅 `auxiliary / nocatalog`，**无生产 TARGET/TNS/恢复目录连接，无 NOFILENAMECHECK**。后续 `target /` 仅指本机 RT41 验证。

这不是一键安装 Oracle 的工具。管理员必须先准备隔离 VM、匹配的 Oracle 二进制及专用测试实例/服务，确认测试 IP 和实际主机名。不得因为名称相似而在旧堡垒机或其他 251 主机执行。使用双条件 IP+hostname 和数据库 SID/版本/路径检查；IP 本身不是隔离证明。

## 1. 先准备测试机

1. 核对 251 确为本次专用 VM；备份源 NAS 账号只授予读取权限。管理员在本机受控会话建立 SMB 连接，密码交互输入，不进入配置、命令行或仓库。不要断开其他会话绕过 1219。
2. 网络侧只允许管理端和 NAS243 的必要流量；拒绝测试机访问生产数据库端口。工具不会为“连通”而修改防火墙、监听器或 TrustedHosts。数据库不对外提供测试访问端口，业务验收另行安排。
3. 备份版本和目标版本分开：46 的清单 `version=11.2.0.1.0`，逻辑导入可明确选择 Windows x64 `target_version=11.2.0.1.0` 或 `11.2.0.4.0`；41 的 RMAN 则保持源/目标均为 **11.2.0.4.0**。当前251已安装 `C:\app\oracle\product\11.2.0\dbhome_1\BIN`，配置的 `oracle_home` 填其上一级，不含 BIN，两份新模板均明确选择目标11.2.0.4.0。逻辑向较新版本导入遵循 [Oracle 11g Data Pump 版本规则](https://docs.oracle.com/cd/E24693_01/server.11203/e22490/dp_overview.htm)，仍需 SQLFILE 审查和真实导入确认；不把“允许生成计划”当作兼容性验收通过。Oracle41 的 RMAN 备份不能用 Linux Oracle 容器代替恢复。
4. 默认独立运行根目录 `C:\core-backup\restore\oracle46_<演练号>` 或 `oracle41_<演练号>`，兼容旧 `D:\oracle-restore\<演练号>`。必须使用这些基础目录下的**独立子目录**，基础目录本身不能作为本轮根目录。不使用生产数据目录、网络盘、junction。251只有 C 盘约485GB，D盘是光驱；不要再选择D盘。空间至少覆盖本地备份暂存 + 恢复数据 + 临时/归档/redo 和余量，逐个演练并先核算实际空闲空间，不能只按压缩包大小或磁盘总容量估算。工具不会删除其他演练来腾空间。
5. 46：用 DBCA 在专用根目录内创建空测试库 **RT46**，所有 datafile/tempfile/controlfile/redo 都位于该根目录，`job_queue_processes=0`。先创建 `REST_*` 目标表空间，数据文件也在根目录内。不要预创建本次映射的 `REST_*` 用户，避免混入旧数据。
6. 41：用匹配 OracleHome 的 ORADIM 预创建专用 **OracleServiceRT41** 服务，但数据库应为未启动的辅助实例，`data` 目录为空；工具用生成 pfile 执行 NOMOUNT、备份式 DUPLICATE、RESETLOGS 开库。不能替换已有 RT41 演练。生成 OMF 数据文件和独立 redo/controlfile，不复制生产 spfile。

**不自动停止、删除或重建已有实例。** 若失败，保留目录、数据库和日志，由 DBA 判断如何处理；不要反复重跑同一目录。每轮用新目录，先核对原生作业是否仍运行，不能仅凭客户端退出判断服务器作业已结束。

## 2. 备份清单与配置

复制 `oracle46.example.json` / `oracle41.example.json` 为私有配置，填写真实测试机名、OracleHome、完成运行目录、演练根目录与关键业务表。不得把生产凭证写入。示例中的主机名、运行号、schema、表空间、表名均必须替换；示例清单是 `incomplete`，不能直接执行。

复制 `manifest.example.json`，由备份负责人从**该次完成回执及原生日志**核对生成：

- `id/source/run/version` 必须与配置绑定，`status=complete`、真实 `native_exit_code=0`；不得手工把失败导出改为成功。
- `files` 必须覆盖完整备份集，每项 `name/role/bytes/sha256`。在只读 NAS 上算真实 SHA256，记录操作时间；不要填 MD5 到 SHA256 字段。脚本会把读取到的完整文件在测试机重新校验 SHA256。
- 46：所有 `FULL_*.DMP`（role=`dump`）和该次完整 `export.log`（role=`export_log`）。有 ORA/UDI/LRM 错误的导出拒绝演练，不把残留 DMP 当完整备份。
- 41：该次全部数据库/归档备份片（role=`piece`）、控制文件备份（role=`controlfile`）、原生 RMAN 日志（role=`rman_log`），并填写源 `dbid` 与版本。跨多轮混合的备份片或归档缺口必须由 DBA 先核对；DUPLICATE 无法恢复时明确失败，不跳过归档强行标绿。
- 清单、SQL及 hashes 都是受控管理员审阅的本地文件，不是数字签名；不可从不受信上传者接收后直接执行。保存清单 SHA256及审核记录到私有演练档案。

46 默认 `mode=sample`，`schema_map` 如 `APP -> REST_APP`，`source_tablespaces` 列出所有来源业务表空间，`tablespace=REST_DATA`。业务表检查 owner 填映射后的用户。`minimum_rows` 必须至少 1，表示非空下限，不代表与生产此刻完全一致。需精确验收时由 DBA 在同一备份时间点提供期望行数及业务查询另做比对。

全库模式改 `mode=full`。会导入整个导出集的可导入内容，排除表空间/目录/DB_LINK/PROCOBJ 等外部环境元数据，仍须审查全部 schema 及表空间映射。因此“full”表示本次 Data Pump 全库导入范围，**不代表逐项还原了系统级外部依赖**。未映射的 schema 可能以原名称出现在专用 RT46，必须由 DBA 在 SQLFILE 中审阅，不会写生产库。

## 3. 离线生成计划（不连接任何服务器）

DBA 先核对映射、空测试库及目标根目录后设置 `ddl_reviewed=true`。用私有绝对路径：

```bash
python3 components/oracle/restore/restore_plan.py plan --config /private/oracle46.json --manifest /private/oracle46-manifest.json --output /private/oracle46-plan
python3 components/oracle/restore/restore_plan.py plan --config /private/oracle41.json --manifest /private/oracle41-manifest.json --output /private/oracle41-plan
```

计划目录必须不存在；输出 `PREPARED_ONLY`。审阅 `guard.sql/verify.sql`，46 的 `import.par/preview.par/prepare.sql`，41 的 `init.ora/startup.sql/duplicate.rman/validate.rman`。生成文件按 ASCII 输出，中文 UNC 在 JSON 内用 Unicode escape 表示，不通过客户端 SQL 编码拼接 UNC。

把整个 `components/oracle/` 复制到测试机 `C:\core-backup\oracle\`（保留 `json-compat.ps1` 和 `restore\` 相对层级），再把计划复制到根目录**之外**的独立位置，例如 `C:\restore-plan\oracle46\`。`plan-hashes.json` 绑定计划文件，改 SQL 后必须重新生成并复审，不能只改一行绕过守卫。

## 4. 在 251 执行

管理员 PowerShell，每次一条命令：

```powershell
& "$PSHOME\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File C:\core-backup\oracle\restore\invoke-restore.ps1 -PlanDirectory C:\restore-plan\oracle46 -Execute -PreviewOnly
```

该命令复制并校验完整 dump 集到本地，生成 SQLFILE，**只记 `partial`**。审阅本地 `stage\preview.sql`，确认数据库路径、业务 schema、表空间及外部依赖范围。正式导入用**新演练目录/新生成计划**，以保留预览证据，不删除旧 stage：

```powershell
& "$PSHOME\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File C:\core-backup\oracle\restore\invoke-restore.ps1 -PlanDirectory C:\restore-plan\oracle46-reviewed -Execute
& "$PSHOME\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File C:\core-backup\oracle\restore\invoke-restore.ps1 -PlanDirectory C:\restore-plan\oracle41 -Execute
```

正式46计划的根目录需对应新的空 RT46 库数据路径；由 DBA 安排新实例准备，不自动覆盖或复用上次实例。41执行前没有预览模式，先审阅生成的 RMAN 文件。

本地互斥锁使用文件独占句柄，不删除“陈旧锁”、不杀其他进程。工具不设强杀原生恢复作业的超时；长时间无输出时看本地 Oracle/worker 日志，不重复触发。状态在当前轮结束时写入 `last-result.json`；未产生回执的预检拒绝只能视为未执行，不会成为通过。

执行通过必须同时满足：来源字节/SHA256一致、测试实例/版本/所有数据库文件路径受限、原生工具退出0且日志无错误、关键表行数下限通过、所检查业务 schema 无 INVALID 对象；41额外要求新 DBID、RMAN `VALIDATE CHECK LOGICAL DATABASE` 完成且无块损坏登记。记录全部 INVALID 对象与控制文件/数据文件位置供 DBA 审阅；不在生产自动重编译任何对象。

## 5. 回传与看板契约

将测试机本次根目录的 `last-result.json` 及所有引用日志经受控管理通道复制到 ops 私有演练档案；核对 run、源备份集、plan_sha256、日志后再发布。工具不内置远程密码、自动 SSH/WinRM 上传或生产调度修改。

```bash
sudo python3 components/oracle/restore/restore_plan.py publish --receipt /private/evidence/oracle46/last-result.json
sudo python3 components/oracle/restore/restore_plan.py publish --receipt /private/evidence/oracle41/last-result.json
```

默认原子写 `/var/lib/backup-console/restore/oracle46.json` / `oracle41.json`，字段：

| 字段 | 口径 |
| --- | --- |
| schema_version/id/run/plan_sha256/source | 绑定资产、备份运行与审阅计划 |
| status | `passed` 原生恢复及要求的检查均通过；`partial` 仅SQLFILE；`failed` 本轮异常 |
| finished/sampled/seconds/target_ip/target_hostname/target_version | 本轮结束时间、发布观察时间（均UTC显式+00:00）、耗时和实际测试机/二进制版本 |
| scope/phases | 明确 sample/full/rman 范围、每阶段状态及本地日志名称 |
| business_acceptance | 固定 `not_tested`，人工业务验收另行记录，不能由 MD5/原生恢复替代 |
| untested | 始终保留业务验收/应用连通未测；样本恢复标明全库未测；预览标明原生恢复/表检查未测；逻辑导入排除的环境元数据单列未测 |
| deletion/production_change | `not_performed` / false |

发布器拒绝缺少阶段证据的 `passed`，核对回执旁每个阶段日志的 SHA256，并拒绝旧结束时间覆盖新结果。兼容旧 PS2 `.ToString('o')` 的7位小数/Z时间戳，截断至微秒并转换UTC；新worker用固定秒精度、Gregorian invariant culture与 `+00:00`。`sampled` 是本次读取并发布证据的时间，**不是重新恢复时间**；`finished` 保留真实结束时刻。回传时必须把引用日志和回执放在同一目录。这不是远程日志签名验证；负责人仍须核对原始日志和计划。现有8100 exporter可直接解析 `sampled` 并投影 `status/scope/untested/deletion`；采集器重新采集后才显示，不能只写回执就宣称已在页面出现。为避免旧通过掩盖新失败，失败结果也必须回传发布。日志会含对象名、路径，按私有运维资料保管，不对所有看板用户公开原文。

## 6. 本地验证与尚未完成项

```bash
python -m unittest discover -s tests/oracle -p 'test_*.py' -v
```

```powershell
powershell.exe -Version 2 -NoProfile -File tests\oracle\test-worker.ps1
```

以上只运行本地临时文件与模拟原生进程，完全不触碰 Oracle/NAS。真实 Win2008R2 Oracle 的启动、导入、RMAN DUPLICATE 与业务恢复仍须在测试机具备二进制、服务、空间和审阅清单后执行；本 PR 不包含真实恢复成功的证明。

参考 Oracle 11g 官方 [备份式无源连接 DUPLICATE](https://docs.oracle.com/cd/E25178_01/backup.1111/e10642/rcmdupdb.htm)。SQLFILE 是计划预览，不是导入；RMAN VALIDATE 是原生校验，不等于完成业务验收。源备份本身的逻辑缺失、归档缺口不能靠 MD5 或重命名 `.partial` 修复。
