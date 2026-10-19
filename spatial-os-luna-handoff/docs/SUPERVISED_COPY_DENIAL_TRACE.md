# Supervised copy denial trace

The production issuer client sends `hello` and `info` before launch approval. The relay witness requires exactly those replies, then one launch-begin challenge and one launch-complete denial. It correlates every request and reply ID, retains the challenge, signature-purpose, candidate binding, and exact BUSY-message checks. Failure diagnostics list only operation names, request IDs, reply IDs, and reply variants.
