"""Reject invalid remote listener setup before exposing an actor interface."""
import socket
import subprocess
import unittest
from google.protobuf import text_format
import world_session_pb2 as session
from test_world_bootstrap import BootstrapWorldFixture
from test_world_identity import WORLD


class RemoteConfigTests(BootstrapWorldFixture):
    def setUp(self):
        super().setUp()
        self.local=self.scoped_world(authenticate=False);self.local.close()
        self.key,self.cert=self.certificate('remote-config','world.test')
        self.tls=session.WorldActorTlsConfig(protocol_version=1,certificate_path=str(self.cert),
            private_key_path=str(self.key),client_ca_path=str(self.service_cert),
            io_timeout_ms=700,idle_timeout_ms=3000,connection_limit=8)
        self.tls_path=self.root/'remote-config.textproto'

    def reject(self,message,kind='tcp',arguments=None,write_config=True):
        if write_config:
            self.tls_path.write_text(text_format.MessageToString(self.tls));self.tls_path.chmod(0o600)
        listener=socket.socket(type=socket.SOCK_DGRAM if kind=='udp' else socket.SOCK_STREAM)
        try:
            listener.bind(('127.0.0.1',0))
            if kind=='tcp':listener.listen(4)
            args=['--state-dir',str(self.local.state_path),'--identity-config',str(self.local.config_path),
                '--actor-listen-fd',str(listener.fileno()),'--actor-tls-config',str(self.tls_path)]
            if arguments:args=arguments(args,listener.fileno())
            result=subprocess.run([str(WORLD),*args],pass_fds=(listener.fileno(),),input=b'',capture_output=True,timeout=5)
            self.assertEqual(result.returncode,2,result.stderr.decode(errors='replace'))
            self.assertIn(message,result.stderr.decode(errors='replace'))
        finally:listener.close()

    def test_non_listening_tcp_descriptor_is_rejected(self):
        self.reject('listening TCP socket',kind='unbound-listener')

    def test_udp_descriptor_is_rejected(self):
        self.reject('listening TCP socket',kind='udp')

    def test_reused_actor_listener_descriptor_is_rejected(self):
        self.reject('Duplicate actor or broker channel',arguments=lambda args,fd:args+['--actor-fd',str(fd)])

    def test_reused_broker_listener_descriptor_is_rejected(self):
        self.reject('cannot share the broker descriptor',arguments=lambda args,fd:args+['--workspace-fd',str(fd),'--workspace-pid','123'])

    def test_listener_requires_tls_configuration(self):
        self.reject('listener and configuration are required together',arguments=lambda args,fd:args[:-2])

    def test_listener_requires_identity_configuration(self):
        self.reject('identity configuration and actor transport are required together',arguments=lambda args,fd:args[:2]+args[4:])

    def test_unsupported_version_and_limits_are_rejected(self):
        for field,value,message in [('protocol_version',2,'Unsupported actor TLS configuration version'),
                ('io_timeout_ms',49,'limits are out of range'),('io_timeout_ms',60001,'limits are out of range'),
                ('idle_timeout_ms',49,'limits are out of range'),('idle_timeout_ms',300001,'limits are out of range'),
                ('connection_limit',0,'limits are out of range'),('connection_limit',129,'limits are out of range')]:
            with self.subTest(field=field,value=value):
                previous=getattr(self.tls,field);setattr(self.tls,field,value)
                self.reject(message);setattr(self.tls,field,previous)

    def test_relative_certificate_and_missing_trust_are_rejected(self):
        self.tls.certificate_path='relative.crt';self.reject('absolute file path')
        self.tls.certificate_path=str(self.cert);self.tls.client_ca_path=str(self.root/'absent.crt')
        self.reject('Unsafe or missing TLS material')

    def test_mismatched_private_key_is_rejected(self):
        self.tls.private_key_path=str(self.service_key);self.reject('Invalid TLS certificate or private key')

    def test_readable_private_key_is_rejected(self):
        self.key.chmod(0o644);self.reject('Unsafe or missing TLS material')

    def test_symlinked_tls_configuration_is_rejected(self):
        actual=self.root/'actual-tls.textproto';actual.write_text(text_format.MessageToString(self.tls));actual.chmod(0o600)
        self.tls_path.symlink_to(actual);self.reject('Unsafe or missing TLS material',write_config=False)

    def test_unknown_configuration_field_is_rejected(self):
        self.tls_path.write_text(text_format.MessageToString(self.tls)+'unknown_transport_flag: true\n');self.tls_path.chmod(0o600)
        self.reject('Invalid actor TLS configuration',write_config=False)


if __name__=='__main__':unittest.main(verbosity=2)
