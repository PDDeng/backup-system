# 核心资产备份驾驶舱

独立部署于ops253的8100端口，保留8088/8090现状。浅蓝色管理大屏，真实证据投影；账号存PostgreSQL16，Web为Flask/Gunicorn。设计规模100个账号，2worker/8线程；尚未做100并发实测，不能把账号容量当并发保证。

功能：登录、首次改密、个人改密、退出、管理员创建/禁用/启用/重置账号、角色隔离、自动刷新、资产筛选/详情、异常列表、NAS容量、恢复验证独立展示。无公开注册、无生产删除/恢复/备份执行接口。

## 部署

将整个目录复制至 `/opt/backup-board`，root执行：

```bash
cd /opt/backup-board
bash install.sh
cat secrets/initial-admin.txt
```

访问 `http://10.10.10.253:8100/` 或 `http://192.168.1.212:8100/`。初始admin密码随机生成，首次登录强制修改。建议先给董事长创建viewer账号，首次登录自行改密。密码为加盐哈希，初始凭证文件仅root可读；管理员重置会使原会话失效。不把凭证加入程序包。

HTTP适用于当前受控内网调试；正式长期使用请经内网HTTPS反向代理访问，设置COOKIE_SECURE=true后 `docker compose up -d`。数据库不向宿主机开放端口。禁止把8100直接映射公网。

## 数据与刷新

现有 `/var/lib/backup-console/snapshot.json` 是主要输入。对GitLab速度模式，新export还只读核对该执行批次NAS的pending.json、精确文件集合与大小，不计算MD5、不更改完成记录。定时器每分钟读取快照，输出专用feed；容器仅只读挂载feed，不能读取原控制台令牌、SSH密钥或Windows凭证。页面每30秒拉取；30分钟未有新采样显示过期。刷新页面不启动源端备份，也不提高原采集频率。

副本完整记录、MD5/原生校验、恢复演练分别展示。存在批次冲突、缺少目标、未来日期、过期副本时标为需关注。没有数据时显示不可用，不使用演示数据冒充生产。

## 运维

```bash
docker compose ps
docker compose logs --tail 80 web
systemctl status core-backup-board-feed.timer --no-pager
```

数据库持久化在Compose命名卷board_db。升级先备份账号库，再更新代码/build。不要运行 `down -v`，不要删除卷或备份目录。现有备份定时任务与清理策略均不修改。

账号库备份示例（在当前目录root执行，文件必须私密保存）：

```bash
umask 077
docker compose exec -T db pg_dump -U board -d backup_board > "board-users-$(date +%Y%m%d-%H%M%S).sql"
```

恢复账号库只对新建隔离数据库演练；不要覆盖在线用户库。应用secret与数据库密码需另行受限保存，不要与公开代码包合并。同一账号登录失败10次/15分钟、同一IP失败100次/15分钟限流，Cookie HttpOnly/SameSite，所有POST校验CSRF；修改密码会撤销该用户其他会话。审计记录在audit表，只记录动作、账号和时间，不记录密码。

## 依赖与验证

程序包包含 Linux amd64 / Python 3.12 的官方 PyPI wheel 离线依赖及 SHA256 来源清单。基础镜像仍需拉取或导入。数据库使用内部网络；Web 另接 frontend 网络发布端口。

已通过7项状态模型测试和10项隔离 PostgreSQL 账号集成测试，覆盖首次改密、权限、会话失效、CSRF、限流及100账号上限。尚未完成100并发压测。

## 253当前访问方式（2026-11-25）

已从操作电脑验证两个网卡8100首页HTTP 200。当前Docker新容器创建异常，保留原运行Web容器，通过 backup-board-access.socket/service 转发到其内部地址172.20.0.3:8100；未重启Docker、未改8090。该转发依赖当前容器地址。后续维护窗口恢复常规Compose端口发布前，先停止 backup-board-access.socket 和 backup-board-access.service，再执行 docker compose up -d --build。新部署使用本包dashboard服务直接发布8100，不需要此临时转发。请勿在转发仍占用8100时重复部署。

实际浏览器登录后的视觉验收尚待完成，两个网卡HTTP访问与隔离账号接口测试已通过。初始账号在服务器执行 `sudo cat /opt/backup-board/secrets/initial-admin.txt` 查看，首次登录强制修改。


Feed update 2026-11-25: the live systemd entry is /opt/core-backup-platform/export-board-feed.py. Standard restore observations newer than the NAS snapshot are projected separately with a 20-minute freshness gate. Only status, scope and untested items are exposed. Existing 45-second systemd timeout is retained.

## 1.1 candidate: human business acceptance

Local candidate only; not deployed. Admin POST /api/acceptance records asset/run, test time, scope, steps, actual result, evidence and untested scope. Current arrived run and fresh evidence are required. PostgreSQL append-only application events retain tester identity and audit. Viewer reads records in asset detail. Automatic hash/restore evidence is never overwritten. Requires isolated PostgreSQL authorization/integration tests and UI verification before production rollout. 16 pure validation/model tests passed; Python and JavaScript syntax checks passed.

2026-11-25: 15 validation/PostgreSQL integration tests passed in retained isolated database board_test_acceptance_20261125 and separate read-only QA container board-acceptance-qa-20261125. Verified admin append/history, server-side actor, viewer write denial, CSRF, stale evidence, wrong run and required evidence; automatic restore/copy projection remained unchanged. Live account database and web container were not modified. Browser UI validation and production deployment still pending.

## 1.1 production rollout 2026-11-25

The acceptance feature is now live on8100 through the separate compose.acceptance-rollout.yml, with the old web retained for rollback and a pre-migration PostgreSQL dump retained. The original notes above describe earlier candidate gates. New install via compose.yml also initializes the additive table. Existing production upgrade must use the rollout instructions, not blindly rerun install.sh. start-access-proxy.sh resolves the named running container address at service startup; restart the proxy after recreating that container. Browser interaction/visual acceptance remains pending. No manual acceptance event was fabricated.
