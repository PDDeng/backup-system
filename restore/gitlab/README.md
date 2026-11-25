# GitLab 隔离恢复 Compose 模板

在 ops253 上使用 Docker Compose V2。仅操作新的测试目录，不能映射生产 GitLab 目录，也不能复用已有恢复实例目录。现有185恢复容器不由此文件管理。

## 逐台配置

复制 `restore.env.example` 为每台独立的环境文件，按备份文件名、version-manifest.txt 再次确认精确版本和 CE 版本。以下是已提供的版本，不代表自动核验了未来备份：

| 资产 | 镜像标签 | 建议 HTTP / SSH |
|---|---|---|
| 52 | 10.5.8-ce.0 | 18086 / 22286 |
| 191 | 10.5.4-ce.0 | 18087 / 22287 |
| 247 | 10.5.4-ce.0 | 18089 / 22289 |
| 14 | 10.5.4-ce.0 | 18091 / 22291 |
| 16 | 10.0.3-ce.0 | 18092 / 22292 |
| 185（另建测试） | 18.1.0-ce.0 | 18093 / 22293 |

每台同步修改 COMPOSE_PROJECT_NAME、TEST_HOSTNAME、TEST_ROOT、版本、两个端口。先用 `ss -lnt` 确认端口空闲。一次只运行一台新测试，ops 当前还承载其他恢复任务，注意内存和磁盘余量。老版本镜像在新宿主机上是否兼容，必须实际启动验证。

## 准备并启动

以下示例环境文件为 `restore52.env`。先配置好，再执行；mkdir 不覆盖文件，但 TEST_ROOT 必须是本次新目录。

```bash
docker compose --env-file restore52.env -f compose.yml config --quiet
set -a
. ./restore52.env
set +a
test ! -e "$TEST_ROOT" || { echo '测试目录已存在，请核实，停止'; exit 1; }
install -d -m 700 "$TEST_ROOT" "$TEST_ROOT/config" "$TEST_ROOT/logs" "$TEST_ROOT/data" "$TEST_ROOT/data/backups"
docker compose --env-file restore52.env -f compose.yml pull
```

把本次选定的备份 tar **复制**至 `$TEST_ROOT/data/backups/`，将同一批次 `gitlab-config.tar.gz` 解到另一个新暂存目录，找到其中 `gitlab-secrets.json`，仅把该文件复制到 `$TEST_ROOT/config/gitlab-secrets.json` 并设为600。不要直接解压配置包覆盖 config，不要复制生产 gitlab.rb、SSL证书或生产 external_url。保留原文件，记录源/目标 SHA256。

```bash
docker compose --env-file restore52.env -f compose.yml up -d
docker compose --env-file restore52.env -f compose.yml logs --tail 80 gitlab
docker compose --env-file restore52.env -f compose.yml exec gitlab gitlab-ctl status
```

模板使用内部网络阻断容器直接访问生产网络，禁用 Sidekiq 和邮件。网页可以检查登录、项目、仓库；CI、异步任务、Webhook、邮件和外部集成不属于这次隔离验证。不要为验证而全局启用后台任务。GitLab HTTP 直接暴露给测试网，请限制访问来源，勿向公网发布。旧版 GitLab 本身存在已知历史风险，只用于隔离演练。

## 手动恢复

先确认新实例无业务数据。`BACKUP` 是归档文件名去掉 `_gitlab_backup.tar` 后的部分。恢复会覆盖**测试容器内数据库**；不要将命令用于生产容器。

```bash
docker compose --env-file restore52.env -f compose.yml exec gitlab sh -c 'chown git:git /var/opt/gitlab/backups/*_gitlab_backup.tar'
```

GitLab 10.x 停 Unicorn；18.x 停 Puma。Sidekiq 已禁用，无需启动。

```bash
# 10.x
docker compose --env-file restore52.env -f compose.yml exec gitlab gitlab-ctl stop unicorn
docker compose --env-file restore52.env -f compose.yml exec gitlab gitlab-rake gitlab:backup:restore BACKUP=填写实际备份ID
```

```bash
# 18.x，改用对应的环境文件
docker compose --env-file restore185.env -f compose.yml exec gitlab gitlab-ctl stop puma
docker compose --env-file restore185.env -f compose.yml exec gitlab gitlab-backup restore BACKUP=填写实际备份ID
```

恢复后执行 reconfigure，再启动对应 Unicorn/Puma：

```bash
docker compose --env-file restore52.env -f compose.yml exec gitlab gitlab-ctl reconfigure
docker compose --env-file restore52.env -f compose.yml exec gitlab gitlab-ctl start unicorn
docker compose --env-file restore52.env -f compose.yml exec gitlab gitlab-rake gitlab:check SANITIZE=true
```

18.x 最后一条启动命令改为 `gitlab-ctl start puma`。原版本有可用 secrets 检查命令时追加检查，但不能用网页可打开代替仓库和附件验证。

## 开发验收记录

打开环境文件对应的 `http://PUBLIC_HOST:HTTP_PORT/`，使用备份时已有账号登录。若原账号启用外部认证，隔离环境可能无法登录，需要单独建立测试访问方案，不能修改生产账号。

记录：备份批次、镜像版本、复制 SHA256、恢复起止时间/退出码、原生检查结果；抽查项目数量与关键项目、克隆并查看提交历史、下载附件/LFS（如使用）、权限和关键业务页面。每项记录通过/失败及证据，未经验证不能标为可恢复。

测试结束只执行 `docker compose --env-file restore52.env -f compose.yml stop`。本轮不要执行 down -v、目录清理或删除命令。保留数据和日志。
