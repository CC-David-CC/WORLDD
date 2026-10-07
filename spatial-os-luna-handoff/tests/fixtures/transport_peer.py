"""Transport test double only. This is NOT a Spatial OS implementation."""
import argparse,json,sys,time
p=argparse.ArgumentParser();p.add_argument("--mode",default="echo");p.add_argument("--state-dir");a=p.parse_args()
pending=[]
for line in sys.stdin:
    r=json.loads(line);i=r["id"]
    if a.mode=="reverse-eight":
        pending.append(r)
        if len(pending)==8:
            for item in reversed(pending):
                print(json.dumps({"id":item["id"],"result":item["params"]}),flush=True)
            pending.clear()
        continue
    if a.mode=="timeout":time.sleep(5);continue
    if a.mode=="eof":break
    if a.mode=="garbage":print("NOT JSON",flush=True);continue
    if a.mode=="wrong-id":print(json.dumps({"id":i+100,"result":{}}),flush=True);continue
    if a.mode=="both":print(json.dumps({"id":i,"result":{},"error":{"code":"ERROR"}}),flush=True);continue
    if a.mode=="nonfinite":print('{"id":%d,"result":NaN}'%i,flush=True);continue
    if a.mode=="error":print(json.dumps({"id":i,"error":{"code":"TEST_ERROR","message":"expected","details":{"x":1}}}),flush=True);continue
    if a.mode=="fixture-hello":result={"protocol":"spatial-conformance/1","implementation":"harness-fixture","build_id":"fixture-only","capabilities":[]}
    else:result=r["params"]
    print(json.dumps({"id":i,"result":result}),flush=True)
