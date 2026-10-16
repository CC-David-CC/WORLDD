"""Independent filesystem check for the configured evidence-root contract."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from harness.copy_fault_evidence import durable_write


class CopyFaultEvidenceTests(unittest.TestCase):
    def test_external_configured_root_is_durable_and_outside_output_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory(prefix='m07-external-evidence-',dir='/tmp') as temporary:
            directory=Path(temporary).resolve()
            self.assertFalse(directory.is_relative_to(Path.home()/'spatial-artifacts'))
            evidence=directory/'qualification-evidence';evidence.mkdir()
            nested=evidence/'fresh'/'nested';nested.mkdir(parents=True)
            destination=nested/'cleanup.pb';outside=directory/'outside.pb'
            with patch.dict(os.environ,SPATIAL_SECURITY_EVIDENCE=str(evidence)):
                durable_write(destination,b'exact checked cleanup bytes')
                self.assertEqual(destination.read_bytes(),b'exact checked cleanup bytes')
                with self.assertRaisesRegex(ValueError,'configured evidence root'):
                    durable_write(outside,b'must not be written')
            self.assertFalse(outside.exists())


if __name__=='__main__':unittest.main(verbosity=2)
