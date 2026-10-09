import ast,hashlib,json,pathlib,tarfile
r=pathlib.Path(__file__).resolve().parents[1];root=r/'local/participant-fixtures-draft-01';root.mkdir()
with tarfile.open(r/'local/workspace-participant-candidate-02.tar') as archive:archive.extractall(root,filter='data')
def edit(name,old,new):
    p=root/name;s=p.read_text(encoding='utf-8');assert s.count(old)==1,(name,s.count(old),old[:80]);p.write_text(s.replace(old,new),encoding='utf-8',newline='\n')
edit('tests/core/test_world_identity.py','import os\n','import os\nimport io\nimport grants_pb2 as grants\n')
edit('tests/core/test_world_identity.py','from harness.wire import frame, make_request, result_value','from harness.wire import frame, make_request, result_value, read_message')
edit('tests/core/test_world_identity.py','        super().setUp();self.worlds=[]','        super().setUp();self.worlds=[];self.registered_services={};self.registry_revision=0')
edit('tests/core/test_world_identity.py','    def world(self, admission=', '''    def configure_participant(self, config, domains):
        # Registration is explicit fixture provisioning. Production still rejects missing service configuration.
        identity=tuple(sorted(domain.SerializeToString() for domain in domains))
        if identity not in self.registered_services:
            number=len(self.registered_services)
            key,public=self.key('service-'+str(number))
            definition=pb.AuthorityParticipant(id=os.urandom(32),public_key=public,domains=domains)
            peer=self.connect()
            try:
                op=pb.IssuerOperation(participant_register=pb.AuthorityParticipantRegister(
                    participant=definition,expected_registry_revision=self.registry_revision))
                reply=self.execute(peer,self.challenge(peer,op));self.assertTrue(reply.HasField('participant'),reply)
            finally:peer.close()
            self.registry_revision=reply.participant.registered_revision
            self.registered_services[identity]=(number,key,definition.id,self.registry_revision)
        number,key,participant_id,revision=self.registered_services[identity]
        client=self.root/('registered-client-'+str(number)+'.textproto')
        client.write_bytes(Path(config.issuer_client_config).read_bytes());client.chmod(0o600)
        config.issuer_client_config=str(client)
        participant=pb.AuthorityParticipantRuntimeConfig(protocol_version=1,issuer_client_config=str(client),
            private_key_path=str(key),participant_id=participant_id,registered_revision=revision)
        path=self.root/('registered-participant-'+str(number)+'.textproto')
        path.write_text(text_format.MessageToString(participant));path.chmod(0o600)
        config.participant_runtime_config=str(path)
    def configure_world_participant(self, config, state):
        # Initialize or read the real stopped world before registering its persisted identity.
        result=subprocess.run([str(WORLD),'--state-dir',str(state)],input=frame(make_request('world.info',{})),
            capture_output=True,timeout=6)
        self.assertEqual(result.returncode,0,result.stderr)
        reply=read_message(io.BytesIO(result.stdout),core.Reply);self.assertFalse(reply.HasField('error'),reply)
        self.configure_participant(config,[grants.Domain(kind=grants.WORLD,id=result_value(reply)['world_id'])])
    def world(self, admission=''')
edit('tests/core/test_world_identity.py',"        path=self.root/f'world-{len(self.worlds)}.textproto';", "        state=state or self.root/f'world-state-{len(self.worlds)}'\n        self.configure_world_participant(config,state)\n        path=self.root/f'world-{len(self.worlds)}.textproto';")
edit('tests/core/test_world_authority.py',"        config_path = self.root/f'scoped-{number}.textproto';", "        self.configure_world_participant(config,state)\n        config_path = self.root/f'scoped-{number}.textproto';")
edit('tests/core/test_workspace_authority.py',"        path = self.root/'workspace-policy.textproto';", "        self.configure_participant(config,[grant.Domain(kind=grant.WORKSPACE,id=binding.workspace) for binding in config.bindings])\n        path = self.root/'workspace-policy.textproto';")
edit('tests/core/test_workspace_authority.py','''    def close(self):
        self.stream.close()
        if self.proc.poll() is None:
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3); raise
        self.log.close()''','''    def close(self):
        if self.stream.fileno()<0:return
        if self.proc.poll() is None:
            try:self.stream.shutdown(socket.SHUT_WR)
            except OSError:pass
            try: self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=3); raise
        self.stream.close();self.log.close()''')
edit('tests/core/test_application_access.py',"        self.config = self.root/'broker.textproto';", "        self.configure_participant(config,[grant.Domain(kind=grant.WORKSPACE,id=binding.workspace) for binding in config.bindings])\n        self.config = self.root/'broker.textproto';")
edit('tests/core/test_application_control_lease.py','        self.events=[]','        self.events=[];self.owned_leases={}')
edit('tests/core/test_application_control_lease.py','        return reply\n\n    def response','''        if reply.HasField('control_lease'):self.owned_leases[reply.control_lease.id]=reply.control_lease
        if reply.HasField('control_released'):self.owned_leases.pop(reply.control_released.id,None)
        return reply

    def close(self):
        # This fixture owns reservations without dispatching native input. Release them explicitly before EOF.
        if self.stream.fileno()>=0 and self.proc.poll() is None:
            for lease in list(self.owned_leases.values()):
                reply=self.request(control_release=pb.ApplicationControlRelease(id=lease.id,principal=lease.principal))
                self.fixture.assertTrue(reply.HasField('control_released'),reply)
        super().close()

    def response''')
prior=json.loads((r/'local/workspace-participant-candidate-02.json').read_text(encoding='utf-8'))['files']
def tests(text):
    result={}
    for node in ast.walk(ast.parse(text)):
        if isinstance(node,ast.ClassDef):
            for method in node.body:
                if isinstance(method,(ast.FunctionDef,ast.AsyncFunctionDef)) and method.name.startswith('test_'):
                    result[node.name+'.'+method.name]=ast.dump(method,include_attributes=False)
    return result
with tarfile.open(r/'local/workspace-participant-candidate-02.tar') as archive:
    for name in prior:
        if name.startswith('tests/') and name.endswith('.py'):
            old=archive.extractfile(name).read().decode('utf-8');new=(root/name).read_text(encoding='utf-8')
            assert tests(old)==tests(new),name
files={n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in prior}
changed=[n for n in files if files[n]!=prior[n]];assert len(changed)==5,changed
manifest=dict(base='workspace-participant-candidate-02',files=files,changed=changed,existing_test_methods='unchanged AST',production_source='unchanged')
(r/'local/participant-fixtures-candidate-01.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
with tarfile.open(r/'local/participant-fixtures-candidate-01.tar','w') as archive:
    for name in sorted(files):archive.add(root/name,arcname=name,recursive=False)
print('250 files; five fixture files changed; every existing test method unchanged')
