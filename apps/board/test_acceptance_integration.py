"""Dedicated database only; never run against production accounts."""
import datetime as dt,json,os,secrets,unittest
from pathlib import Path
if not os.environ.get('DB_NAME','').startswith('board_test_'):raise RuntimeError('Test DB required')
from app import app,init,db,hash_password

class Integration(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  init();cls.pw='Test9!'+secrets.token_hex(12);cls.names={}
  with db() as c:
   for role in ('admin','viewer'):
    name=role+secrets.token_hex(5);cls.names[role]=name
    c.execute('INSERT INTO users(username,password,role,must_change) VALUES(%s,%s,%s,false)',(name,hash_password(cls.pw),role))
 def setUp(self):
  self.client=app.test_client();self.write_snapshot()
  self.body=dict(asset_id='gitlab185',backup_run='run-1',status='passed',tested_at=dt.datetime.now(dt.timezone.utc).isoformat(),scope='isolated login',steps='login and clone',actual='commit matches',evidence='/test/log.txt',untested='CI')
 def write_snapshot(self,stale=False):
  stamp=dt.datetime.now(dt.timezone.utc)-dt.timedelta(hours=2 if stale else 0)
  Path(os.environ['SNAPSHOT']).write_text(json.dumps({'sampled':stamp.isoformat(),'assets':[{'id':'gitlab185','targets':['nas243'],'copies':[{'target':'nas243','date':stamp.astimezone(dt.timezone(dt.timedelta(hours=8))).date().isoformat(),'run':'run-1','status':'receipt_found'}]}]}))
 def login(self,role):
  token=self.client.get('/api/session').json['csrf']
  r=self.client.post('/api/login',json={'username':self.names[role],'password':self.pw},headers={'X-CSRF-Token':token})
  self.assertEqual(r.status_code,200);self.token=r.json['csrf']
 def post(self):return self.client.post('/api/acceptance',json=self.body,headers={'X-CSRF-Token':self.token})
 def test_admin_append_and_actor(self):
  self.login('admin');r=self.post();self.assertEqual(r.status_code,201)
  self.body['status']='failed';r2=self.post();self.assertEqual(r2.status_code,201);self.assertNotEqual(r.json['id'],r2.json['id'])
  a=self.client.get('/api/board').json['assets'][0]
  ids=(r2.json['id'],r.json['id']);records=[item for item in a['manual_acceptance'] if item['id'] in ids]
  self.assertEqual(len(records),2);self.assertTrue(all(x['tester']==self.names['admin'] for x in records))
  self.assertEqual(a['restore']['status'],'not_tested');self.assertTrue(a['copies'][0]['ok'])
 def test_viewer_cannot_write(self):
  self.login('viewer');self.assertEqual(self.post().status_code,403)
 def test_csrf(self):
  self.login('admin');self.assertEqual(self.client.post('/api/acceptance',json=self.body).status_code,403)
 def test_stale(self):
  self.login('admin');self.write_snapshot(True);self.assertEqual(self.post().status_code,409)
 def test_wrong_run(self):
  self.login('admin');self.body['backup_run']='another';self.assertEqual(self.post().status_code,400)
 def test_missing_evidence(self):
  self.login('admin');self.body['evidence']='';self.assertEqual(self.post().status_code,400)

if __name__=='__main__':unittest.main()
