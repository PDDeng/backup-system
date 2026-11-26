"""One daily summary through the existing webhook helper; no inline credentials."""
import argparse, datetime as dt, json, os, subprocess, sys
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl = None
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None
from platform_core import convert, report

FAILED_EXEC_STATUSES = {'failed', 'failure', 'error'}


def _failure_reason(asset: dict) -> str:
    exec_status = str((asset.get('execution') or {}).get('status', '')).strip().lower()
    cov = asset.get('coverage')
    targets = asset.get('targets') or []

    if exec_status in FAILED_EXEC_STATUSES:
        return '备份任务执行报错'
    if not targets:
        return '目标存储尚未接入'
    if cov == 'unknown':
        return '采集异常待确认（非确认备份丢失）'

    missing = [t['storage_id'] for t in targets if t.get('status') not in ('available', 'pending_verification')]
    stale = [t['storage_id'] for t in targets if t.get('status') == 'stale']
    if cov == 'unavailable':
        if stale and len(stale) == len(targets):
            return '备份记录已过期'
        return '无有效备份结果'
    if cov == 'partial':
        if exec_status == 'running':
            return '备份/同步进行中（' + ','.join(missing) + '尚未到位）' if missing else '备份/同步进行中'
        if missing:
            return '部分副本缺失或过期（' + ','.join(missing) + '）'
        return '多端备份批次不一致'
    return '无有效备份结果'


def build_summary_message(data: dict, stamp: str, board_url: str = 'http://192.168.1.212:8100/') -> str:
    """Build concise daily backup summary: total, succeeded, failed count, and failure details (no MD5 noise)."""
    assets = data.get('assets') or []
    total = len(assets)
    succeeded = []
    failed_lines = []
    unknown_count = 0

    for a in assets:
        cov = a.get('coverage')
        exec_status = str((a.get('execution') or {}).get('status', '')).strip().lower()
        is_ok = cov in ('available', 'pending_verification') and exec_status not in FAILED_EXEC_STATUSES
        if is_ok:
            succeeded.append(a)
        else:
            if cov == 'unknown':
                unknown_count += 1
            failed_lines.append(f"- {a.get('name', a.get('asset_id', '未知资产'))}：{_failure_reason(a)}")

    fail_count = len(failed_lines)
    rate = f"{(len(succeeded) / total) * 100:.1f}%" if total > 0 else '0.0%'
    fail_header = f"失败数量：{fail_count} 项" + (f"（含采集待确认 {unknown_count} 项）" if unknown_count else '')

    lines = [
        f"【核心资产备份日报｜{stamp}】",
        f"需备份数量：{total} 项",
        f"已备份数量：{len(succeeded)} 项（{rate}）",
        fail_header,
        "失败明细：" + ("" if failed_lines else "无"),
    ]
    lines.extend(failed_lines)
    lines.append(f"详情：{board_url}")
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--send', action='store_true')
    p.add_argument('--snapshot')
    a = p.parse_args()
    os.umask(0o077)
    state = Path('/var/lib/backup-console')
    source = Path(a.snapshot) if a.snapshot else Path('/var/lib/backup-board-feed/snapshot.json')
    if not a.snapshot and not source.exists():
        source = state / 'snapshot.json'
    snapshot = json.loads(source.read_text(encoding='utf-8-sig'))
    data = convert(snapshot)
    if data.get('collecting'):
        raise SystemExit('Snapshot is collecting; summary deferred')
    try:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(snapshot['sampled'])).total_seconds()
    except (KeyError, ValueError, TypeError):
        raise SystemExit('Invalid snapshot timestamp; report deferred')
    if not -300 <= age <= 1800:
        raise SystemExit('Stale snapshot; report deferred, not marked delivered')
    stamp = dt.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    text = report(data)
    tmp = state / 'standard-report.md.tmp'
    tmp.write_text(text, encoding='utf-8')
    tmp.replace(state / 'standard-report.md')
    message = build_summary_message(data, stamp)
    if not a.send:
        print(message)
        return
    with (state / 'daily-summary.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sent = state / 'daily-summary-sent.json'
        if sent.exists() and json.loads(sent.read_text(encoding='utf-8-sig')).get('date') == stamp:
            return
        cfg = Path('/var/lib/backup-board-notify/daily-webhook.json')
        if not cfg.exists():
            cfg = Path('/etc/core-backup-ops/notify.json')
        helper = Path('/opt/core-backup-modular/notify.py')
        if json.loads(cfg.read_text(encoding='utf-8-sig')).get('Enabled', True) is not True:
            raise SystemExit('Webhook disabled; report generated, no delivery recorded')
        subprocess.run([sys.executable, str(helper), str(cfg), 'SUMMARY', '核心资产备份平台', message], check=True, timeout=60)
        sent.write_text(json.dumps({'date': stamp, 'sampled': data.get('sampled_at'), 'sent': True}), encoding='utf-8')


if __name__ == '__main__':
    main()
