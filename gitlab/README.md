# Core Backup Platform 2.0

Read the bundled deployment and operations manual (Chinese Markdown) first.

- Source GitLab scripts: client/
- Collection: run.py, backup-all-smb.py
- SMB retention: keep_days / cleanup_mode in each opsNNN.json
- Speed mode still performs retention using existence/size and ownership receipts.
- Weekly history MD5: verify-history.py (read only, no receipt promotion)
- Web console: portal.py + dashboard.html; loopback port 8088, bearer token.
- systemd: core-backup-portal.service
- Register additional GitLab: register-client.py (refuses overwrite)

No real credentials are bundled. notify.example.json and client/notify.json are disabled placeholders.
Oracle, SVN, Nexus and Harbor execution adapters are not implemented in this GitLab console.
Production Linux SSH/SMB deployment verification remains necessary.
