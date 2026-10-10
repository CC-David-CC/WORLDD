"""Existing coordinator rejects a real inherited actor listener before world launch."""
import socket
import subprocess
import unittest
from unittest.mock import patch
import test_session_world as existing

class ActorListenerBaseline(existing.SessionWorldTests):
    def test_actor_listener_mapping_is_not_yet_supported(self):
        listener = socket.socket(); self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0)); listener.listen(8)
        popen = subprocess.Popen
        def launch(args, **kwargs):
            if str(args[0]).endswith('spatial-session-world-probe'):
                kwargs['pass_fds'] = tuple(kwargs['pass_fds']) + (listener.fileno(),)
            return popen(args, **kwargs)
        def mapping(config):
            config.inherited_source_fds.append(listener.fileno())
            config.launch.inherited_fds.append(254)
            config.launch.arguments.extend(['--actor-listen-fd', '254'])
        with patch.object(subprocess, 'Popen', side_effect=launch):
            diagnostic = self.rejected_world(mapping)
        self.assertIn('Session world requires an unmapped compositor launch configuration', diagnostic)
        self.assertFalse((self.world_journal / 'launch.db').exists())
        print('CONFIRMED: valid inherited TCP listener rejected before world launch', flush=True)

if __name__ == '__main__':
    suite = unittest.TestSuite([ActorListenerBaseline('test_actor_listener_mapping_is_not_yet_supported')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
