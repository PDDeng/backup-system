"""Pure local tests: no NAS, SSH, WinRM or Oracle connections."""
import copy
import importlib.util
import json
import hashlib
import datetime as dt
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("restore_plan", ROOT / "components/oracle/restore/restore_plan.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def fixture(asset="oracle46"):
    name = "oracle01_10.10.10.46_dev研发环境数据库" if asset == "oracle46" else "oracle02_10.10.10.41_uat测试环境数据库"
    source = "\\\\10.10.11.243\\资源备份\\" + name + "\\2026-11-26\\RUN_01"
    c = dict(id=asset, target_ip="10.10.10.251", target_hostname="ORACLE-RESTORE", sid="RT"+asset[-2:], root="D:\\oracle-restore\\"+asset+"_run01", oracle_home="D:\\app\\oracle\\product\\11.2.0\\dbhome_1", source=source, mode="sample" if asset == "oracle46" else "rman", tables=[dict(owner="REST_APP", table="ORDERS", minimum_rows=1)], schema_map={"APP": "REST_APP"}, tablespace="REST_DATA", source_tablespaces=["USERS"], ddl_reviewed=True)
    def file(name, role): return dict(name=name, role=role, bytes=123, sha256="a"*64)
    files = [file("FULL_01.DMP", "dump"), file("export.log", "export_log")] if asset == "oracle46" else [file("data_01.bkp", "piece"), file("control.bkp", "controlfile"), file("rman.log", "rman_log")]
    return c, dict(id=asset, source=source, run="RUN_01", version=mod.IDS[asset], status="complete", native_exit_code=0, files=files, dbid=123456)


class PlanTests(unittest.TestCase):
    def test_sample(self):
        c,m=fixture(); p=mod.plan(c,m)
        self.assertIn("SCHEMAS=APP",p["import.par"])
        self.assertIn("REMAP_SCHEMA=APP:REST_APP",p["import.par"])
        self.assertIn("REMAP_TABLESPACE=USERS:REST_DATA",p["import.par"])
        self.assertIn("SQLFILE=preview.sql",p["preview.par"])
        self.assertIn("MAPPED_USERS_ALREADY_EXIST",p["guard.sql"])
        self.assertIn("TABLE_ROW_FLOOR",p["verify.sql"])
        self.assertIn("BUSINESS_INVALID_OBJECTS",p["verify.sql"])
        self.assertNotIn("not like",p["guard.sql"])
        self.assertFalse(json.loads(p["plan.json"])["production_change"])

    def test_full(self):
        c,m=fixture();c["mode"]="full"
        self.assertIn("FULL=YES",mod.plan(c,m)["import.par"])

    def test_c_drive_plan_and_separate_target_version(self):
        for asset in ('oracle46','oracle41'):
            c,m=fixture(asset);c['root']='C:\\core-backup\\restore\\'+asset+'_run01'
            c['oracle_home']='C:\\app\\oracle\\product\\11.2.0\\dbhome_1';c['target_version']='11.2.0.4.0'
            p=mod.plan(c,m)
            self.assertIn("version='11.2.0.4.0'",p['guard.sql'])
            loaded=json.loads(p['plan.json'])
            self.assertEqual(loaded['manifest']['version'],mod.IDS[asset])
            self.assertEqual(loaded['config']['target_version'],'11.2.0.4.0')
            if asset=='oracle41':self.assertIn("C:\\core-backup\\restore",p['duplicate.rman'])
        c,m=fixture();c['oracle_home']='C:\\app\\oracle';mod.plan(c,m)  # Cross-drive root/home safe.
        for root in ('C:\\core-backup\\restore','C:\\core-backup\\restore-other\\r','C:\\Windows\\r','C:\\core-backup\\restore\\..\\prod','C:\\core-backup\\restore\\'):
            c,m=fixture();c['root']=root
            with self.subTest(root=root),self.assertRaises(ValueError):mod.plan(c,m)
        c,m=fixture('oracle41');c['target_version']='11.2.0.1.0'
        with self.assertRaises(ValueError):mod.plan(c,m)

    def test_dotnet_seven_fraction_and_explicit_zones(self):
        for text, expected in [('2026-11-26T02:18:43.8821433Z','2026-11-26T02:18:43.882143+00:00'),('2026-11-26T10:18:43.1234567+08:00','2026-11-26T02:18:43.123456+00:00'),('2026-11-26T02:18:43Z','2026-11-26T02:18:43+00:00')]:
            with self.subTest(text=text):self.assertEqual(mod.parse_timestamp(text).isoformat(),expected)
        for text in ('2026-11-26T02:18:43','invalid','2026-02-30T00:00:00Z',None,'2026-11-26T02:18:43.1234567890Z'):
            with self.subTest(text=text),self.assertRaises(ValueError):mod.parse_timestamp(text)

    def test_published_feed_contract_and_legacy_previous_timestamp(self):
        e=dict(schema_version=1,id='oracle46',run='RUN_01',mode='sample',status='partial',production_change=False,target_ip='10.10.10.251',plan_sha256='b'*64,finished='2026-11-26T01:00:00.8821433Z',scope='SQLFILE only',phases={},deletion='not_performed')
        with tempfile.TemporaryDirectory() as td:
            previous=dict(e);previous['finished']='2026-11-26T00:59:00.9999999Z'
            Path(td,'oracle46.json').write_text(json.dumps(previous))
            record=json.loads(mod.publish(e,td).read_text())
            # Same parsing/projection used by export_feed.py (without Z replacement).
            stamp=dt.datetime.fromisoformat(record['sampled'])
            self.assertLess(abs((dt.datetime.now(dt.timezone.utc)-stamp).total_seconds()),30)
            self.assertTrue(record['sampled'].endswith('+00:00'))
            self.assertEqual(record['finished'],'2026-11-26T01:00:00.882143+00:00')
            projected={k:record[k] for k in ('sampled','status','scope','untested','deletion')}
            self.assertEqual(projected['status'],'partial')
            for untested in ('business_acceptance','application_connectivity','full_database_restore','native_restore','table_checks'):
                self.assertIn(untested,projected['untested'])
            self.assertEqual(e['finished'],'2026-11-26T01:00:00.8821433Z')  # Do not alter source receipt.
            e['untested']='not an array'
            with self.assertRaises(ValueError):mod.publish(e,td)

    def test_rman_no_source_connection_or_filename_bypass(self):
        c,m=fixture("oracle41");p=mod.plan(c,m)
        self.assertIn("BACKUP LOCATION 'D:\\oracle-restore",p["duplicate.rman"])
        self.assertIn("SET NEWNAME FOR DATABASE TO NEW",p["duplicate.rman"])
        self.assertNotIn("NOFILENAMECHECK",p["duplicate.rman"])
        self.assertNotIn("SPFILE",p["duplicate.rman"])
        self.assertIn("job_queue_processes=0",p["init.ora"])
        self.assertIn("VALIDATE CHECK LOGICAL DATABASE",p["validate.rman"])

    def test_invalid_config_matrix(self):
        changes = [("target_ip","10.10.10.46"),("target_hostname","x';drop"),("sid","ORCL"),("root","D:\\"),("root","D:\\oracle-restore\\..\\prod"),("root","\\\\nas\\data"),("mode","other"),("ddl_reviewed",False),("schema_map",{"SYS":"REST_SYS"}),("schema_map",{"APP":"APP"}),("schema_map",{"APP":"REST_A","B":"REST_A"}),("tablespace","USERS"),("source_tablespaces",["SYSTEM"]),("tables",[]),("schema_map",{})]
        for k,v in changes:
            with self.subTest(k=k,v=v):
                c,m=fixture();c[k]=v
                with self.assertRaises(ValueError):mod.plan(c,m)

    def test_invalid_manifest_matrix(self):
        for k,v in [("id","oracle41"),("status","pending_md5"),("native_exit_code",5),("native_exit_code",False),("run","../escape"),("version","19.0"),("files",[]),("files",None),("source","\\\\other\\share")]:
            with self.subTest(k=k):
                c,m=fixture();m[k]=v
                with self.assertRaises(ValueError):mod.plan(c,m)

    def test_file_matrix(self):
        for k,v in [("name","../evil"),("name","x'"),("name","."),("bytes",0),("bytes",True),("sha256",""),("role","unknown")]:
            c,m=fixture();m["files"][0][k]=v
            with self.subTest(k=k,v=v), self.assertRaises(ValueError):mod.plan(c,m)
        c,m=fixture();m["files"].append(copy.deepcopy(m["files"][0]))
        with self.assertRaises(ValueError):mod.plan(c,m)

    def test_unc_guards(self):
        for src in ["\\\\10.10.11.243\\资源备份\\other\\2026-11-26\\RUN_01",fixture()[0]["source"]+".partial",fixture()[0]["source"].replace("2026-11-26","2026-02-30"),fixture()[0]["source"].replace("RUN_01","..")]:
            c,m=fixture();c["source"]=m["source"]=src
            with self.subTest(src=src), self.assertRaises(ValueError):mod.plan(c,m)

    def test_table_and_rman_requirements(self):
        c,m=fixture();c["tables"][0]["owner"]="OTHER"
        with self.assertRaises(ValueError):mod.plan(c,m)
        for value in [0,-1,True]:
            c,m=fixture();c["tables"][0]["minimum_rows"]=value
            with self.assertRaises(ValueError):mod.plan(c,m)
        for value in [0,True,None]:
            c,m=fixture("oracle41");m["dbid"]=value
            with self.assertRaises(ValueError):mod.plan(c,m)
        c,m=fixture("oracle41");m["files"]=m["files"][1:]
        with self.assertRaises(ValueError):mod.plan(c,m)

    def test_publish_partial_and_passed(self):
        e=dict(schema_version=1,id="oracle46",run="RUN_01",status="partial",production_change=False,target_ip="10.10.10.251",plan_sha256="b"*64,finished="2026-11-26T01:00:00Z",scope="SQLFILE only",phases={})
        with tempfile.TemporaryDirectory() as td:
            d=mod.publish(e,td);self.assertEqual(json.loads(d.read_text())["status"],"partial")
            e["status"]="passed"
            with self.assertRaises(ValueError):mod.publish(e,td)
            for i,p in enumerate(("source_sha256","isolation","native_restore","table_checks")):
                name="phase-"+str(i)+".log";Path(td,name).write_bytes(b"local fixture")
                e["phases"][p]=dict(status="passed",log=name,sha256=hashlib.sha256(b"local fixture").hexdigest())
            mod.publish(e,td,td)
            e["id"]="oracle41"
            with self.assertRaises(ValueError):mod.publish(e,td,td)
            Path(td,"validate.log").write_bytes(b"local fixture")
            e["phases"]["rman_validate"]=dict(status="passed",log="validate.log",sha256=hashlib.sha256(b"local fixture").hexdigest())
            mod.publish(e,td,td)
            e["finished"]="2026-11-25T00:00:00Z"
            with self.assertRaises(ValueError):mod.publish(e,td,td)
            e["finished"]="2026-11-26T01:00:00Z"
            Path(td,"validate.log").write_bytes(b"tampered")
            with self.assertRaises(ValueError):mod.publish(e,td,td)
            e["target_ip"]="10.10.10.41"
            with self.assertRaises(ValueError):mod.publish(e,td,td)

    def test_worker_safety_static(self):
        text=(ROOT/"components/oracle/restore/invoke-restore.ps1").read_text(encoding="utf-8-sig")
        for forbidden in ("Remove-Item", "Start-Service", "Restart-Service", "Invoke-Command", "ConvertTo-Json", "ConvertFrom-Json", "Get-FileHash", "-LiteralPath", ".Restart()"):
            self.assertNotIn(forbidden,text)
        self.assertIn("[IO.FileAccess]::Read",text)
        self.assertIn("[IO.FileShare]::None",text)
        self.assertIn("$env:LOCAL=$null",text)
        self.assertIn("$status='partial'",text)

    def test_cli_writes_bound_ascii_plan_and_refuses_overwrite(self):
        c,m=fixture()
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);config=base/'config.json';manifest=base/'manifest.json';output=base/'plan'
            config.write_text(json.dumps(c),encoding='utf-8');manifest.write_text(json.dumps(m),encoding='utf-8')
            args=[sys.executable,str(ROOT/'components/oracle/restore/restore_plan.py'),'plan','--config',str(config),'--manifest',str(manifest),'--output',str(output)]
            proc=subprocess.run(args,capture_output=True,text=True)
            self.assertEqual(proc.returncode,0,proc.stderr);self.assertIn('PREPARED_ONLY',proc.stdout)
            hashes=json.loads((output/'plan-hashes.json').read_text())
            for name,digest in hashes.items():
                data=(output/name).read_bytes();data.decode('ascii');self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
            before=(output/'plan.json').read_bytes()
            self.assertNotEqual(subprocess.run(args,capture_output=True).returncode,0)
            self.assertEqual((output/'plan.json').read_bytes(),before)

    def test_publisher_exclusive_lock(self):
        e=dict(schema_version=1,id='oracle46',run='RUN_01',status='failed',production_change=False,target_ip='10.10.10.251',plan_sha256='b'*64,finished='2026-11-26T01:00:00Z',scope='local test only',phases={})
        with tempfile.TemporaryDirectory() as td:
            with mod.output_lock(Path(td),'oracle46'):
                with self.assertRaises(OSError): mod.publish(e,td)
            self.assertFalse((Path(td)/'oracle46.json').exists())
            self.assertTrue(mod.publish(e,td).exists())


if __name__ == "__main__": unittest.main()
