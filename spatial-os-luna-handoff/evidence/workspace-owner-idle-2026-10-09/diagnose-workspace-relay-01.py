import hashlib,json,os,pathlib,sys,threading,time,unittest
root=pathlib.Path('/home/dflanag3/src/spacial_desktop/spatial-os-luna-handoff')
source=root/'local/m02-participant-session-source-03'
build=root/'local/m02-participant-session-runtime-sanitize-03/build'
out=root/'local/workspace-relay-diagnostic-01';out.mkdir()
manifest=json.loads((root/'local/participant-session-candidate-03.json').read_text())
for name,digest in manifest['files'].items():assert hashlib.sha256((source/name).read_bytes()).hexdigest()==digest,name
os.environ.update(ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
sys.path[:0]=[str(build/'python'),str(source),str(source/'tests/core')]
sys.argv=[sys.argv[0],str(build/'identityd'),str(build/'spatial-issuer-client'),str(build/'worldd')]
# Imported fixture modules consume their production binary arguments.
import test_workspace_authority as target
log=(out/'events.jsonl').open('w');lock=threading.Lock();start=time.monotonic()
def record(event,**values):
    with lock:
        log.write(json.dumps(dict(t=time.monotonic()-start,thread=threading.get_ident(),event=event,**values))+'\n');log.flush()
def trace(frame,event,arg):
    if frame.f_code.co_filename.endswith('/test_world_authority.py') and frame.f_code.co_name=='relay':
        if event=='exception':record('relay_exception',line=frame.f_lineno,type=arg[0].__name__,message=str(arg[1]))
        elif event=='line' and frame.f_lineno in (63,64,65,66,68,69,70,74):
            obj=frame.f_locals.get('self');req=frame.f_locals.get('request');reply=frame.f_locals.get('reply')
            record('relay_line',line=frame.f_lineno,armed=obj.armed.is_set(),delivered=obj.delivered.is_set(),operation=req.WhichOneof('operation') if req else None,outcome=reply.WhichOneof('outcome') if reply else None)
        return trace
    return None
threading.settrace(trace)
oldclose=target.WorldProcess.close
def close(self):
    record('world_close_begin',exited=self.proc.poll() is not None)
    try:return oldclose(self)
    finally:record('world_close_end')
target.WorldProcess.close=close
oldrequest=target.BrokerProcess.request
def request(self,**operation):
    record('broker_request',operation=list(operation))
    reply=oldrequest(self,**operation)
    record('broker_reply',outcome=reply.WhichOneof('outcome'),error_code=reply.error.code,error_message=reply.error.message)
    return reply
target.BrokerProcess.request=request
results=[]
with (out/'tests.log').open('w') as output:
    for iteration in range(5):
        record('iteration',number=iteration)
        suite=unittest.TestSuite([target.WorkspaceAuthorityTests('test_retry_cannot_acknowledge_a_process_revoked_during_status_refresh')])
        result=unittest.TextTestRunner(stream=output,verbosity=2).run(suite);output.flush()
        results.append(dict(iteration=iteration,tests=result.testsRun,failures=len(result.failures),errors=len(result.errors)))
        print(results[-1],flush=True)
        if not result.wasSuccessful():break
(out/'run.json').write_text(json.dumps(dict(results=results,source_manifest_sha256=hashlib.sha256((root/'local/participant-session-candidate-03.json').read_bytes()).hexdigest()),indent=2)+'\n')
log.close()
