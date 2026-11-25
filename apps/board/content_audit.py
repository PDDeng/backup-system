"""Projection of independently recorded content audits, never receipt promotion."""
import datetime as dt,re

def verified_at(report,asset,run,target,files,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    try:
        if report['status']!='passed' or report['asset']!=asset or report['run']!=run:return None
        stamp=dt.datetime.fromisoformat(report['finished'])
        if stamp.tzinfo is None or not -300<=(now-stamp).total_seconds()<=86400:return None
        rows=[r for r in report['results'] if r['target']==target]
        if len(rows)!=1:return None
        row=rows[0];hashes=row['md5']
        if row['status']!='verified' or row['run']!=run or row['files']!=len(files) or set(hashes)!=set(files):return None
        if not all(isinstance(v,str) and re.fullmatch('[a-f0-9]{32}',v) for v in hashes.values()):return None
        return stamp.isoformat()
    except (KeyError,TypeError,ValueError,AttributeError):return None
