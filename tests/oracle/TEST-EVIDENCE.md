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
