# Concrete local fixture chain

`index.json` identifies a resource, policy, pinned root grant, delegated grant and signed
intent. All hashes are computed from actual canonical bytes. The public seeds in
`tests/fixtures.py` sign these examples; anyone can reproduce or forge fixture authority.
Do not treat the example keys or roots as trusted outside tests. No LOCAL_READY example is
published here; synthetic readiness objects exist only within verifier unit tests.
