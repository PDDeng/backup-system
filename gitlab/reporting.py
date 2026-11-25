"""Human-readable final results. Progress stays in local logs."""
def duration(value):
    seconds=int(value or 0)
    return '%d分%02d秒'%(seconds//60,seconds%60)

def summary(rows,seconds,title='GitLab备份分发',action='upload'):
    good=sum(r.get('status')=='success' for r in rows.values())
    label='全部成功' if good==len(rows) else ('部分失败' if good else '失败')
    if action=='check':title='备份环境检查（未执行备份）'
    lines=['【'+title+'｜'+label+'】','结果：%d/%d成功；总耗时：%s'%(good,len(rows),duration(seconds))]
    for sid,r in sorted(rows.items(),key=lambda item:item[1].get('status')=='success'):
        d=r.get('delivery',r)
        targets=d.get('targets',{})
        # A row with current source/target check errors must never be replaced by old delivery success.
        if r.get('status')!='success':
            error=r.get('error','未完成')
            source=r.get('source',{})
            if source.get('error'):error+='；'+source['error']
            failed_details=[k+':'+v.get('error','失败') for k,v in d.get('target_details',{}).items() if v.get('status')!='success']
            if failed_details:error+='；'+'；'.join(failed_details)
            bad=[t['id']+':'+t.get('error','失败') for t in r.get('targets',[]) if isinstance(t,dict) and t.get('status')!='ok'] if isinstance(r.get('targets'),list) else []
            detail=str(error)+('；'+'；'.join(bad) if bad else '')
            if len(detail.encode('utf-8'))>220:detail=detail.encode('utf-8')[:210].decode('utf-8','ignore')+'…'
            lines.append('❌ '+sid+'：'+detail)
            continue
        line='✅ '+sid
        if isinstance(targets,dict):line+='：'+' / '.join(k.replace('nas','').replace('win','')+('成功' if v else '失败') for k,v in targets.items())
        lines.append(line)
        if d.get('run'):lines.append('  批次：'+d['run'])
        size=r.get('source',{}).get('backup_bytes')
        if size is not None:lines.append('  大小：%.2f GiB'%(size/1024**3))
        if action!='check':
            verified=d.get('verification')=='md5_verified'
            details=d.get('target_details',{})
            deleted=sum(t.get('deleted_runs',0) for t in details.values()) if not r.get('unchanged') else r.get('cleanup_runs',0)
            lines.append('  校验：'+('MD5通过' if verified else '仅存在/大小，未做MD5')+'；清理：'+str(deleted)+'个运行目录；耗时：'+duration(r.get('seconds')))
    lines.append('说明：上传成功不代表已完成恢复演练。' if action!='check' else '检查通过不代表已生成或上传新备份。')
    return '\n'.join(lines)
