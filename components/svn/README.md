# SVN29 后台备份候选实现（FIX-SVN-01）

仅本地实现与测试，未经独立审核不得直接安装到生产。要求 Windows PowerShell 2.0 / .NET 2+，使用现有 VisualSVN 的 svnadmin/svnlook，不停服务。

## 安装与配置

在源机管理员 PowerShell 执行前，先核对旧任务、生产脚本版本、现有运行进程和仓库路径。安装器默认创建 **禁用** 的 SYSTEM 后台任务，不启动备份，也不自动执行预检。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-svn-task.ps1 -PlanOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install-svn-task.ps1
```

默认：源 D:\Repositories，VisualSVN bin 为 C:\Program Files\VisualSVN Server\bin，目标仅 \\192.168.5.240\资源备份；本机私有目录为 ProgramData\CoreSvnBackup。可用同名参数修改这些路径。凭证提示输入NAS账号，不是Windows登录密码。

机器级 DPAPI 生成 nas240.dpapi，目录 ACL 仅 SYSTEM/Administrators。不要提交或复制到其他机器。更新NAS密码：在源机管理员上下文运行已安装的 svn-backup.ps1 -SetupCredentials；无需修改Windows任务密码。出现1219时保留既有SMB会话并失败，不断开其他业务连接。

安装后通过计划任务中同一 SYSTEM 身份运行 launch-svn.cmd -CheckOnly，检查本地日志与 last-result.json 的 check_ok；确认后才由管理员启用每日01:00任务 CoreBackup-SVN29。此仓库测试不会执行注册/启用。

## 执行与状态

- `-CheckOnly`：连接SMB、容量/仓库检查、临时探针读写删除；不hotcopy、不清理历史。结果为 check_ok，不表示备份成功。
- `-SmallTest`：仅研发一部仓库，正常hotcopy与verify，但回执 SmallTest=true，绝不计为全量成功周期，不执行清理。
- 正常执行：hotcopy每库 -> verify -> 复制库UUID/youngest -> 根配置白名单 -> 清单回执 -> 同目录原子重命名正式run。
- 本地 logs/yyyy-MM-dd.log 持续落盘；last-result.json 含 Status/Run/Target/Seconds/Repositories/Error/Mode/Published。失败保留partial，已发布后清理失败标为 cleanup_failed，与源端备份失败区分。
- 本机文件互斥和任务 IgnoreNew 双重防重；互斥失败退出2，不覆盖另一个任务的结果。
- hotcopy-result.json 为正式副本证据；MD5、真实恢复演练独立，不因verify通过就宣称恢复通过。

## 保留与删除边界

默认 KeepCycles=15，保留15个**不同日期的完整全量周期**；一天多次正式run都属于该日期。少于或等于15个有效日期不删。不按墙钟15天强删。

Clean仅接受本版本 Owner=svn-hotcopy-v2、Asset/Date/Run匹配、Status=hotcopy_verified、SmallTest=false且文件名/大小清单完全一致的目录。拒绝ReparsePoint、路径穿越、未知文件及未知空目录。一个日期含partial/未知内容就整日跳过；旧版无Owner回执保留，不自动纳管删除。

Repositories、Files 必须是非 null、非空数组；Directories 必须是非 null 数组。清理时复核无链接后仅剥离 ReadOnly 属性，保留其他属性；先删除清单负载和负载目录，最后删除 hotcopy-result.json。中途失败保留回执用于定位，但缺失负载的残留批次不再视为完整副本。cleanup_failed 在日志与结果中保留原异常明细。

`-SkipCleanup` 可以禁用本轮清理。安装时同名参数写入每日任务启动器。清理不访问源仓库，不用递归删除；仅删除经过校验的清单文件与空目录。共享ACL须限制非管理员并发修改，基于路径的SMB校验不能彻底消除恶意并发替换的竞态。

## 测试

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests\svn\test-svn.ps1
```

测试在随机本地临时目录使用模拟svnadmin/svnlook与SMB适配器，不调用网络、不注册任务、不修改服务。PS5.1结果不能替代Windows Server 2008的PS2现场验证；首次上线应先禁用清理预检并做小仓库测试。
