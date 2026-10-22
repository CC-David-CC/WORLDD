# Protobuf reproducibility proof

This verifier checks the candidate's Protobuf generation in two independent source/build roots. It binds every run to a versioned JSON contract, exact provenance bytes, pinned source files, recipe inventory, and generator toolchain. The no-contract mode uses the reviewed 580834 candidate contract. Pass `--contract` for a future package contract.

Run from a reviewed ROG checkout:

```sh
python3 -B tools/verify_protobuf_reproducibility.py \
  --contract /external/path/protobuf-contract.json \
  --artifact-root /external/new-run-directory
```

Omit `--contract` only for the reviewed 580834 baseline. The contract must be schema 1 and contain exactly these fields:

```json
{
  "schema": 1,
  "candidate": "40-character Git commit",
  "provenance_path": "/absolute/path/provenance.json",
  "provenance_sha256": "64-character SHA-256 of exact provenance bytes",
  "source_root": "/absolute/source/root",
  "build_dir": "/absolute/Debug/build/root",
  "production_off_python_path": "/absolute/staged/Debug/python",
  "artifact_parent": "/absolute/external/output/parent",
  "source_file_count": 1222,
  "recipe_count": 67,
  "candidate_python_output_count": 36,
  "production_off_python_output_count": 30,
  "cpp_output_count": 66,
  "round_output_count": 102,
  "toolchain": {
    "protoc": {"version": "libprotoc 3.21.12", "sha256": "fc386306299ba6a2f3668a0bb85cb059f389d52842f72dc618626a96c60506c7", "closure_sha256": "e13a55dd717e18fbb386dd4531248a3021a3e7ddb97142676d895ba63e81a2d5"},
    "cmake": {"version": "cmake version 3.28.3", "sha256": "1c5227af4edd22d8d689def545e18ee458260c0fd579eba2187967f38817e638", "closure_sha256": "c42a6bc1a3e08f43081fa596e3fc32852c5ee8874bd3868e5a057ae98a66cbd5"},
    "ninja": {"version": "1.11.1", "sha256": "5965527e09fe2b3787772aa4f711d6a36b393e7f2fcaa744a7a96c5a4ddf59cb", "closure_sha256": "a89a3aa1a104c52b32551ed0e1d5c98eab76d1acab1d74d2aee1598d86742810"}
  }
}
```

The values above show the baseline contract. Future contracts must retain the reviewed generator toolchain pins. The Python API exports `DEFAULT_TOOLCHAIN`, `verify_sources`, `recipes_for` (without an expected count), `generated_map`, and `cpp_map` so a prepare caller can derive contract counts without duplicating command parsing. The verifier checks source and build paths against the provenance and CMake cache. The production-off output must be an exact byte-identical subset of candidate-generated Python bindings. The candidate Python tree must match both Debug and ASan-UBSan provenance maps. The example baseline has 36 candidate Python files, 30 production-off Python files, 66 C++ files, and 102 total generated files.

The verifier accepts only pinned `protoc`, fixed CMake `make_directory`, and the reviewed compatibility `copy_if_different` operation. It never runs Ninja build commands or shell recipes. It uses a controlled locale, timezone, hash seed, and candidate commit timestamp. It records the source manifest, Protobuf inputs, 67 recipes, tool versions and dependency hashes, and both output manifests. It ignores only `.pyc` and `.pyo` files under existing `__pycache__` directories when inspecting candidate runtime trees. Fresh generation trees reject those cache files.

The artifact root must be new and inside the contract's external artifact parent. Failures exit 2 without writing a proof. Success prints the proof path and hash. The JSON proof includes the exact contract hash, schema, candidate, provenance hash, and qualification marker `UNQUALIFIED_M08`. This is a generation-reproducibility result; it does not itself qualify M08 or establish cross-machine reproducibility.

## Candidate 580834 observation

The first maintained attempt is preserved at `/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/reproducible-protobuf-01/primary-maintained-proof-run-01`. It exited 2/2 before producing outputs because a fresh replay root lacked the Python output directory. The repaired replay creates each declared output directory inside its owned staged build before invoking `protoc`. The tiny real-protoc regression covers this setup.

The repaired maintained attempt completed with command/wrapper exit 0/0 and terminal `COMPLETE`. Its proof is `/home/dflanag3/spatial-artifacts/m08-issuer-fixture-580834ceeda1-20261021/packaging-cache-repair-01/reproducible-protobuf-01/maintained-proof-02/protobuf-reproducibility.json`, SHA256 `d28d3b1376f62786f6e5efe6b605f7d7d8ab366f7b4ac0cd85350e0f35dcbc6f`. The two fresh roots matched all 102 outputs; all 36 candidate Python bindings, 66 C++ outputs, and the exact 30-file production-off subset matched their pins. This remains an unqualified reproducibility observation and does not qualify M08.
