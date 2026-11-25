# 统一上海时区与阿里云NTP

范围：ops本机及六台Linux GitLab宿主机。复用/etc/core-backup-ops/ops数字.json的SSH密钥/地址，不把ops当NTP源，不进入容器改时间。Windows和NAS不在本脚本变更范围。

阿里云官方公网域名：ntp.aliyun.com、ntp1.aliyun.com、ntp2.aliyun.com。内网必须允许DNS和出站UDP123及返回流量；TCP123测试不能代表NTP可用。脚本实际发送只读NTP查询，并校验返回来源时间戳、状态和延迟，至少两个源一致才允许变更。

## 运行策略

* 默认只检查，不修改系统。每台记录时区、时间偏差、NTP状态、源；最终汇总Webhook。
* --apply才设置Asia/Shanghai并配置已安装的chrony客户端；不会安装软件或替换其他正在运行的时间服务。
* chrony配置先备份，再统一为三个阿里云源、原driftfile、maxslewrate1000、rtcsync，不包含makestep/initstepslew。只重启时间服务，不重启GitLab、SSH、数据库或虚拟机。原配置若为时间服务器/peer/refclock则拒绝覆盖。
* 默认偏差大于5秒、两个源相差大于1秒、NTP网络不通则拒绝自动修改，需要维护窗口处理。不会date -s、ntpdate、chronyc makestep、hwclock写时钟或强制跳时。
* chrony逐步校准需要时间，--apply结束的warning可能表示仍在选源；5至10分钟后只检查验证。不能把“服务已启动”当成“同步完成”。
* 没装chrony、仅有ntpd/timesyncd活动服务、特殊chronyd选项、localtime误建成目录都明确报告失败，不盲目修复。老CentOS源不可用时不自动改源。需要根据首轮报告补齐这些环境问题。

修改时区会改变cron本地触发时间。先核对当前任务；52如果还保留UTC换算17点任务，必须在改上海时区后改回01点；不要同时保留两条。不会自动编辑其他任务。

## 安装（ops root）

将unified-time-1.0.zip放/home/ops，执行：

```bash
mkdir -p /opt/core-time /etc/core-time
unzip /home/ops/unified-time-1.0.zip -d /opt/core-time
test -e /etc/core-time/settings.json || install -m 600 /opt/core-time/settings.example.json /etc/core-time/settings.json
chmod 700 /opt/core-time /etc/core-time
```

先全量检查：

```bash
python3 -u /opt/core-time/time-all.py >> /var/log/core-time.log 2>&1
```

首次只对已确认的185应用：

```bash
python3 -u /opt/core-time/time-all.py --servers 185 --apply >> /var/log/core-time.log 2>&1
```

确认报告后其他Linux统一应用：

```bash
python3 -u /opt/core-time/time-all.py --apply >> /var/log/core-time.log 2>&1
```

结果：cat /var/lib/core-time/last-report.json；日志：tail -n80 /var/log/core-time.log。服务器依次执行，单机失败继续下一台。发生SSH超时仍会报告，不假报成功。先确认ops时钟正常，否则批量计划的时间判断也不可靠。

## 定时

NTP时间服务持续校准，不用cron反复强制校时。首次应用并确认后，在ops root crontab添加只读巡检：

```cron
47 * * * * /usr/bin/python3 -u /opt/core-time/time-all.py >> /var/log/core-time.log 2>&1
```

巡检不带--apply，不反复重启时间服务。时区配置持久保存，偏差/时区变更/NTP不同步会告警；若要重新修正，手动执行--apply。notify_success=false可只发异常。真实Webhook沿用/etc/core-backup-ops/notify.json，包内不含口令。

## 回退及风险边界

原chrony配置位于原路径.before-core-time.时间戳-PID；原时区信息在/root/core-time-backups/目录。按报告中的实际路径恢复配置，再在控制台核对后恢复原时间服务；原NTP配置若含makestep，恢复及重启前需要评估跳时风险。调整后的真实时钟无法通过复制配置“回滚”，脚本因此拒绝大偏差且不进行跳时。

本地5项逻辑测试已通过，尚未批量变更生产。无需单独向客户端复制脚本：ops通过现有SSH发送内存脚本，远端需Python2.7或3、timedatectl、已安装chrony。只新增NTP配置备份，不改变备份数据。

官方资料：https://www.alibabacloud.com/help/en/ecs/user-guide/alibaba-cloud-ntp-server ；https://chrony-project.org/doc/3.2/chrony.conf.html 。
