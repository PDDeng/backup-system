"""Known read-only Oracle inventory queries through Windows local OS authentication."""
import base64,sys
from windows_transport import session,decode
asset=sys.argv[1]
if asset not in ('oracle41','oracle46'):raise SystemExit('Known Oracle asset required')
sid,home=('YUANQI','dbhome_2') if asset=='oracle41' else ('ORCL','dbhome_1')
sql="""set pages 100 lines 240
select instance_name,status from v$instance;
select log_mode from v$database;
select file_name,round(bytes/1024/1024) mb,autoextensible,round(maxbytes/1024/1024) max_mb from dba_data_files where tablespace_name='UNDOTBS1';
select to_char(begin_time,'YYYY-MM-DD HH24:MI') t,undoblks,tuned_undoretention,maxquerylen,ssolderrcnt,nospaceerrcnt from v$undostat where begin_time>sysdate-1/24 order by begin_time;
exit
"""
encoded=base64.b64encode(sql.encode('ascii')).decode()
home='D:\\app\\administrator\\product\\11.2.0\\'+home
batch='@echo off\r\nset ORACLE_SID='+sid+'\r\nset ORACLE_HOME='+home+'\r\n"'+home+'\\bin\\sqlplus.exe" -L -S "/ as sysdba" @C:\\ProgramData\\CoreBackupScheduleAudit\\readonly-inventory.sql\r\n'
bat=base64.b64encode(batch.encode('ascii')).decode()
script="[IO.File]::WriteAllBytes('C:\\ProgramData\\CoreBackupScheduleAudit\\readonly-inventory.sql',[Convert]::FromBase64String('"+encoded+"'));[IO.File]::WriteAllBytes('C:\\ProgramData\\CoreBackupScheduleAudit\\readonly-inventory.cmd',[Convert]::FromBase64String('"+bat+"'));[Console]::WriteLine('PREPARED')"
s=session(asset);r=s.run_ps(script)
if r.status_code or b'PREPARED' not in r.std_out:raise SystemExit('Read-only query preparation not confirmed')
r=s.run_cmd('cmd.exe',['/d','/c','C:\\ProgramData\\CoreBackupScheduleAudit\\readonly-inventory.cmd'])
print('SQL_RC='+str(r.status_code));print(decode(r.std_out));print(decode(r.std_err))
raise SystemExit(r.status_code)
