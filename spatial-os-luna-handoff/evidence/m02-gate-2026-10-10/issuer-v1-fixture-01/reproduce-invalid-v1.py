import ast,json,sqlite3,subprocess,sys
from pathlib import Path
old=Path(sys.argv.pop(1));sys.path.insert(0,str(old/'tests/core'))
from test_identity_issuer import IssuerFixture,BINARY
tree=ast.parse((old/'tests/core/test_identity_enrollment.py').read_text())
method=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='test_explicit_schema_migration_preserves_issuer_key_owner_and_policy')
sql=next(ast.literal_eval(n.args[0]) for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='executescript')
fixture=IssuerFixture();fixture.setUp()
try:
 fixture.init()
 with sqlite3.connect(fixture.state/'identity.db') as db:
  db.executescript(sql)
  tables=db.execute("SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name").fetchall()
  assert tables==[('authority_retirement',),('issuer',)],tables
  try:db.execute('CREATE TABLE authority_retirement(marker INTEGER)')
  except sqlite3.OperationalError as error:collision=str(error)
  else:raise AssertionError('Expected a table collision')
 before=(fixture.state/'identity.db').read_bytes()
 p=subprocess.run([str(BINARY),'--state-dir',str(fixture.state),'--migrate'],input=b'',capture_output=True,timeout=5)
 assert p.returncode==2 and (fixture.state/'identity.db').read_bytes()==before
 print(json.dumps(dict(tables=tables,collision=collision,migration_exit=p.returncode,stderr=p.stderr.decode(),database_unchanged=True)))
finally:fixture.doCleanups()
