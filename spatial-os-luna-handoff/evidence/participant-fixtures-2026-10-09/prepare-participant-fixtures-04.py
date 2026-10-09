import ast,hashlib,json,pathlib,tarfile
r=pathlib.Path(__file__).resolve().parents[1];s=r/'local/participant-fixtures-draft-01'
p=s/'tests/core/test_workspace_participant_races.py';text=p.read_text(encoding='utf-8')
start=text.index('    def test_exact_multi_workspace_registration_allows_both_bindings(self):');end=text.index('    def delayed_status(self,kind):',start)
method=text[start:end];cut_start=method.index("        self.signer,public=self.key('multi-workspace-service')");cut_end=method.index('        item=self.start_broker(bindings=[other,one]);')
method=method[:cut_start]+method[cut_end:]
text=text[:start]+text[end:]
addition='''class WorkspaceParticipantMultiTests(WorkspaceParticipantFixture):
    second_binding=WorkspaceParticipantRaceTests.second_binding
    access=WorkspaceParticipantRaceTests.access
    def service_domains(self):
        return [grants.Domain(kind=grants.WORKSPACE,id='one'),grants.Domain(kind=grants.WORKSPACE,id='two')]
'''+method+'\n'
text=text.replace("if __name__=='__main__':",addition+"if __name__=='__main__':")
p.write_text(text,encoding='utf-8',newline='\n')
p=s/'tests/core/test_world_identity.py';text=p.read_text(encoding='utf-8')
needle='        self.identity=self.claims(self.record,self.info)\n';assert text.count(needle)==1
text=text.replace(needle,needle+'''        def retain_service_logs():
            if not os.environ.get('SPATIAL_SECURITY_EVIDENCE'):return
            target=Path(os.environ['SPATIAL_SECURITY_EVIDENCE']);target.mkdir(parents=True,exist_ok=True)
            for path in self.root.glob('*.log'):
                (target/(type(self).__name__+'-'+self._testMethodName+'-'+path.name)).write_bytes(path.read_bytes())
        self.addCleanup(retain_service_logs)
''');p.write_text(text,encoding='utf-8',newline='\n')
prior=json.loads((r/'local/participant-fixtures-candidate-03.json').read_text(encoding='utf-8'))['files']
files={n:hashlib.sha256((s/n).read_bytes()).hexdigest() for n in prior}
assert {n for n in prior if files[n]!=prior[n]}=={'tests/core/test_world_identity.py','tests/core/test_workspace_participant.py','tests/core/test_workspace_participant_races.py'}
for n in files:
    if n.endswith('.py'):ast.parse((s/n).read_text(encoding='utf-8'),filename=n)
(r/'local/participant-fixtures-candidate-04.json').write_text(json.dumps(dict(base='participant-fixtures-candidate-03',files=files,change='Register both workspace domains once in the multi-domain fixture; retain service diagnostics.'),indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(r/'local/participant-fixtures-candidate-04.tar','w') as a:
    for n in sorted(files):a.add(s/n,arcname=n,recursive=False)
runner=(r/'local/qualify-participant-fixtures-03.py').read_text(encoding='utf-8').replace('fixtures-03','fixtures-04')
(r/'local/qualify-participant-fixtures-04.py').write_text(runner,encoding='utf-8',newline='\n')
print('251 files, unchanged production; all fixture corrections frozen in candidate04')
