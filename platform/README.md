# 统一结果与传输兼容层 0.7

这是渐进收敛的兼容层。结果、报告和观测模块只读；collect_bundle.py、bundle_transfer.py 按明确配置复制已完成备份。安装器不启用资产收集任务，不替换源端备份或既有清理，不执行备份文件删除。

platform_core.py读取现有控制台snapshot.json，输出core-backup-result/1.0结构化结果和Markdown日报。保留MD5待复核、原生校验和实际恢复状态，避免把“副本存在”显示为“恢复通过”。不同目标的数据日期不一致时显示partial。

在ops253执行：

```bash
python3 /opt/core-backup-platform/platform_core.py status --output /var/lib/backup-console/standard-result.json
python3 /opt/core-backup-platform/platform_core.py report --output /var/lib/backup-console/standard-report.md
```

输出目录须由管理员事先创建。platform_core.py只读已有快照，不连接业务数据库、不读取密码或令牌。不应将原始snapshot或程序日志公开发布。后续适配器将逐个迁移到该结果协议，旧脚本在验收前继续运行。

Windows观测为独立模块，不由安装器自动启用。observe_nexus.py和observe_oracle.py通过WinRM读取已部署任务、服务和结果，使用root专用的/etc/core-remote/credentials/<资产ID>.json，权限必须600；文件字段为host、username、password，程序包不包含实际凭证。需要已安装pywinrm、requests-ntlm。NTLM兼容仅作用于当前进程，不改系统OpenSSL配置。观测失败保留旧备份证据，不能据此判定备份任务终止。oracle_readonly.py仅执行明确列出的只读SQL。

ops当前启用Nexus、Oracle每5分钟观测，分别使用互斥锁、120秒超时，日志/var/log/core-nexus-observer.log和/var/log/core-oracle-observer.log。安装器不自动创建这些源端观测调度；现网Oracle任务在接入后单独启用。观测器不启动备份、不重启服务、不修改数据库或清理副本。5分钟观测结果有效期600秒，超期不推断任务终止。

check_connections.py为整体控制台的一键只读检查后端。固定资产来自/opt/backup-console/config.json；SSH严格检查known_hosts，Windows通过/opt/core-remote/run_ps_absolute.py执行明确的只读探针，兼容旧Windows损坏的PATH。安装器包含此助手，不再依赖人工补文件。Windows连接需要已部署的NTLM运行时及root私有凭证，程序包不包含凭证。连接检查不会启动备份或停止服务。

audit_schedules.py每日08:50只读核验六台GitLab的root cron，以及已接入的Oracle46/41、CRM200、DSE27任务。输出/var/lib/backup-console/schedules/资产ID.json，整体控制台“源端调度”可展开查看。原始cron不发布到网页，只保留已知备份/传输/清理动作及时间表达式。Windows只列指定任务前缀的状态、最近结果、下次时间，不读取任务密码。连接未完成的Nexus/SVN/金蝶不通过重复登录猜测调度状态；它们尚需补齐。此模块不注册、启用、禁用或运行任何源端任务。

install.sh会设置每日09:10一条汇总到现有Webhook，按北京时间日期去重。daily_report.py不加--send时仅生成日报和预览摘要；加--send才通过现有notify.py发送，不在程序包内保存Webhook。安装过程不发送消息。与旧控制台09:05本地报告并存，旧报告是否推送须单独审计以避免重复。


0.5 includes bundle_transfer.py for exact mounted-source checks, per-attempt partial directories, durable copy and no-overwrite publication. It never removes files. Manifest SHA256 identifies metadata, not payload MD5. Source schedule audit supports old PowerShell registry timezone lookup. Target retention template sets daily win254=7; other daily targets15, Harbor weekly3. Templates do not apply production policies. This incremental package is not full project acceptance.

## 配置通用分发

`dse27-bundle.example.json`演示DSE源端先上传240，再由ops分发243/254。`oracle41-bundle.example.json`演示原生验证通过的RMAN包从243分发240/254。复制模板后先逐项核对资产目录、挂载SOURCE、日期新鲜度、状态目录与原生验证要求；模板不自动激活调度。

```bash
python3 /opt/core-backup-platform/collect_bundle.py --config /etc/core-backup-platform/dse27-bundle.json --check
```

去掉`--check`才会实际复制。失败目录保留，最终目录不覆盖；相同批次重跑必须重新核对现有目标文件和源清单身份。DSE适配仅证明文件集合与大小，不宣称原BAK提供了CHECKSUM。元数据SHA256不是负载MD5。

Oracle观测器优先读取已部署的每日任务。临时的Harbor恢复观测器`observe_restore.py`只用于本次20261124演练，安装器不自动部署或激活它，不能作为所有恢复任务的通用适配器。生产配置、凭证、私钥、令牌不随包分发。


## 0.7 release boundary (2026-11-25)

Includes fresh-feed daily reports, pending-MD5 separation, explicit Oracle logical run matching, exact source inventory gates, read-only retention plans, scoped Harbor restore observations and Oracle46 daily-task observation. Installation does not activate Oracle46, create client credentials, start source backups or apply retention. The Oracle46 published-source dispatcher is asset-specific; its tested collector alias and cron are configured separately on ops253. Existing Windows producer installers remain separate. First Oracle46 daily run is pending Nov26 00:00. No complete project acceptance is claimed.
