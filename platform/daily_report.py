"""One daily summary through the existing webhook helper; no inline credentials."""
import argparse,datetime as dt,fcntl,json,os,subprocess,sys
from pathlib import Path
from zoneinfo import ZoneInfo
from platform_core import convert,report

def main():
    p=argparse.ArgumentParser();p.add_argument('--send',action='store_true');p.add_argument('--snapshot');a=p.parse_args();os.umask(0o077)
    state=Path('/var/lib/backup-console')
    source=Path(a.snapshot) if a.snapshot else Path('/var/lib/backup-board-feed/snapshot.json')
    if not a.snapshot and not source.exists():source=state/'snapshot.json'
    snapshot=json.loads(source.read_text());data=convert(snapshot)
    if data.get('collecting'):raise SystemExit('Snapshot is collecting; summary deferred')
    try:age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(snapshot['sampled'])).total_seconds()
    except (KeyError,ValueError,TypeError):raise SystemExit('Invalid snapshot timestamp; report deferred')
    if not -300<=age<=1800:raise SystemExit('Stale snapshot; report deferred, not marked delivered')
    stamp=dt.datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    text=report(data);tmp=state/'standard-report.md.tmp';tmp.write_text(text);tmp.replace(state/'standard-report.md')
    good=[x for x in data['assets'] if x['coverage']=='available'];attention=[x['name'] for x in data['assets'] if x['coverage'] in ('partial','unavailable')];unknown=[x['name'] for x in data['assets'] if x['coverage']=='unknown']
    restored=[x['name'] for x in data['assets'] if x['restore']['status']=='passed']
    pending=[x['name'] for x in data['assets'] if x['coverage']=='pending_verification']
    message='【核心资产备份日报｜'+stamp+'】\n已确认副本齐全：'+str(len(good))+'/'+str(len(data['assets']))+'；采集待确认：'+str(len(unknown))+'\n文件已到位、待MD5复核：'+str(len(pending))+'项'+('（'+'、'.join(pending)+'）' if pending else '')+'\n需处理：'+('、'.join(attention) if attention else '无')+'\n采集异常（不代表备份丢失）：'+('、'.join(unknown) if unknown else '无')+'\n技术恢复验证通过：'+('、'.join(restored) if restored else '尚无')+'\n口径：副本存在不等于MD5或恢复通过。\n详情：http://10.10.10.253:8100/'
    if not a.send:print(message);return
    with (state/'daily-summary.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        sent=state/'daily-summary-sent.json'
        if sent.exists() and json.loads(sent.read_text()).get('date')==stamp:return
        cfg=Path('/etc/core-backup-ops/notify.json');helper=Path('/opt/core-backup-modular/notify.py')
        if json.loads(cfg.read_text(encoding='utf-8-sig')).get('Enabled',True) is not True:
            raise SystemExit('Webhook disabled; report generated, no delivery recorded')
        subprocess.run([sys.executable,str(helper),str(cfg),'SUMMARY','核心资产备份平台',message],check=True,timeout=60)
        sent.write_text(json.dumps({'date':stamp,'sampled':data.get('sampled_at'),'sent':True}))
if __name__=='__main__':main()
