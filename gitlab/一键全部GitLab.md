# 一键全部GitLab及三端上传

入口backup-all-smb.py，替代旧backup-all.py进行本轮批量操作。六台顺序185、52、191、247、14、16，串行，单台失败继续下一台，不假装全部成功。调用已有源端脚本，不安装软件、不改GitLab配置、不重启服务。源端官方backup_keep_time清理仍可能执行，不承诺源端归档永远保留。

复用/etc/core-backup-ops中六份配置、SSH密钥、挂载和state。三个目标统一挂载在“资源备份”共享根；findmnt FSROOT须为/。最终目录“资源备份/资产名/日期/运行号/”。默认速度优先，verify-md5=off，目标文件pending.json待校验，不计完整保留日期，不触发目标历史清理。开关on使用完整校验。旧的已验证备份不降级。

先在页面暂停六台收集周期、等所有任务空闲。备份ops旧cron后注释对应旧中央分发条目。源端cron保留，但不要在其执行窗口重复启动批量。已在生成的服务器会跳过并标失败，后续只重试该台。

将core-backup-modular-1.1.zip上传到ops的/home/ops，root执行：

```bash
unzip -o /home/ops/core-backup-modular-1.1.zip -d /opt/core-backup-modular
```

然后一条命令：

```bash
python3 -u /opt/core-backup-modular/backup-all-smb.py --verify-md5 off >> /var/log/core-backup-all-smb.log 2>&1
```

有当天完成标记则复用，否则生成后上传；目标状态跳过前重新核对实际文件，缺失则重新收集。仍在/opt/core-backup-ops的旧版本不会被覆盖。

当前只收集已有备份、最快落地：在同一命令加--collect-only，不会启动源端生成。要求完整MD5把off改on。只重试16增加--servers 16。执行命令不会新增定时。

观察：

```bash
tail -F /var/log/core-backup-all-smb.log
```

汇总：

```bash
cat /var/lib/core-backup-ops/backup-all-result.json
```

全部success表示传输策略下成功；速度模式看verification=pending_md5，不是完整验证。失败的资产会在汇总列明并发Webhook，保留可追溯日志。只有MD5模式的md5_verified算完整验证；该模式会执行配置允许的目标过期清理。

注意：源端脚本SPACE/INODE阈值必须准确，16真实路径/home/gitlab/backups。源端SSH不通、空间不足、无完整标记都不会被绕过。此前16尚未确认扩容完成，需要实际修复后重试，不能承诺这条命令自动修复环境。
