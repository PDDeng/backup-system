"""Human business acceptance; independent of automatic integrity evidence."""
import datetime as dt

def validate(body, assets):
    def field(key, maximum, minimum=1):
        value=body.get(key)
        if not isinstance(value,str) or not minimum<=len(value.strip())<=maximum:
            raise ValueError('字段不完整或过长：'+key)
        return value.strip()
    asset=field('asset_id',100);run=field('backup_run',160)
    matches=[a for a in assets if a.get('id')==asset]
    if len(matches)!=1:raise ValueError('资产不存在或证据不可用')
    if run not in {c.get('run') for c in matches[0].get('copies',[]) if c.get('arrived')}:
        raise ValueError('该批次没有当前可核实的副本；不能提交验收')
    status=field('status',10)
    if status not in ('passed','failed'):raise ValueError('验收结果无效')
    tested=field('tested_at',64)
    try:
        stamp=dt.datetime.fromisoformat(tested)
        if stamp.tzinfo is None or stamp>dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=5):raise ValueError()
    except ValueError:raise ValueError('测试时间须含时区且不能在未来')
    return dict(asset_id=asset,backup_run=run,status=status,tested_at=stamp.isoformat(),
        scope=field('scope',1000),steps=field('steps',3000),actual=field('actual',3000),
        evidence=field('evidence',2000),untested=field('untested',1500))
