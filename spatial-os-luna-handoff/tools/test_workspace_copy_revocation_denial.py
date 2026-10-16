"""Pure typed-denial controls; importing the actor fixture would start argument setup."""
import ast
from pathlib import Path
import unittest

SOURCE=Path(__file__).resolve().parents[1]/'tests/core/test_workspace_copy_faults.py'
module=ast.parse(SOURCE.read_bytes())
function=next(node for node in module.body if isinstance(node,ast.FunctionDef) and node.name=='is_revoked_copy_status_denial')
namespace={}
exec(compile(ast.Module(body=[function],type_ignores=[]),str(SOURCE),'exec'),namespace)
accept=namespace['is_revoked_copy_status_denial']
UNAUTHORIZED=12
FORBIDDEN=13

class CopyRevocationDenialTests(unittest.TestCase):
    def accepted(self,code,message):
        return accept(code,message,unauthorized=UNAUTHORIZED,forbidden=FORBIDDEN)

    def test_exact_world_cancellation_fence_accepts_default_and_preserved_denial(self):
        for code in (UNAUTHORIZED,FORBIDDEN):
            with self.subTest(code=code):
                self.assertTrue(self.accepted(code,'World session changed during operation'))

    def test_existing_layer_denials_keep_their_exact_codes(self):
        reasons=('Issuer revocation invalidated the in-flight broker result','Actor credential is revoked',
                 'Actor identity is inactive','Channel has no authenticated actor','World session was revoked')
        for message in reasons:
            with self.subTest(message=message):
                self.assertTrue(self.accepted(UNAUTHORIZED,message))
                self.assertFalse(self.accepted(FORBIDDEN,message))
        self.assertTrue(self.accepted(FORBIDDEN,'Workspace copy status authority is denied'))
        self.assertFalse(self.accepted(UNAUTHORIZED,'Workspace copy status authority is denied'))

    def test_arbitrary_codes_and_changed_messages_are_rejected(self):
        message='World session changed during operation'
        # Includes success, malformed, storage, busy, timeout, unavailable and internal failures.
        for code in range(0,40):
            if code not in (UNAUTHORIZED,FORBIDDEN):
                with self.subTest(code=code):self.assertFalse(self.accepted(code,message))
        for code in (UNAUTHORIZED,FORBIDDEN):
            for text in ('',message+' ',message.lower(),'World session operation failed',
                         'World identity service is disabled','Unexpected actor denial'):
                with self.subTest(code=code,message=text):self.assertFalse(self.accepted(code,text))

if __name__=='__main__':unittest.main(verbosity=2)
