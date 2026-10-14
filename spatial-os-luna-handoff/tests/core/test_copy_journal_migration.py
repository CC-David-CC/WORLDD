import os, pathlib, sqlite3, subprocess, sys, tempfile, time
binary=pathlib.Path(sys.argv[1]).resolve()
def make_v4(root, interrupted=False):
    root.mkdir(); db=sqlite3.connect(root/'execution.db')
    db.executescript('''CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
    CREATE TABLE worktrees(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repositories(id),branch TEXT NOT NULL,path TEXT UNIQUE NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL);
    CREATE TABLE operations(id TEXT PRIMARY KEY,payload BLOB NOT NULL,result BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),state TEXT NOT NULL);
    CREATE TABLE removals(id TEXT PRIMARY KEY,payload BLOB NOT NULL,record BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),retired TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
    CREATE TABLE workspaces(id TEXT PRIMARY KEY,worktree TEXT UNIQUE NOT NULL REFERENCES worktrees(id),repo TEXT UNIQUE NOT NULL REFERENCES repositories(id),policy INTEGER NOT NULL,revision INTEGER NOT NULL);
    CREATE TABLE workspace_members(workspace TEXT NOT NULL REFERENCES workspaces(id),actor TEXT NOT NULL,rights INTEGER NOT NULL,PRIMARY KEY(workspace,actor));
    CREATE TABLE processes(id TEXT PRIMARY KEY,actor TEXT NOT NULL,operation TEXT NOT NULL,payload BLOB NOT NULL,workspace TEXT NOT NULL REFERENCES workspaces(id),rights INTEGER NOT NULL,state TEXT NOT NULL,exit_code INTEGER,UNIQUE(actor,operation));
    INSERT INTO repositories VALUES('retained','origin','/old/registered','ready');''')
    if interrupted: db.execute('CREATE TABLE copy_effects(bad TEXT)')
    db.execute('PRAGMA user_version=4'); db.commit(); db.close()
def call(root, *args): return subprocess.run([str(binary),'--state-dir',str(root),*args],capture_output=True,text=True,timeout=20)
with tempfile.TemporaryDirectory(prefix='copy-journal-migration-') as temp:
    base=pathlib.Path(temp); old=base/'old'; make_v4(old)
    refused=call(old)
    assert refused.returncode != 0 and '--migrate-v4' in refused.stderr, (refused.returncode,refused.stderr)
    migrated=call(old,'--migrate-v4')
    assert migrated.returncode == 0, migrated.stderr
    repeated=call(old,'--migrate-v4'); assert repeated.returncode == 0, repeated.stderr
    db=sqlite3.connect(old/'execution.db'); assert db.execute('PRAGMA user_version').fetchone()[0]==5
    assert db.execute("SELECT source FROM repositories WHERE id='retained'").fetchone()[0]=='origin'
    assert {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {'copy_operations','copy_effects'}; db.close()
    proc=subprocess.Popen([str(binary),'--state-dir',str(old)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    time.sleep(.25); assert proc.poll() is None, proc.communicate(timeout=2)
    proc.terminate(); proc.wait(timeout=5)
    broken=base/'interrupted'; make_v4(broken,True); failed=call(broken,'--migrate-v4'); assert failed.returncode != 0
    db=sqlite3.connect(broken/'execution.db'); assert db.execute('PRAGMA user_version').fetchone()[0]==4
    assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='copy_operations'").fetchone() is None
    assert db.execute("SELECT source FROM repositories WHERE id='retained'").fetchone()[0]=='origin'; db.close()
    print('v4 refusal, explicit migration, preserved rows, v5 reopen and rollback recovery passed')

with tempfile.TemporaryDirectory(prefix='copy-journal-v1-rollback-') as temp:
    root=pathlib.Path(temp)/'old-v1'; root.mkdir(); db=sqlite3.connect(root/'execution.db')
    db.executescript("""CREATE TABLE repositories(id TEXT PRIMARY KEY,source TEXT NOT NULL,path TEXT UNIQUE NOT NULL,state TEXT NOT NULL);
    CREATE TABLE worktrees(id TEXT PRIMARY KEY,repo TEXT NOT NULL REFERENCES repositories(id),branch TEXT NOT NULL,path TEXT UNIQUE NOT NULL,head TEXT NOT NULL,state TEXT NOT NULL);
    CREATE TABLE operations(id TEXT PRIMARY KEY,payload BLOB NOT NULL,result BLOB NOT NULL,worktree TEXT NOT NULL REFERENCES worktrees(id),state TEXT NOT NULL);
    INSERT INTO repositories VALUES('legacy','origin','/old','ready'); CREATE VIEW copy_operations AS SELECT 1 AS conflict; PRAGMA user_version=1;""")
    db.commit(); db.close(); failed=call(root,'--migrate-v1'); assert failed.returncode != 0
    db=sqlite3.connect(root/'execution.db'); assert db.execute('PRAGMA user_version').fetchone()[0]==1
    assert db.execute("SELECT source FROM repositories WHERE id='legacy'").fetchone()[0]=='origin'
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names=={'repositories','worktrees','operations'}; db.close()
