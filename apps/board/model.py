"""Evidence-only projection. Delivery claims cannot replace published copy evidence."""
import datetime as dt

def project(snapshot,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    try:age=(now-dt.datetime.fromisoformat(snapshot['sampled'])).total_seconds()
    except (KeyError,ValueError,TypeError):age=None
    stale=age is None or age>1800 or age< -300
    assets=[]
    for a in snapshot.get('assets',[]):
        copies=[];issues=[];expected=a.get('targets',[])
        for tid in expected:
            c=next((c for c in a.get('copies',[]) if c.get('target')==tid),{})
            transfer=next((x for x in a.get('transfers',[]) if x.get('target')==tid and x.get('status') in ('files_size_rechecked','files_md5_rechecked')),None)
            prior_verified=c.get('date')
            if transfer and transfer.get('date','')>=c.get('date',''):c=transfer
            days=None
            try:days=(now.astimezone(dt.timezone(dt.timedelta(hours=8))).date()-dt.date.fromisoformat(c['date'])).days
            except (KeyError,ValueError,TypeError):pass
            limit=a.get('max_age_days',8 if a.get('type')=='Harbor' else 2)
            recent=days is not None and 0<=days<=limit
            ok=c.get('status') in ('receipt_found','files_md5_rechecked') and recent
            pending=c.get('status')=='files_size_rechecked' and recent
            note='近期完成记录' if ok else ('文件大小已核对，MD5待复核' if pending else ('记录过期' if days is not None and days>limit else '缺少有效完成记录'))
            if not ok and not pending:issues.append(tid+'：'+note)
            copies.append(dict(target=tid,ok=ok,arrived=ok or pending,pending_md5=pending,date=c.get('date'),run=c.get('run'),verification=c.get('verification','未记录'),note=note,partials=c.get('partials',0),last_published_date=prior_verified))
        if not expected:issues.append('目标尚未接入')
        execution=a.get('execution') or {}
        erun=execution.get('run',execution.get('Run'))
        if erun and any(c.get('run') and c['run']!=erun for c in copies):issues.append('执行批次与存储完成记录不一致，需核查采集')
        diagnosis=a.get('diagnosis') or {}
        if diagnosis.get('status') not in (None,'ok'):issues.append('源端连接检查未通过')
        r=a.get('restore') or {}
        assets.append(dict(id=a.get('id'),name=a.get('name'),type=a.get('type'),host=a.get('host'),os=a.get('os'),
            state='attention' if issues else ('pending_verification' if any(c['pending_md5'] for c in copies) else 'protected'),issues=issues,copies=copies,
            restore=dict(status=r.get('status','not_tested'),finished=r.get('finished'),scope=r.get('scope'),phases=r.get('phases')),
            schedule=a.get('schedule'),seconds=execution.get('source_backup_seconds',execution.get('seconds')),
            connection=diagnosis.get('status','unknown'),disk=diagnosis.get('disk'),retention=a.get('retention'),note=a.get('note')))
    return dict(sampled=snapshot.get('sampled'),stale=stale,collecting=snapshot.get('collecting',False),assets=assets,
        targets=snapshot.get('targets',[]),summary=dict(total=len(assets),protected=sum(a['state']=='protected' for a in assets),delivered=sum(a['state'] in ('protected','pending_verification') for a in assets),pending_verification=sum(a['state']=='pending_verification' for a in assets),
        attention=sum(a['state']=='attention' for a in assets),restore_passed=sum(a['restore']['status']=='passed' for a in assets)))
