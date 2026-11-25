"""Integration tests against a dedicated retained PostgreSQL database, never live users."""
import os,secrets,unittest
if not os.environ.get('DB_NAME','').startswith('board_test_'):raise RuntimeError('Dedicated test database required')
from app import app,init,db
from werkzeug.security import generate_password_hash

class AuthFlow(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  init();cls.name='qa_'+secrets.token_hex(5);cls.password='Aa9!'+secrets.token_hex(12)
  with db() as c:c.execute("INSERT INTO users(username,password,role,must_change) VALUES(%s,%s,'admin',false)",(cls.name,generate_password_hash(cls.password)))
 def setUp(self):
  self.c=app.test_client();self.token=self.c.get('/api/session').json['csrf']
 def post(self,path,body):return self.c.post('/api/'+path,json=body,headers={'X-CSRF-Token':self.token})
 def login(self):
  r=self.post('login',{'username':self.name,'password':self.password});self.assertEqual(r.status_code,200);self.token=r.json['csrf'];self.assertIn('HttpOnly',r.headers.get('Set-Cookie',''));self.assertIn('SameSite=Strict',r.headers.get('Set-Cookie',''))
 def test_unauthenticated_board(self):self.assertEqual(self.c.get('/api/board').status_code,401)
 def test_authenticated_evidence(self):
  self.login();r=self.c.get('/api/board');self.assertEqual(r.status_code,200);self.assertEqual(r.json['summary']['total'],len(r.json['assets']));self.assertIn('stale',r.json);self.assertEqual(r.headers['Cache-Control'],'no-store')
 def test_csrf_required(self):self.assertEqual(self.c.post('/api/login',json={}).status_code,403)
 def test_malformed_body_rejected(self):self.assertEqual(self.c.post('/api/login',json=[],headers={'X-CSRF-Token':self.token}).status_code,400)
 def test_wrong_password(self):self.assertEqual(self.post('login',{'username':self.name,'password':'wrong'}).status_code,401)
 def test_viewer_lifecycle_and_revocation(self):
  self.login();name='viewer_'+secrets.token_hex(5);pw='Ab12!'+secrets.token_hex(12)
  self.assertEqual(self.post('users',dict(username=name,password=pw)).status_code,201)
  viewer=app.test_client();tok=viewer.get('/api/session').json['csrf'];r=viewer.post('/api/login',json=dict(username=name,password=pw),headers={'X-CSRF-Token':tok});tok=r.json['csrf']
  self.assertEqual(viewer.get('/api/board').status_code,428)
  new='Ac13!'+secrets.token_hex(12)
  self.assertEqual(viewer.post('/api/password',json={'current':pw,'new':new},headers={'X-CSRF-Token':tok}).status_code,200)
  self.assertEqual(viewer.get('/api/board').status_code,401)
  tok=viewer.get('/api/session').json['csrf'];r=viewer.post('/api/login',json=dict(username=name,password=new),headers={'X-CSRF-Token':tok});tok=r.json['csrf']
  self.assertEqual(viewer.get('/api/users').status_code,403)
  uid=next(u['id'] for u in self.c.get('/api/users').json['users'] if u['username']==name)
  temporary='Az19!'+secrets.token_hex(12)
  self.assertEqual(self.post('users/'+str(uid),{'action':'reset','password':temporary}).status_code,200)
  self.assertEqual(viewer.get('/api/board').status_code,401)
  tok=viewer.get('/api/session').json['csrf'];r=viewer.post('/api/login',json=dict(username=name,password=temporary),headers={'X-CSRF-Token':tok});self.assertEqual(r.status_code,200);self.assertTrue(r.json['must_change'])
  self.assertEqual(self.post('users/'+str(uid),{'action':'toggle'}).status_code,200)
  self.assertEqual(viewer.get('/api/board').status_code,401)
 def test_invalid_name_and_no_self_disable(self):
  self.login();self.assertEqual(self.post('users',dict(username="a';select",password='Ab123456789!')).status_code,400)
  uid=next(u['id'] for u in self.c.get('/api/users').json['users'] if u['username']==self.name)
  self.assertEqual(self.post('users/'+str(uid),{'action':'toggle'}).status_code,400)
 def test_cookie_flags_and_logout(self):
  self.login();r=self.c.get('/api/session');self.assertTrue(r.json['user']);self.assertEqual(self.post('logout',{}).status_code,200);self.assertEqual(self.c.get('/api/users').status_code,401)
 def test_z_account_limit(self):
  self.login()
  with db() as c:
   n=c.execute('SELECT count(*) AS n FROM users').fetchone()['n']
   for i in range(100-n):c.execute("INSERT INTO users(username,password,role,enabled) VALUES(%s,%s,'viewer',false)",('limit_'+secrets.token_hex(8),'disabled_test_placeholder'))
  self.assertEqual(self.post('users',dict(username='overflow_'+secrets.token_hex(4),password=self.password)).status_code,400)
 def test_rate_limit(self):
  name='absent_'+secrets.token_hex(6)
  for i in range(10):self.assertEqual(self.post('login',{'username':name,'password':'bad'}).status_code,401)
  self.assertEqual(self.post('login',{'username':name,'password':'bad'}).status_code,429)
if __name__=='__main__':unittest.main()
