"""Use the production C++ client for the real issuer/broker lease contract."""
import socket
import subprocess
import unittest
from unittest.mock import patch
import test_application_control_lease as lease
import test_application_access as access


class ClientBroker(lease.LeaseBroker):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        upstream=self.stream;self.stream,child=socket.socketpair()
        self.client_log=(self.fixture.root/'broker-client.log').open('wb')
        self.client=subprocess.Popen([str(access.workspace.WORLD.parent/'spatial-broker-control-client-probe'),
            str(upstream.fileno()),str(self.proc.pid)],stdin=child,stdout=child,stderr=self.client_log,
            pass_fds=(upstream.fileno(),))
        upstream.close();child.close()

    def close(self):
        super().close()
        if not hasattr(self,'client') or self.client_log.closed:return
        try:self.client.wait(timeout=3)
        except subprocess.TimeoutExpired:self.client.kill();self.client.wait(timeout=3);raise
        finally:self.client_log.close()
        text=(self.fixture.root/'broker-client.log').read_text()
        self.fixture.assertEqual(self.client.returncode,0,text)
        for marker in ('AddressSanitizer','LeakSanitizer','runtime error:'):self.fixture.assertNotIn(marker,text)


class BrokerControlClientLiveTests(lease.ApplicationControlLeaseTests):
    def application(self,rights=access.ALL,native_enabled=True):
        with patch.object(access,'Broker',ClientBroker):
            return access.ApplicationAccessTests.setup_application(self,rights,native_enabled=native_enabled)


if __name__=='__main__':unittest.main(defaultTest='BrokerControlClientLiveTests',verbosity=2)
