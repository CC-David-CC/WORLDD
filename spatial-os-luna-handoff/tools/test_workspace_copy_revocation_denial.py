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
        reasons=('Actor credential is revoked',
                 'Actor identity is inactive','Channel has no authenticated actor','World session was revoked')
        for message in reasons:
            with self.subTest(message=message):
                self.assertTrue(self.accepted(UNAUTHORIZED,message))
                self.assertFalse(self.accepted(FORBIDDEN,message))
        self.assertTrue(self.accepted(FORBIDDEN,'Workspace copy status authority is denied'))
        self.assertFalse(self.accepted(UNAUTHORIZED,'Workspace copy status authority is denied'))

        typed_denials=(
            (FORBIDDEN,'Workspace authority changed during the request'),
            (UNAUTHORIZED,'Workspace actor identity was revoked during the request'),
        )
        for code,message in typed_denials:
            with self.subTest(code=code,message=message):
                self.assertTrue(self.accepted(code,message))
                other_code=UNAUTHORIZED if code==FORBIDDEN else FORBIDDEN
                self.assertFalse(self.accepted(other_code,message))

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

        retired='Issuer revocation invalidated the in-flight broker result'
        for code in (UNAUTHORIZED,FORBIDDEN):
            for text in (retired,retired+' ',retired.lower()):
                with self.subTest(code=code,message=text):self.assertFalse(self.accepted(code,text))
        for code in range(0,40):
            if code not in (UNAUTHORIZED,FORBIDDEN):
                with self.subTest(code=code,message=retired):self.assertFalse(self.accepted(code,retired))

        typed_messages=('Workspace authority changed during the request',
                        'Workspace actor identity was revoked during the request')
        for message in typed_messages:
            for code in range(0,40):
                if code not in (UNAUTHORIZED,FORBIDDEN):
                    with self.subTest(code=code,message=message):self.assertFalse(self.accepted(code,message))
            for code in (UNAUTHORIZED,FORBIDDEN):
                for text in (message+' ',message.lower()):
                    with self.subTest(code=code,message=text):self.assertFalse(self.accepted(code,text))


if __name__=='__main__':unittest.main(verbosity=2)
