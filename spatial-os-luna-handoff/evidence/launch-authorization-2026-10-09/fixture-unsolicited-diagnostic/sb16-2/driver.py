import json,runpy,sys,unittest
scope=runpy.run_path(sys.argv.pop(1),run_name="diagnostic")
original=scope["LaunchAuthorizationTests"]
class Diagnostic(original):
    def poisoned(self,client,request,peer,count):
        try:return super().poisoned(client,request,peer,count)
        except BaseException:
            print(json.dumps({"requests":[r.WhichOneof("operation") for r in peer.requests],"connections":peer.connections,"stderr":open(client.stderr.name).read()}),flush=True)
            raise
suite=unittest.TestSuite(Diagnostic("test_client_unsolicited_authorization_is_rejected") for _ in range(10))
raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
