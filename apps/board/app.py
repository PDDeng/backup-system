import datetime as dt,functools,hashlib,json,os,re,secrets,sys
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from flask import Flask,request,session,jsonify,send_from_directory,g
from werkzeug.security import generate_password_hash,check_password_hash
from model import project
from acceptance import validate as validate_acceptance

def secret(name):return Path(os.environ[name]).read_text().strip()
app=Flask(__name__,static_folder='static')
app.config.update(SECRET_KEY=secret('APP_SECRET_FILE'),MAX_CONTENT_LENGTH=16384,
 SESSION_COOKIE_NAME='backup_board',SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',
 SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE','false').lower()=='true',PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=8))

def db():return psycopg.connect(host=os.getenv('DB_HOST','db'),dbname=os.getenv('DB_NAME','backup_board'),user='board',password=secret('DB_PASSWORD_FILE'),row_factory=dict_row,connect_timeout=5)
def fail(text,code=400):return jsonify(error=text),code
def hash_password(p):return generate_password_hash(p,method='pbkdf2:sha256:1000000')
DUMMY_HASH=hash_password('invalid-user-placeholder-123')
def password_ok(p):return isinstance(p,str) and 12<=len(p)<=128 and any(x.isalpha() for x in p) and any(x.isdigit() for x in p)
def audit(c,action,subject):c.execute('INSERT INTO audit(actor,action,subject) VALUES(%s,%s,%s)',(session.get('uid'),action,subject))
def init():
 with db() as c:
  c.execute('CREATE TABLE IF NOT EXISTS users(id serial PRIMARY KEY,username varchar(64) UNIQUE NOT NULL,password text NOT NULL,role text NOT NULL CHECK(role IN (\'admin\',\'viewer\')),enabled boolean NOT NULL DEFAULT true,version integer NOT NULL DEFAULT 1,must_change boolean NOT NULL DEFAULT true,created timestamptz NOT NULL DEFAULT now())')
  c.execute('CREATE TABLE IF NOT EXISTS attempts(id bigserial PRIMARY KEY,identity text NOT NULL,ip text NOT NULL,created timestamptz NOT NULL DEFAULT now(),success boolean NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS acceptance_events(id bigserial PRIMARY KEY,actor integer NOT NULL REFERENCES users(id),record jsonb NOT NULL,created timestamptz NOT NULL DEFAULT now())')
  c.execute('CREATE INDEX IF NOT EXISTS attempts_time ON attempts(created)')
  c.execute('CREATE TABLE IF NOT EXISTS audit(id bigserial PRIMARY KEY,actor integer,action text NOT NULL,subject text,created timestamptz NOT NULL DEFAULT now())')

def evidence_board():
 p=Path(os.environ.get('SNAPSHOT','/evidence/snapshot.json'))
 if p.stat().st_size>8*1024**2:raise ValueError('snapshot too large')
 return project(json.loads(p.read_text(encoding='utf-8-sig')))

def auth(admin=False,allow_change=False):
 def decorate(f):
  @functools.wraps(f)
  def wrapped(*args,**kwargs):
   with db() as c:g.user=c.execute('SELECT id,username,role,enabled,version,must_change FROM users WHERE id=%s',(session.get('uid',0),)).fetchone()
   if not g.user or not g.user['enabled'] or g.user['version']!=session.get('version'):session.clear();return fail('请重新登录',401)
   if admin and g.user['role']!='admin':return fail('需要管理员权限',403)
   if g.user['must_change'] and not allow_change:return fail('请先修改初始密码',428)
   return f(*args,**kwargs)
  return wrapped
 return decorate

@app.before_request
def csrf():
 if request.method=='POST':
  if not request.is_json:return fail('仅接受JSON请求',415)
  if not isinstance(request.get_json(silent=True),dict):return fail('JSON必须是对象',400)
  if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','!')):return fail('页面验证失效，请刷新',403)

@app.after_request
def headers(r):
 r.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
 'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"});return r

@app.get('/')
def index():return send_from_directory('static','index.html')
@app.get('/health')
def health():
 with db() as c:c.execute('SELECT 1')
 return jsonify(status='ok')
@app.get('/api/session')
def state():
 if 'csrf' not in session:session['csrf']=secrets.token_urlsafe(32)
 with db() as c:u=c.execute('SELECT id,username,role,version,enabled,must_change FROM users WHERE id=%s',(session.get('uid',0),)).fetchone()
 if not u or not u['enabled'] or u['version']!=session.get('version'):u=None
 return jsonify(csrf=session['csrf'],user={k:u[k] for k in ('username','role','must_change')} if u else None)
@app.post('/api/login')
def login():
 b=request.get_json();name=str(b.get('username','')).strip().lower();password=b.get('password','');ip=request.remote_addr
 if len(name)>64 or not isinstance(password,str) or len(password)>128:return fail('账号或密码错误',401)
 with db() as c:
  c.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(ip,))
  n=c.execute("SELECT count(*) FILTER (WHERE identity=%s) AS account_fail,count(*) FILTER (WHERE ip=%s) AS ip_fail FROM attempts WHERE created>now()-interval '15 minutes' AND NOT success",(name,ip)).fetchone()
  if n['account_fail']>=10 or n['ip_fail']>=100:return fail('尝试过多，请15分钟后再试',429)
  u=c.execute('SELECT * FROM users WHERE username=%s',(name,)).fetchone()
  ok=bool(u and u['enabled'] and check_password_hash(u['password'],password))
  if not u:check_password_hash(DUMMY_HASH,password)
  c.execute('INSERT INTO attempts(identity,ip,success) VALUES(%s,%s,%s)',(name,ip,ok))
  if not ok:return fail('账号或密码错误',401)
  session.clear();session.update(uid=u['id'],version=u['version'],csrf=secrets.token_urlsafe(32));session.permanent=True;audit(c,'login',name)
 return jsonify(ok=True,csrf=session['csrf'],must_change=u['must_change'])
@app.post('/api/logout')
def logout():session.clear();return jsonify(ok=True)
@app.post('/api/password')
@auth(allow_change=True)
def password():
 b=request.get_json();new=b.get('new','')
 if not password_ok(new):return fail('密码需要12—128位，包含字母和数字')
 with db() as c:
  u=c.execute('SELECT * FROM users WHERE id=%s FOR UPDATE',(g.user['id'],)).fetchone()
  if not check_password_hash(u['password'],str(b.get('current',''))):return fail('当前密码错误',403)
  if check_password_hash(u['password'],new):return fail('新密码不能与旧密码相同')
  c.execute('UPDATE users SET password=%s,version=version+1,must_change=false WHERE id=%s',(hash_password(new),u['id']));audit(c,'password_change',u['username'])
 session.clear();return jsonify(ok=True,relogin=True)
@app.get('/api/board')
@auth()
def board():
 try:
  p=Path(os.environ.get('SNAPSHOT','/evidence/snapshot.json'))
  if p.stat().st_size>8*1024**2:raise ValueError('snapshot too large')
  result=project(json.loads(p.read_text(encoding='utf-8-sig')))
  with db() as c:
   rows=c.execute('SELECT e.id,e.record,e.created,u.username AS tester FROM acceptance_events e JOIN users u ON u.id=e.actor ORDER BY e.id DESC LIMIT 500').fetchall()
  for a in result['assets']:a['manual_acceptance']=[dict(r) for r in rows if r['record']['asset_id']==a['id']]
  return jsonify(result)
 except (OSError,ValueError,KeyError,TypeError):return fail('备份证据暂不可用，请联系运维；不能据此认定备份成功',503)
@app.post('/api/acceptance')
@auth(admin=True)
def submit_acceptance():
 try:
  board=evidence_board()
  if board['stale']:return fail('自动证据已过期，请先刷新采集',409)
  record=validate_acceptance(request.get_json(),board['assets'])
 except (OSError,ValueError,KeyError,TypeError) as e:return fail(str(e),400)
 with db() as c:
  row=c.execute('INSERT INTO acceptance_events(actor,record) VALUES(%s,%s::jsonb) RETURNING id',(g.user['id'],json.dumps(record))).fetchone()
  audit(c,'business_acceptance',str(row['id'])+':'+record['asset_id']+':'+record['backup_run'])
 return jsonify(ok=True,id=row['id']),201

@app.get('/api/users')
@auth(admin=True)
def users():
 with db() as c:rows=c.execute('SELECT id,username,role,enabled,must_change,created FROM users ORDER BY id').fetchall()
 return jsonify(users=rows)
@app.post('/api/users')
@auth(admin=True)
def create_user():
 b=request.get_json();name=str(b.get('username','')).strip().lower();pw=b.get('password','');role=b.get('role','viewer')
 if not re.fullmatch('[a-z0-9_.-]{3,64}',name) or not password_ok(pw) or role not in ('admin','viewer'):return fail('账号格式或密码不符合要求')
 try:
  with db() as c:
   c.execute('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE')
   if c.execute('SELECT count(*) AS n FROM users').fetchone()['n']>=100:return fail('最多100个账号')
   c.execute('INSERT INTO users(username,password,role) VALUES(%s,%s,%s)',(name,hash_password(pw),role));audit(c,'user_create',name)
 except psycopg.errors.UniqueViolation:return fail('账号已存在',409)
 return jsonify(ok=True),201
@app.post('/api/users/<int:uid>')
@auth(admin=True)
def edit_user(uid):
 b=request.get_json()
 with db() as c:
  c.execute('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE');u=c.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
  if not u:return fail('账号不存在',404)
  if uid==g.user['id']:return fail('不能在此禁用或重置自己的账号')
  if b.get('action')=='toggle':
   if u['role']=='admin' and u['enabled'] and c.execute("SELECT count(*) AS n FROM users WHERE role='admin' AND enabled").fetchone()['n']<=1:return fail('必须保留管理员')
   c.execute('UPDATE users SET enabled=NOT enabled,version=version+1 WHERE id=%s',(uid,));audit(c,'user_toggle',u['username'])
  elif b.get('action')=='reset' and password_ok(b.get('password')):
   c.execute('UPDATE users SET password=%s,version=version+1,must_change=true WHERE id=%s',(hash_password(b['password']),uid));audit(c,'user_reset',u['username'])
  else:return fail('无效操作')
 return jsonify(ok=True)

if __name__=='__main__':
 init()
 if sys.argv[1:]==['init-admin']:
  name='admin';pw=sys.stdin.readline().strip()
  if not password_ok(pw):raise SystemExit('Invalid bootstrap password')
  with db() as c:
   if c.execute('SELECT count(*) AS n FROM users').fetchone()['n']:raise SystemExit('Accounts already exist; no overwrite')
   c.execute("INSERT INTO users(username,password,role) VALUES(%s,%s,'admin')",(name,hash_password(pw)))
  print('Administrator created; initial password must be changed')
