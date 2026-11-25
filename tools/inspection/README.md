# SVN/Nexus备份准备

目标为192.168.0.29 SVN1.10.6和10.10.10.51 Nexus2.14.11-01。16上发现Nexus3.19.1，属于额外实例，不能当51的同一份备份处理。先确认目标服务器操作系统，包内Windows/Linux任选相应脚本；不要为运行巡检安装新运行环境或重启服务。

Windows管理员PowerShell：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\core-backup\inspect-windows.ps1
```

仅本进程ExecutionPolicy，不改系统策略。输出服务/版本/路径/磁盘，默认不递归扫描大目录、不读取认证文件内容。确认仓库路径后可追加参数-RepositoryRoot D:\Repositories或-NexusData D:\sonatype-work\nexus。权限/进程参数可能含内部路径，脚本只保留选定标志并对常见口令字段做遮盖，仍需在分享前检查。

Linux root：

```bash
bash /root/inspect-linux.sh
```

确认路径后可以传入svn根目录和Nexus数据目录；不会全盘搜文件，不自动运行du全量扫描。示例bash inspect-linux.sh /srv/svn /opt/sonatype-work/nexus。第二参数路径未知时留空，不照抄示例作为实际配置。

## 下一步要确认

* SVN：真实服务端仓库根目录（不是工作副本）、svnadmin工具路径、仓库数量和容量；独立认证/授权、Apache/VisualSVN配置及证书位置。认证文件只备份不公开内容。
* Nexus51：版本、安装目录、实际work目录、storage是否外置、配置与元数据目录、数据量；进程里显示的16号Nexus3与该实例独立。
* 两者现有备份生成、同步和清理定时任务；先只导出查看，不禁用未知清理或业务任务。

SVN计划使用svnadmin hotcopy生成仓库一致副本，包含仓库hooks/conf；外部认证/服务配置另备份。仓库备份成功后发布标记，中央分发；可在服务运行时生成，仍有I/O负载。实际实施前用当前安装版本验证命令。

Nexus2不能套Nexus3数据库备份任务。不能把运行中直接tar整个数据目录说成可恢复的一致备份。优先确认现有可用快照/应用备份方案；若需要暂停写入或服务必须明确维护窗口，不自动停服务。Nexus3 OrientDB实际备份方案另行配置，不复制正在使用的数据库文件后标成功。

统一目标仍为三个“资源备份”共享根下：svn01_192.168.0.29_svn代码库/日期/运行号 和 nexus01_10.10.10.51_maven制品库/日期/运行号。当前GitLab分发器校验_gitlab_backup.tar格式，尚不能直接填写SVN/Nexus类型就上传；后续需要通用成功清单适配。

参考：SVN hotcopy https://svnbook.red-bean.com/en/1.7/svn-book.pdf ；Nexus2目录 https://support.sonatype.com/hc/en-us/articles/213464308-Repository-Understanding-Nexus-Repository-2-Basic-Directories 。
