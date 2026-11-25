"""Read-only compatibility layer for existing backup receipts and dashboard evidence.

No credentials, source backup commands, file deletion or service changes belong here.
"""
import argparse, datetime as dt, json, os
from pathlib import Path

SCHEMA='core-backup-result/1.0'

def normalize(asset, sampled):
    targets=[]
    copies={c['target']:c for c in asset.get('copies',[])}
    for t in asset.get('transfers',[]):
        old=copies.get(t.get('target'),{})
        if t.get('status')=='files_size_rechecked' and t.get('date','')>=old.get('date',''):
            copies[t['target']]=t
    for tid in asset.get('targets',list(copies)):
        c=copies.get(tid,{'target':tid,'status':'unknown'})
        limit=asset.get('max_age_days',8 if asset.get('type')=='Harbor' else 2)
        receipt=c.get('status')=='receipt_found'
        pending=c.get('status')=='files_size_rechecked'
        age=c.get('age_days')
        try:age=(dt.datetime.fromisoformat(sampled).date()-dt.date.fromisoformat(c['date'])).days
        except (ValueError,TypeError,KeyError):pass
        fresh=(receipt or pending) and age is not None and 0<=age<=limit
        status=('pending_verification' if pending else 'available') if fresh else ('stale' if receipt or pending else c.get('status','unknown'))
        targets.append({'storage_id':tid,'status':status,
                        'data_date':c.get('date'),'run_id':c.get('run'),'verification':c.get('verification','unknown'),
                        'unfinished_count':c.get('partials',0),'error':c.get('error')})
    available=[t for t in targets if t['status'] in ('available','pending_verification')]
    dates={t['data_date'] for t in available}
    runs={t['run_id'] for t in available if t['run_id']}
    uncertain={'error','collecting','unknown'}
    coverage=(('pending_verification' if any(t['status']=='pending_verification' for t in targets) else 'available') if targets and len(available)==len(targets) and len(dates)==1 and len(runs)<=1 else
              'unknown' if any(t['status'] in uncertain for t in targets) and all(t['status'] in ('available','pending_verification') or t['status'] in uncertain for t in targets) else
              'partial' if available else 'unavailable')
    execution=asset.get('execution') or {}
    current_run=execution.get('run',execution.get('Run'))
    if current_run and any(t['run_id'] and t['run_id']!=current_run for t in available):
        coverage='partial'
    return {'schema_version':SCHEMA,'asset_id':asset['id'],'name':asset['name'],'component':asset['type'],
            'os':asset.get('os','unknown'),'sampled_at':sampled,'coverage':coverage,'targets':targets,
            'execution':{'status':execution.get('status',execution.get('Status','unknown')),
                         'run_id':execution.get('run',execution.get('Run')),
                         'seconds':execution.get('seconds',execution.get('Seconds'))},
            'restore':asset.get('restore') or {'status':'untested'},'diagnosis':asset.get('diagnosis'),
            'retention':{'keep_cycles':asset.get('keep'),'configuration_audited':False}}

def convert(snapshot):
    return {'schema_version':SCHEMA,'sampled_at':snapshot.get('sampled'),'collecting':snapshot.get('collecting',False),
            'assets':[normalize(a,snapshot.get('sampled')) for a in snapshot['assets']],
            'storages':snapshot.get('targets',[])}

def report(data):
    labels={'pending_verification':'文件已到位，待MD5复核','available':'近期副本齐全','partial':'副本不齐或日期不一致','unavailable':'缺少近期合格收据','passed':'技术恢复验证通过','untested':'未做恢复验证','running':'执行中','failed':'失败','success':'成功','unknown':'采集异常，无法确认'}
    rows=['# 核心资产备份日报','',f"采集时间：{data.get('sampled_at')}",'',
          '以结构化收据为依据；副本存在、MD5通过和实际恢复通过分别展示。','',
          '|资产|近期副本覆盖|最近执行|耗时秒|恢复验证|','|---|---|---|---|---|']
    if data.get('collecting'):rows.insert(4,'本轮仍在采集中，缺失状态暂不作为最终失败结论。')
    for a in data['assets']:
        clean=lambda s:str(s if s is not None else '未知').replace('|','/').replace('\n',' ')
        rows.append('|'+ '|'.join(map(clean,[a['name'],labels.get(a['coverage'],a['coverage']),labels.get(a['execution']['status'],a['execution']['status']),a['execution']['seconds'],labels.get(a['restore']['status'],a['restore']['status'])]))+'|')
    rows+=['','## 优先处理','']
    for a in data['assets']:
        if a['coverage']!='available':rows.append('- '+a['name']+'：'+labels[a['coverage']]+('；'+str(a['diagnosis'].get('summary','连接详情待采集')) if a.get('diagnosis') else '。'))
    rows+=['','未验证项保持未验证；本文不证明业务验收完成。']
    return '\n'.join(rows)+'\n'

def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['status','report']);p.add_argument('--snapshot',default='/var/lib/backup-console/snapshot.json');p.add_argument('--output');a=p.parse_args()
    data=convert(json.loads(Path(a.snapshot).read_text(encoding='utf-8-sig')))
    text=report(data) if a.action=='report' else json.dumps(data,ensure_ascii=False,indent=2)
    if a.output:
        os.umask(0o077);dest=Path(a.output);tmp=dest.with_suffix(dest.suffix+'.tmp');tmp.write_text(text,encoding='utf-8');tmp.replace(dest)
    else:print(text)
if __name__=='__main__':main()
