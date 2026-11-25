"""Read native restore evidence and live service handles; never start/stop a job."""
import datetime,json,subprocess
from pathlib import Path

def read(path):
 try:return json.loads(path.read_text())
 except (OSError,ValueError):return None

def collect():
 root=Path('/var/lib/core-backup-restore/harbor248/20261124')
 phases={k:read(root/(k+'-result.json')) for k in ('registry','database','configuration')}
 phases['artifact_storage']=read(root/'artifact-storage-check.json')
 phases['service_integration']=read(root/'service-runtime-v2/service-integration-result.json')
 jobs={}
 for phase,unit in [('registry','harbor248-registry-restore-20261124'),('configuration','harbor248-configuration-restore-20261124'),('image','harbor248-restore-image-20261124'),('artifact_storage','harbor248-artifact-storage-check-20261124'),('database','harbor248-database-readiness-resume-20261124')]:
  p=subprocess.run(['systemctl','show',unit,'-p','ActiveState','-p','MainPID','-p','ExecMainStatus'],capture_output=True,text=True,timeout=10)
  jobs[phase]=dict(x.split('=',1) for x in p.stdout.splitlines() if '=' in x)
 running=any(x.get('ActiveState')=='active' and x.get('MainPID','0')!='0' for x in jobs.values())
 failed=any(x and x.get('status')=='failed' for x in phases.values())
 # Success is restricted to the native and API/Registry HTTP evidence below.
 passed=all(x and x.get('status')=='passed' for x in phases.values())
 result={'sampled':datetime.datetime.now().astimezone().isoformat(),'status':'running' if running else 'failed' if failed else 'passed' if passed else 'incomplete','scope':'isolated storage SHA256, PostgreSQL restore, configuration and artifact links; administrator API login and one complete Registry V2 image transfer passed. Jobservice/replication, Docker daemon pull and image execution are not tested','phases':phases,'live_jobs':jobs,'service_integration':phases['service_integration'], 'untested':['jobservice/replication','Docker daemon pull','image execution'],'deletion':'not_performed'}
 dest=Path('/var/lib/backup-console/restore/harbor248.json');dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700);tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2));tmp.replace(dest)
 print('Harbor restore observation: '+result['status'])
if __name__=='__main__':collect()
