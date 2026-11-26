# RESTORE-ORACLE-01 本地测试证据

日期：2026-11-26。基线 `origin/main=43cdcfd`，独立分支 `ai/codex/RESTORE-ORACLE-01`。

| 检查 | 实际结果 | 范围 |
| --- | --- | --- |
| `python -m unittest discover -s tests/oracle -p 'test_*.py' -v`，Windows Python3.9 | 12 tests，OK | 配置/清单拒绝矩阵、sample/full/RMAN计划、ASCII与SHA256绑定、禁止覆盖计划、结果阶段/日志哈希/旧时间保护、独占发布锁 |
| 同一 Python 测试集，ops Ubuntu Python3，新增隔离 `/tmp` 目录 | 12 tests，OK，exit=0 | Linux fcntl 发布锁及计划逻辑；未调用 Oracle、NAS、SSH/WinRM 备份执行逻辑 |
| `powershell.exe -Version 2 -NoProfile -File tests\oracle\test-worker.ps1` | 14 assertions，PASS；PS=2.0 | JSON嵌套契约、.NET原生参数往返（空格/引号/尾反斜杠/空值）、stdout+stderr/非零退出码、未授权执行/错误主机拒绝 |
| `powershell.exe -NoProfile -File tests\oracle\test-worker.ps1` | 14 assertions，PASS；PS=5.1 | 同上，仅本地模拟程序与临时文件 |
| .NET2 `csc.exe /target:library ... native-process.cs` | 编译成功 | 不依赖 ReadToEndAsync、Stopwatch.Restart、.NET4 API |
| Windows PowerShell Parser 检查执行器 | Syntax errors: 0 | 语法检查；真实 PS2 测试也已解析整份执行器 |
| `git diff --cached --check` | 无错误 | 交付路径内文本差异 |

测试夹具由本地生成，与真实备份数据无关。PS 测试不会创建数据库、查询 WMI 或访问 NAS；仅模拟原生可执行程序。测试完成删除的只有随机临时目录夹具，不含业务/备份文件。

未执行：251真实 Oracle 安装/启动、impdp、RMAN DUPLICATE、数据库开库及业务验收；不把本测试表作为恢复成功记录。不部署生产定时任务，不连接生产46/41，不扫描大文件 MD5。真实演练须由负责人审阅计划/备份清单并准备专用测试实例后安排。

Issue #7 的 Oracle46 首次每日备份证据已另在 GitHub 评论记录，仅只读复核 ops 回执与 NAS243/254 目录；不混入本地单测，也不作为 Oracle 恢复通过证据。

## r2 回归（2026-11-26）

- Windows Python3.9：**15/15 tests OK**；ops Linux 独立临时目录运行同一测试：**15/15 OK，exit=0**。
- 真实 Windows PowerShell2：**16/16 assertions PASS**；PS5.1：**16/16 PASS**。新增非 Gregorian 当前文化下的 invariant UTC 格式核验，无7位小数，明确 `+00:00`。
- 新回归覆盖 `.NET ...8821433Z`、带 `+08:00` 的7位小数、旧回执时间比较、非法无时区/日期拒绝；按 `export_feed.py` 原样 `fromisoformat(record['sampled'])` 和标准字段投影检查，不向实际看板发布夹具。
- C盘专用子目录、拒绝基础目录/Windows目录/目录逃逸、跨盘 OracleHome、46逻辑源版本1与显式目标版本4分离、41 RMAN拒绝降版本均覆盖。新模板选 `C:\core-backup\restore\<资产_演练号>` 和 `C:\app\oracle\product\11.2.0\dbhome_1`。
- 非阻塞建议一并收口：SQLFILE-only 独立 scope、UDE错误识别、完整必需计划文件哈希白名单。原生 .NET2 helper 无变化。
- 无251/46/41连接；无真实 impdp/RMAN 执行或状态伪造；仅私有临时文件单测与代码交付。
