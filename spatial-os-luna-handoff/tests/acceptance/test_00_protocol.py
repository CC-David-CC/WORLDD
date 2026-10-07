from harness.base import Acceptance, case

class ProtocolTests(Acceptance):
    @case("P00","protocol.handshake")
    def test_hello_has_version_and_build(self):
        h=self.call("system.hello")
        self.assertEqual(h["protocol"],"spatial-conformance/1")
        self.assertEqual(h["implementation"],"production")
        self.assertIsInstance(h["build_id"],str)
        self.assertGreaterEqual(len(h["build_id"]),7)
    @case("P00","protocol.unknown_method")
    def test_unknown_method_is_typed_error(self):
        self.error("UNKNOWN_METHOD","definitely.not.a.real.method")
        self.assertEqual(self.call("system.hello")["protocol"],"spatial-conformance/1")
    @case("P00","protocol.required_parameters")
    def test_missing_parameter_rejected(self):
        self.error("INVALID_ARGUMENT","world.get")
    @case("P00","protocol.capabilities")
    def test_capabilities_are_unique_and_explicit(self):
        caps=self.call("system.hello")["capabilities"]
        self.assertIsInstance(caps,list)
        self.assertEqual(len(caps),len(set(caps)))
        self.assertNotIn("mock",caps)
    @case("P00","protocol.concurrent_correlation")
    def test_concurrent_calls_keep_their_responses(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _: self.call("system.hello"),range(32)))
        self.assertEqual(len({r["build_id"] for r in results}),1)
