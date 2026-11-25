"""Reversible Harbor 2.13 maintenance through documented configuration/queue APIs."""
import time


def read_only_value(data):
    value=data.get('read_only')
    if isinstance(value,dict):value=value.get('value')
    if type(value) is not bool:raise ValueError('Unknown read_only API response')
    return value


class Maintenance:
    def __init__(self,api,path,atomic,log,timeout=300,sleep=time.sleep):
        self.api,self.path,self.atomic,self.log=api,path,atomic,log
        self.timeout,self.sleep=timeout,sleep
        self.state=None

    def save(self):self.atomic(self.path,self.state)

    def inspect(self):
        readonly=read_only_value(self.api('/configurations'))
        scheduler=self.api('/schedules/all/paused')
        if type(scheduler.get('paused')) is not bool:raise ValueError('Unknown scheduler pause state')
        queues=self.api('/jobservice/queues')
        if not isinstance(queues,list) or not queues:raise ValueError('Unknown/empty queue response; cannot establish pause coverage')
        for queue in queues:
            if not isinstance(queue,dict) or type(queue.get('paused')) is not bool:
                raise ValueError('Unknown queue pause state')
        states={q['paused'] for q in queues}
        if len(states)>1:raise ValueError('Mixed queue pause states; preserve existing settings, no changes made')
        return {'read_only':readonly,'scheduler_paused':scheduler['paused'],'queues':queues,
                'queues_paused':bool(states and True in states)}

    def enter(self):
        if self.path.exists():
            import json
            old=json.loads(self.path.read_text())
            if old.get('active'):raise ValueError('Unrestored maintenance state exists; run --restore-only first')
        original=self.inspect()
        self.state={'active':True,'original':original,'readonly_attempted':False,'pause_attempted':False}
        self.save()
        # Queue mutations can be rejected once Harbor is read-only.
        # Never temporarily enable writes if the operator already made it read-only.
        if original['read_only'] and not original['queues_paused']:
            self.state['active']=False;self.save()
            raise ValueError('Originally read-only with active queues; no settings changed. Operator coordination required')
        if not original['queues_paused']:
            self.state['pause_attempted']=True;self.save()
            self.api('/jobservice/queues/all','PUT',{'action':'pause'})
        self.require_paused()
        if not original['read_only']:
            self.state['readonly_attempted']=True;self.save()
            self.api('/configurations','PUT',{'read_only':True})
        self.require_readonly()
        deadline=time.monotonic()+self.timeout
        while True:
            workers=self.api('/jobservice/pools/all/workers')
            if not isinstance(workers,list) or any(not isinstance(w,dict) for w in workers):
                raise ValueError('Unknown worker response; backup blocked')
            busy=[w for w in workers if w.get('job_id') or w.get('job_name')]
            if not busy:break
            if time.monotonic()>=deadline:raise ValueError('Running Harbor jobs did not drain; no jobs forcibly stopped')
            self.log('WAIT_ACTIVE_JOBS '+str(len(busy)));self.sleep(10)
        # Allow requests that began before readonly was enabled to settle.
        self.log('WRITES_PAUSED; settling in-flight requests for 30 seconds')
        self.sleep(30)
        self.require_paused();self.require_readonly()

    def require_readonly(self):
        if not read_only_value(self.api('/configurations')):raise ValueError('Harbor readonly lost')

    def require_paused(self):
        queues=self.api('/jobservice/queues')
        if not isinstance(queues,list) or any(q.get('paused') is not True for q in queues):
            raise ValueError('Background queues not paused; backup blocked')

    def restore(self):
        if self.state is None:
            if not self.path.exists():return
            import json
            self.state=json.loads(self.path.read_text())
        if not self.state.get('active'):return
        errors=[]
        if self.state.get('readonly_attempted'):
            try:
                original=self.state['original']['read_only']
                self.api('/configurations','PUT',{'read_only':original})
                if read_only_value(self.api('/configurations')) != original:raise ValueError('Readonly restoration not confirmed')
                self.state['readonly_attempted']=False;self.save()
            except Exception as exc:errors.append('readonly: '+str(exc))
        if self.state.get('pause_attempted'):
            try:
                self.api('/jobservice/queues/all','PUT',{'action':'resume'})
                queues=self.api('/jobservice/queues')
                if not isinstance(queues,list) or any(q.get('paused') is not False for q in queues):raise ValueError('Queue resume not confirmed')
                self.state['pause_attempted']=False;self.save()
            except Exception as exc:errors.append('queues: '+str(exc))
        if errors:
            self.state['restore_errors']=errors;self.save()
            raise RuntimeError('CRITICAL RESTORE FAILED: '+'; '.join(errors))
        self.state['active']=False;self.save();self.log('ORIGINAL_WRITE_AND_QUEUE_STATE_RESTORED')
