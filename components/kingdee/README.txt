PowerShell 2.0 compatibility edition

Extract backup.ps1, config.ps1 and json-compat.ps1 into C:\core-kingdee-backup.
Keep your existing config.ps1 if you have already edited it.
Run all commands with the Windows account used by the scheduled task.

1. Save NAS credentials (only once):
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\core-kingdee-backup\backup.ps1 -SetupCredentials

2. Check source date/files and NAS create/read/delete access, without upload or expiry deletion:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\core-kingdee-backup\backup.ps1 -CheckOnly

3. Upload latest filename date and retain latest 15 managed complete dates:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\core-kingdee-backup\backup.ps1

Task Scheduler: daily 03:00; same Windows account; run whether logged on or not.
Program: powershell.exe
Arguments: -NoProfile -ExecutionPolicy Bypass -File C:\core-kingdee-backup\backup.ps1
Start in: C:\core-kingdee-backup
Do not start another instance. Save Windows account password in Task Scheduler.

Source is scanned at the first level only. Select all *_YYYYMMDD.ADF for the newest date.
Files must be nonempty, older than MinimumAgeMinutes, and stable for StableSeconds.
Copy uses read-only file handles denying writes to these selected ADF files until publication.
Schedule after Kingdee export has completed. Never point this at live database files.
Completeness means all selected files passed size checks; no MD5 or restore verification.
Missing companies cannot be detected without a configured expected company list.
Only owned complete date directories are eligible for expiry. Unknown/extra files block expiry.
Partials are retained on failure. Source files are never deleted.
Logs and deletion audits are kept in logs. Credentials are DPAPI encrypted for the saving user.
Webhook is optional; old Windows TLS limitations can cause NOTIFY_FAILED, recorded locally.
No system or Oracle changes required. JSON helper has no external assembly dependencies.
Syntax and JSON checks were run on Windows PowerShell 5.1; actual PS2/NAS checks must be run on the target.
