"""Explicit PowerShell path for old Windows hosts with a damaged PATH."""
import base64,sys
from pathlib import Path
sys.path.insert(0,'/opt/core-backup-platform')
from windows_transport import session,decode
asset,script=sys.argv[1:]
source=Path(script).read_text(encoding='utf-8-sig')
encoded=base64.b64encode(source.encode('utf-16-le')).decode()
response=session(asset).run_cmd(r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
    ['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-EncodedCommand',encoded])
print(decode(response.std_out))
if response.status_code:print(decode(response.std_err))
raise SystemExit(response.status_code)
