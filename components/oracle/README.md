# Oracle直接导出到NAS243（首次验证版）

用户已选择直接到NAS，避免本地D盘暂存120G/57G dump。先46，41当前EXFSYS导出包错误必须另行诊断，不能靠换目录修复。工具不会重编译EXFSYS、移除组件、改Oracle服务账号、重启数据库/SSH或修改原DATA/DATA_DIR。

本包适用Windows PowerShell2、Oracle服务LocalSystem（两台已确认）。借助受保护的SYSTEM计划任务建立NAS SMB连接，随后用数据库UTL_FILE实际写入并删除一个专用小测试文件证明Oracle进程能访问UNC。SYSTEM测试成功不等于Oracle一定可访问，只有ORACLE_SERVICE_NAS_WRITE_OK才通过。

## 安装与首次准备（只在46执行）

全部解压到C:\core-backup\oracle-direct-nas。管理员PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\core-backup\oracle-direct-nas\prepare-direct-nas.ps1 -Server 46
```

凭据窗口输入NAS243备份账号（默认databak）及密码，不是数据库密码。凭据不会写入脚本、日志或进程参数；使用Windows DPAPI LocalMachine加密保存在C:\ProgramData\CoreOracleNas46，目录ACL仅SYSTEM/Administrators。DPAPI LocalMachine必须配合严格ACL，本机管理员仍有解密能力，不能当作硬件密钥保护。不要公开或复制凭据文件。

脚本会新增独立计划任务CoreOracleNas46-243，SYSTEM运行，开机延迟1分钟尝试恢复NAS会话，本次立即运行。只操作这个专用任务，已有同名任务先保存XML。不会清除已有SMB连接；遇1219连接冲突则失败，需要核对SYSTEM已有连接，不执行net use * /delete。

输出需要包含：

```text
SYSTEM_SMB_OK
ORACLE_SERVICE_NAS_WRITE_OK directory=CORE46_...
PREPARED_ONLY C:\core-backup\nas46-运行号
```

准备会新建唯一Oracle DIRECTORY、NAS日期/临时运行目录，不覆盖原DIRECTORY，不生成全库dump。为避免中文客户端编码问题，DIRECTORY路径通过UNISTR指定。UTL_FILE测试失败则停止，错误保留；不转用不安全共享、SMB1或更换业务服务身份。

## 正式直接导出

确认准备成功、无其他导出作业运行、在低峰执行。原本C:\core-backup\notify.json可继续用于通知，旧Windows若TLS/网络不通会记录NOTIFY_FAILED，不能假定消息已送达。

把下面prepared.json路径换为PREPARED_ONLY输出的实际路径：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\core-backup\oracle-direct-nas\start-direct-export.ps1 -Prepared C:\core-backup\nas46-实际运行号\prepared.json
```

会检查EXFSYS包状态、是否有其他DataPump导出、NAS可用空间（46至少200GiB；41至少100GiB）以及本次目录无旧dump，随后执行已生成参数FULL=YES、PARALLEL=1、10G分片、FLASHBACK_TIME。无本地大文件中转，仅本地参数/日志元数据。

成功后将NAS本次.partial目录发布为正式运行目录，并写pending.json，表示导出成功但MD5尚未执行。这里只直备NAS243，不会同时生成240/254副本、不自动清理历史、未接中台统一清单。不能把NAS243成功当三端成功。

目标：\\10.10.11.243\资源备份\oracle01_10.10.10.46_dev研发环境数据库\日期\运行号\。失败保留.partial、export.log，不发布成功状态，不删除源业务数据。切勿反复准备/导出产生重复大文件。

直接模式依赖SMB持续稳定，断网或NAS空间被其他任务耗尽仍会导致导出失败。首次仅检查开始空间，未提供运行中NAS空间守护；需人工观察，不能宣称零风险。关闭窗口或杀客户端不保证停止服务器Data Pump任务；如需取消，必须由DBA attach对应作业STOP_JOB，不重启库。

## 41

可以-Server 41只验证服务访问NAS，但正式启动器会拒绝已知的EXFSYS无效导出包，不自动重试失败全库。必须先诊断组件错误，或由DBA明确制定另一份备份策略；不擅自EXCLUDE有问题的内容后叫完整备份。

## 撤回准备配置

1. 停止使用本包，不再运行导出。
2. 确认没有当前导出后，删除专用任务CoreOracleNas46-243（41对应替换），其他任务不动。
3. 本次新增DIRECTORY名称、UNC和参数记录在prepared.json；由DBA确认无作业使用后DROP这个专用DIRECTORY。原DATA/DATA_DIR没改，不需要恢复。
4. 保留NAS备份及日志，不自动删除。DPAPI私有目录在不再需要后由管理员处理；NAS网络会话不全局强制断开，以免影响其他任务。

本地仅完成语法和参数检查；未在你的LocalSystem/Oracle环境执行。先提交准备输出再正式导出最稳妥。

参考：Microsoft LocalSystem/网络会话 https://learn.microsoft.com/en-us/windows/win32/secauthz/client-logon-sessions ；WNetAddConnection2 https://learn.microsoft.com/en-us/windows/win32/api/winnetwk/nf-winnetwk-wnetaddconnection2w 。
