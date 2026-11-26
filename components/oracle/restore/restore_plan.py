"""Offline Oracle restore plan/evidence validation. Never connects to a database.

Python 3 runs on ops; generated ASCII SQL and PS2 worker run on the isolated VM.
Manifests are operator-reviewed evidence, not a claim that a restore has passed.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import ntpath
import os
import re
from pathlib import Path
import uuid

IDS = {"oracle46": "11.2.0.1.0", "oracle41": "11.2.0.4.0"}
IDENT = re.compile(r"^[A-Z][A-Z0-9_]{0,29}$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
HEX = re.compile(r"^[a-f0-9]{64}$")
RESTORE_ROOTS = ("c:\\core-backup\\restore", "d:\\oracle-restore")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and IDENT.fullmatch(value), "unsafe SQL identifier")
    return value


def local_path(value):
    require(isinstance(value, str) and re.fullmatch(r"[C-Z]:\\[A-Za-z0-9_\\.-]+", value),
            "local absolute ASCII path required (C:..Z:)")
    require(all(p not in ("", ".", "..") for p in value[3:].split("\\")), "invalid path segment")
    return ntpath.normpath(value)


def below(path, root):
    if ntpath.splitdrive(path)[0].lower() != ntpath.splitdrive(root)[0].lower():
        return False
    return ntpath.commonpath([path.lower(), root.lower()]) == root.lower() and path.lower() != root.lower()


def validate(config, manifest):
    asset = config["id"]
    require(asset in IDS and manifest.get("id") == asset, "asset mismatch")
    require(config.get("target_ip") == "10.10.10.251", "test host must be 10.10.10.251")
    host = config.get("target_hostname", "")
    require(re.fullmatch(r"[A-Za-z0-9-]{1,63}", host) is not None, "test hostname required")
    sid = config["sid"]
    require(sid == ("RT46" if asset == "oracle46" else "RT41"), "dedicated test SID required")
    root = local_path(config["root"])
    require(any(below(root, base) for base in RESTORE_ROOTS), "dedicated C:\\core-backup\\restore or D:\\oracle-restore child required")
    home = local_path(config["oracle_home"])
    require(not below(root, home) and not below(home, root) and root.lower() != home.lower(), "home/root overlap")
    target_version = config.get("target_version", IDS[asset])
    require(target_version in (("11.2.0.1.0", "11.2.0.4.0") if asset == "oracle46" else ("11.2.0.4.0",)), "unsupported target Oracle version")
    source = config["source"]
    require(isinstance(source, str) and source.startswith("\\\\10.10.11.243\\资源备份\\"), "approved source NAS243 only")
    parts = source[2:].split("\\")
    require(all(p and p not in (".", "..") and not any(c in p for c in "'\r\n*?<>|\"") for p in parts), "unsafe source")
    require(not any(p.lower().endswith(".partial") for p in parts), "partial source refused")
    expected_asset = "oracle01_10.10.10.46_dev研发环境数据库" if asset == "oracle46" else "oracle02_10.10.10.41_uat测试环境数据库"
    require(len(parts) == 5 and parts[2] == expected_asset, "source must be asset/date/run")
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[3]) is not None, "source date required")
    dt.date.fromisoformat(parts[3])
    require(TOKEN.fullmatch(manifest.get("run", "")) is not None and parts[4] == manifest["run"], "run mismatch")
    require(manifest.get("source") == source and manifest.get("version") == IDS[asset], "source/version mismatch")
    require(manifest.get("status") == "complete" and manifest.get("native_exit_code") == 0
            and type(manifest.get("native_exit_code")) is int, "clean completed backup required")
    files = manifest.get("files")
    require(isinstance(files, list) and len(files) > 0, "nonempty files array required")
    names = set()
    for f in files:
        name = f.get("name", "")
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,150}", name) is not None and name not in (".", ".."), "unsafe file name")
        require(name.lower() not in names, "duplicate file")
        names.add(name.lower())
        require(type(f.get("bytes")) is int and f["bytes"] > 0, "positive byte count required")
        require(HEX.fullmatch(f.get("sha256", "")) is not None, "SHA256 required")
        require(f.get("role") in ("dump", "piece", "controlfile", "export_log", "rman_log"), "unknown file role")
    if asset == "oracle46":
        require(any(f["role"] == "dump" for f in files) and all(f["name"].upper().endswith(".DMP") for f in files if f["role"] == "dump"), "dump set missing/invalid")
        require(sum(f["role"] == "export_log" for f in files) == 1, "one export log required")
        require(all(f["role"] in ("dump", "export_log") for f in files), "unexpected logical file")
    else:
        require(any(f["role"] == "piece" for f in files) and any(f["role"] == "controlfile" for f in files), "RMAN piece/controlfile missing")
        require(sum(f["role"] == "rman_log" for f in files) == 1, "one RMAN log required")
        require(all(f["role"] in ("piece", "controlfile", "rman_log") for f in files), "unexpected RMAN file")
        require(type(manifest.get("dbid")) is int and manifest["dbid"] > 0, "source DBID required")
    mode = config["mode"]
    require(mode in (("sample", "full") if asset == "oracle46" else ("rman",)), "invalid restore mode")
    checks = config.get("tables")
    require(isinstance(checks, list) and checks, "at least one business table check required")
    for t in checks:
        identifier(t["owner"]); identifier(t["table"])
        require(type(t.get("minimum_rows")) is int and t["minimum_rows"] >= 1, "positive row floor required")
    if asset == "oracle46":
        mappings = config.get("schema_map", {})
        require(isinstance(mappings, dict) and mappings, "reviewed schema map required")
        for old, new in mappings.items():
            identifier(old); identifier(new)
            require(old not in ("SYS", "SYSTEM", "SYSMAN", "EXFSYS") and new.startswith("REST_"), "business schemas to REST_* only")
        require(len(set(mappings.values())) == len(mappings), "duplicate mapped schema")
        require(all(t["owner"] in mappings.values() for t in checks), "table owner must be mapped schema")
        identifier(config["tablespace"])
        require(config["tablespace"].startswith("REST_"), "dedicated REST_* tablespace required")
        require(config.get("ddl_reviewed") is True, "SQLFILE/DDL review acknowledgement required")
    return root, sid


def guard_sql(c):
    # Machine binding is also checked by PS, database identity/paths before any import.
    root = c["root"].upper() + "\\"
    return """whenever sqlerror exit failure rollback
whenever oserror exit failure rollback
set heading off feedback off pages 0 lines 1000
declare n number; begin
 select count(*) into n from v$instance where upper(instance_name)='%s' and upper(host_name)='%s' and version='%s';
 if n<>1 then raise_application_error(-20001,'WRONG_INSTANCE'); end if;
 select count(*) into n from v$database where name='%s' and database_role='PRIMARY' and open_mode='READ WRITE';
 if n<>1 then raise_application_error(-20002,'WRONG_DATABASE'); end if;
 select count(*) into n from (select name p from v$datafile union all select name from v$tempfile union all select member from v$logfile union all select name from v$controlfile) where substr(upper(p),1,length('%s'))<>'%s';
 if n<>0 then raise_application_error(-20003,'PATH_OUTSIDE_ISOLATION'); end if;
 select count(*) into n from v$parameter where name='job_queue_processes' and value='0';
 if n<>1 then raise_application_error(-20004,'JOBS_NOT_DISABLED'); end if;
end;
/
""" % (c["sid"], c["target_hostname"].upper(), c.get("target_version", IDS[c["id"]]), c["sid"], root, root)


def plan(c, m):
    c = dict(c)
    c.setdefault("target_version", IDS[c["id"]])
    root, sid = validate(c, m)
    stage = root + "\\stage"
    result = {"guard.sql": guard_sql(c) + "exit success\n"}
    verify = guard_sql(c)
    verify += "select 'DATABASE|'||name||'|'||open_mode||'|'||dbid from v$database;\n"
    verify += "select 'FILE|'||name from v$datafile;\nselect 'CONTROLFILE|'||name from v$controlfile;\n"
    verify += "select 'INVALID|'||owner||'|'||object_name||'|'||object_type from dba_objects where status='INVALID';\n"
    owners = sorted(set(t["owner"] for t in c["tables"]))
    verify += "declare n number; begin\n"
    for t in c["tables"]:
        verify += " select count(*) into n from %s.%s; if n<%d then raise_application_error(-20010,'TABLE_ROW_FLOOR'); end if; dbms_output.put_line('TABLE|%s.%s|'||n);\n" % (t["owner"], t["table"], t["minimum_rows"], t["owner"], t["table"])
    verify += " select count(*) into n from dba_objects where status='INVALID' and owner in (%s); if n<>0 then raise_application_error(-20011,'BUSINESS_INVALID_OBJECTS'); end if; end;\n/\n" % ",".join("'" + o + "'" for o in owners)
    verify = "set serveroutput on\n" + verify + "prompt RESTORE_VERIFY_OK\nexit success\n"
    result["verify.sql"] = verify
    if c["id"] == "oracle46":
        # No full-source DDL allowed to create original directories/tablespaces/jobs/links.
        result["prepare.sql"] = guard_sql(c) + "create directory RESTORE_DUMP as '%s';\nexit success\n" % stage
        par = ['USERID="/ AS SYSDBA"', 'DIRECTORY=RESTORE_DUMP', 'DUMPFILE=' + ",".join(f["name"] for f in m["files"] if f["role"] == "dump"), 'LOGFILE=import.log', 'PARALLEL=1', 'TABLE_EXISTS_ACTION=SKIP', 'EXCLUDE=TABLESPACE,DIRECTORY,DB_LINK,PROCOBJ', 'REMAP_TABLESPACE=%:' + c["tablespace"]]
        # 11g does not support the % wildcard REMAP_TABLESPACE: explicit source list.
        par.pop()
        sources = c.get("source_tablespaces", [])
        require(isinstance(sources, list) and sources, "explicit source tablespaces required")
        for ts in sources:
            identifier(ts)
            require(ts not in ("SYSTEM", "SYSAUX", "TEMP", "UNDOTBS1"), "system tablespace remap refused")
            par.append("REMAP_TABLESPACE=" + ts + ":" + c["tablespace"])
        par.append("FULL=YES" if c["mode"] == "full" else "SCHEMAS=" + ",".join(c["schema_map"]))
        for old, new in c["schema_map"].items():
            par.append("REMAP_SCHEMA=" + old + ":" + new)
        result["import.par"] = "\n".join(par) + "\n"
        result["preview.par"] = result["import.par"] + "SQLFILE=preview.sql\n"
        # Refuse already-existing mapped users: fresh test instance, no mixed prior data.
        result["guard.sql"] = guard_sql(c) + "declare n number; begin select count(*) into n from dba_users where username in (%s); if n<>0 then raise_application_error(-20012,'MAPPED_USERS_ALREADY_EXIST'); end if; end;\n/\nexit success\n" % ",".join("'" + x + "'" for x in c["schema_map"].values())
    else:
        verify_dbid = "declare n number; begin select count(*) into n from v$database where dbid=%d; if n<>0 then raise_application_error(-20013,'SOURCE_DBID_REUSED'); end if; select count(*) into n from v$database_block_corruption; if n<>0 then raise_application_error(-20014,'BLOCK_CORRUPTION'); end if; end;\n/\n" % m["dbid"]
        result["verify.sql"] = result["verify.sql"].replace("prompt RESTORE_VERIFY_OK", verify_dbid + "prompt RESTORE_VERIFY_OK")
        data = root + "\\data"
        result["init.ora"] = "db_name='%s'\ncompatible='11.2.0.4.0'\ncontrol_files='%s\\control01.ctl'\ndb_create_file_dest='%s'\ndb_recovery_file_dest='%s\\fra'\ndb_recovery_file_dest_size=200G\njob_queue_processes=0\ndg_broker_start=false\nremote_listener=''\nlocal_listener=''\nsga_target=1G\npga_aggregate_target=256M\n" % (sid, data, data, root)
        result["startup.sql"] = "whenever sqlerror exit failure\nwhenever oserror exit failure\nstartup nomount pfile='%s\\init.ora';\nexit success\n" % root
        result["duplicate.rman"] = "RUN {\n SET NEWNAME FOR DATABASE TO NEW;\n DUPLICATE DATABASE TO %s BACKUP LOCATION '%s'\n LOGFILE GROUP 1 ('%s\\redo01.log') SIZE 100M, GROUP 2 ('%s\\redo02.log') SIZE 100M;\n}\nEXIT;\n" % (sid, stage, data, data)
        result["validate.rman"] = "VALIDATE CHECK LOGICAL DATABASE;\nEXIT;\n"
    result["plan.json"] = json.dumps({"schema_version": 1, "production_change": False, "config": c, "manifest": m}, ensure_ascii=True, indent=2) + "\n"
    return result


@contextlib.contextmanager
def output_lock(directory, asset):
    with (directory / (asset + ".lock")).open("a+b") as handle:
        handle.seek(0, 2)
        if not handle.tell():
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def parse_timestamp(value):
    """RFC3339, including .NET round-trip 100ns fractions, on Python 3.8+."""
    require(isinstance(value, str), "timestamp must be a string")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})", value)
    require(match is not None, "timezone-qualified ISO timestamp required")
    fraction = ("." + match[2][:6]) if match[2] else ""
    zone = "+00:00" if match[3] == "Z" else match[3]
    return dt.datetime.fromisoformat(match[1] + fraction + zone).astimezone(dt.timezone.utc)


def publish(evidence, out_dir, evidence_dir=None):
    """Import a completed worker receipt only; does not invent missing phases."""
    asset = evidence.get("id")
    require(asset in IDS and evidence.get("schema_version") == 1, "unknown result contract")
    require(evidence.get("production_change") is False and evidence.get("target_ip") == "10.10.10.251", "wrong result target")
    require(TOKEN.fullmatch(evidence.get("run", "")) is not None, "result run missing")
    require(evidence.get("status") in ("passed", "failed", "partial"), "invalid status")
    require(HEX.fullmatch(evidence.get("plan_sha256", "")) is not None, "plan binding missing")
    require(evidence.get("finished") and evidence.get("scope"), "result metadata missing")
    finished = parse_timestamp(evidence["finished"])
    result = dict(evidence)
    result["finished"] = finished.isoformat(timespec="microseconds")
    # sampled is the observation/publication time; finished remains actual restore time.
    result["sampled"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    untested = evidence.get("untested", [])
    require(isinstance(untested, list) and all(isinstance(x, str) and x.strip() for x in untested), "untested must be a string array")
    result["untested"] = list(dict.fromkeys(untested + ["business_acceptance", "application_connectivity"]))
    mode = evidence.get("mode", "")
    if asset == "oracle46" and (mode == "sample" or "sample" in evidence["scope"].lower()):
        result["untested"] += [x for x in ("full_database_restore", "tables_outside_selected_schemas") if x not in result["untested"]]
    phases = evidence.get("phases", {})
    required = ["source_sha256", "isolation", "native_restore", "table_checks"]
    if asset == "oracle41":
        required.append("rman_validate")
    result["untested"] += [name for name in required if name not in phases and name not in result["untested"]]
    if asset == "oracle46" and "excluded_environment_metadata" not in result["untested"]:
        result["untested"].append("excluded_environment_metadata")
    if evidence["status"] == "passed":
        require(all(isinstance(phases.get(p), dict) and phases[p].get("status") == "passed" and phases[p].get("log") for p in required), "missing restore evidence")
        require(evidence_dir is not None, "local evidence directory required for passed receipt")
    for phase in phases.values():
        require(isinstance(phase, dict) and phase.get("status") == "passed", "unexpected phase")
        name = phase.get("log", "")
        require(re.fullmatch(r"[a-z][a-z0-9-]*\.log", name) is not None, "unsafe evidence log")
        require(HEX.fullmatch(phase.get("sha256", "")) is not None, "log digest missing")
        require(evidence_dir is not None, "evidence files required")
        log = Path(evidence_dir) / name
        require(log.is_file() and not log.is_symlink(), "evidence log missing/symlink")
        require(hashlib.sha256(log.read_bytes()).hexdigest() == phase["sha256"], "evidence log digest mismatch")
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True, mode=0o700)
    dest = out / (asset + ".json")
    with output_lock(out, asset):
        if dest.exists():
            previous = json.loads(dest.read_text(encoding="utf-8"))
            previous_time = parse_timestamp(previous["finished"])
            require(previous_time.tzinfo is not None and finished >= previous_time, "older receipt must not replace newer observation")
        temp = out / (asset + "." + uuid.uuid4().hex + ".tmp")
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=True, indent=2))
            stream.flush(); os.fsync(stream.fileno())
        temp.replace(dest)
    return dest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    b = sub.add_parser("plan"); b.add_argument("--config", required=True); b.add_argument("--manifest", required=True); b.add_argument("--output", required=True)
    r = sub.add_parser("publish"); r.add_argument("--receipt", required=True); r.add_argument("--output", default="/var/lib/backup-console/restore")
    a = p.parse_args()
    if a.action == "publish":
        print(publish(json.loads(Path(a.receipt).read_text(encoding="utf-8-sig")), a.output, Path(a.receipt).parent)); return
    files = plan(json.loads(Path(a.config).read_text(encoding="utf-8-sig")), json.loads(Path(a.manifest).read_text(encoding="utf-8-sig")))
    dest = Path(a.output); dest.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (dest / name).write_text(data, encoding="ascii")
    hashes = {name: hashlib.sha256((dest / name).read_bytes()).hexdigest() for name in files}
    (dest / "plan-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="ascii")
    print("PREPARED_ONLY: review SQL/manifest; no database or NAS connection made")


if __name__ == "__main__":
    main()
