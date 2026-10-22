import pathlib
import contextlib
import io
import tempfile
import unittest
from unittest import mock

import verify_protobuf_reproducibility as v

SOURCE = pathlib.Path('/work/source')
BUILD = pathlib.Path('/work/build')

class ReproTests(unittest.TestCase):
    def test_cpp_recipe_supports_separate_colon_output_and_include(self):
        line = f'cd {BUILD} && /usr/bin/protoc --cpp_out :{BUILD} -I {SOURCE}/proto {SOURCE}/proto/demo.proto'
        with mock.patch.object(v.pathlib.Path, 'is_absolute', autospec=True, side_effect=lambda p: str(p).startswith('/')):
            with mock.patch.object(v, 'inside', return_value=True):
                recipe = v.parse_recipe(line, SOURCE, BUILD)
        self.assertEqual(recipe['outputs'], {'cpp': str(BUILD)})
        self.assertEqual(recipe['inputs'], [str(SOURCE / 'proto/demo.proto')])
        self.assertEqual(recipe['proto_paths'], [str(SOURCE / 'proto')])

    def test_python_recipe_tracks_generation_and_compat_copy(self):
        line = (f'cd {BUILD} && /usr/bin/cmake -E make_directory {BUILD}/python/spatial/workspace/v1 && '
                f'/usr/bin/protoc --python_out={BUILD}/python --proto_path={SOURCE}/proto {SOURCE}/proto/demo.proto && '
                f'/usr/bin/cmake -E copy_if_different {SOURCE}/{v.COMPAT} {BUILD}/python/spatial/workspace/v1/copy_worker_pb2.py')
        with mock.patch.object(v, 'inside', return_value=True):
            recipe = v.parse_recipe(line, SOURCE, BUILD)
        self.assertEqual(recipe['outputs'], {'python': str(BUILD / 'python')})
        self.assertEqual(recipe['copy'], [str(SOURCE / v.COMPAT), str(BUILD / 'python/spatial/workspace/v1/copy_worker_pb2.py')])

    def test_unapproved_shell_and_escape_rejected(self):
        line = f'cd {BUILD} && /usr/bin/protoc --python_out=/tmp/escape --proto_path={SOURCE}/proto {SOURCE}/proto/a.proto'
        with mock.patch.object(v, 'inside', side_effect=lambda p, root: pathlib.Path(p).is_relative_to(pathlib.Path(root))):
            with self.assertRaises(v.Refusal):
                v.parse_recipe(line, SOURCE, BUILD)
        with self.assertRaises(v.Refusal):
            v.chain(f'cd {BUILD} && /usr/bin/protoc --python_out={BUILD} --proto_path={SOURCE}/proto {SOURCE}/proto/a.proto ; touch /tmp/x')

    def test_two_round_coverage_and_bytes_must_match(self):
        a = {'a_pb2.py': '1', 'x.pb.cc': '2'}
        v.compare_maps(a, dict(a))
        with self.assertRaises(v.Refusal):
            v.compare_maps(a, {'a_pb2.py': '1'})
        with self.assertRaises(v.Refusal):
            v.compare_maps(a, {'a_pb2.py': 'changed', 'x.pb.cc': '2'})

    def test_production_off_is_exact_subset_of_candidate_bindings(self):
        full = {'a_pb2.py': '1', 'b_pb2.py': '2'}
        v.compare_subset({'a_pb2.py': '1'}, {'a_pb2.py': full['a_pb2.py']}, 'subset')
        with self.assertRaises(v.Refusal):
            v.compare_subset({'a_pb2.py': 'wrong'}, {'a_pb2.py': '1'}, 'subset')
        with self.assertRaises(v.Refusal):
            v.compare_subset({'missing_pb2.py': 'x'}, full, 'subset')

    def test_generated_map_rejects_bytecode_and_unexpected_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / 'one_pb2.py').write_text('binding')
            self.assertEqual(set(v.generated_map(root)), {'one_pb2.py'})
            (root / '__pycache__').mkdir()
            (root / '__pycache__/one.pyc').write_bytes(b'cache')
            with self.assertRaises(v.Refusal):
                v.generated_map(root)
            self.assertEqual(set(v.generated_map(root, allow_bytecode_cache=True)), {'one_pb2.py'})
            (root / '__pycache__/one.pyc').unlink()
            (root / 'one.pyc').write_bytes(b'outside-cache')
            with self.assertRaises(v.Refusal):
                v.generated_map(root, allow_bytecode_cache=True)
            (root / 'one.pyc').unlink()
            (root / 'unexpected.txt').write_text('extra')
            with self.assertRaises(v.Refusal):
                v.generated_map(root)


    def test_pinned_tool_path_and_unreviewed_schema_recipe_rejected(self):
        line = f'cd {BUILD} && /usr/local/bin/protoc --python_out={BUILD} --proto_path={SOURCE}/proto {SOURCE}/proto/demo.proto'
        with self.assertRaises(v.Refusal):
            v.parse_recipe(line, SOURCE, BUILD)

    def test_source_schema_mutation_breaks_pinned_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            pins = {}
            for n in range(1222):
                rel = f'source/{n:04d}.proto' if n == 0 else f'files/{n:04d}.txt'
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'original-schema' if n == 0 else f'file-{n}'.encode())
                pins[rel] = v.sha_file(path)
            provenance = {'source_sha256': pins}
            v.verify_sources(root, provenance, 1222)
            (root / 'source/0000.proto').write_bytes(b'changed-schema')
            with self.assertRaises(v.Refusal):
                v.verify_sources(root, provenance, 1222)


    def test_changed_toolchain_version_hash_or_closure_rejected(self):
        version, digest, closure = v.PINNED_TOOLS['protoc']
        v.require_tool_pin('protoc', version, digest, closure)
        for values in ((version, '0' * 64, closure), (version + ' altered', digest, closure), (version, digest, '0' * 64)):
            with self.assertRaises(v.Refusal):
                v.require_tool_pin('protoc', *values)

    def test_versioned_contract_rejects_unknown_schema_or_toolchain(self):
        contract, digest = v.load_contract(None)
        self.assertEqual(contract['schema'], 1)
        self.assertEqual(len(digest), 64)
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / 'contract.json'
            changed = dict(contract)
            changed['toolchain'] = dict(contract['toolchain'])
            changed['toolchain']['protoc'] = dict(changed['toolchain']['protoc'])
            changed['toolchain']['protoc']['sha256'] = '0' * 64
            path.write_text(__import__('json').dumps(changed))
            with self.assertRaises(v.Refusal):
                v.load_contract(path)


    def test_outer_main_writes_proof_only_after_full_candidate_comparison(self):
        import contextlib
        import io
        import json
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            source = root / 'source'
            build = root / 'build'
            production = root / 'production-off/python'
            parent = root / 'artifacts'
            for directory in (source / 'src/workspace', build / 'python/__pycache__',
                              build / 'cpp', production / '__pycache__', parent):
                directory.mkdir(parents=True, exist_ok=True)
            (source / 'message.proto').write_bytes(b'schema')
            compat = source / v.COMPAT
            compat.parent.mkdir(parents=True, exist_ok=True)
            compat.write_bytes(b'handwritten compatibility module')
            (build / 'CMakeCache.txt').write_text(
                f'CMAKE_HOME_DIRECTORY:INTERNAL={source}\nCMAKE_BUILD_TYPE:STRING=Debug\n'
                f'Protobuf_PROTOC_EXECUTABLE:FILEPATH={v.PROTOC}\n')
            (build / 'build.ninja').write_text('# fixture graph')
            (build / 'python/foo_pb2.py').write_bytes(b'generated binding')
            (build / 'python/__pycache__/foo.pyc').write_bytes(b'candidate cache')
            (production / 'foo_pb2.py').write_bytes(b'generated binding')
            (production / '__pycache__/foo.pyc').write_bytes(b'production cache')
            (build / 'cpp/foo.pb.cc').write_bytes(b'cpp source')
            (build / 'cpp/foo.pb.h').write_bytes(b'cpp header')
            binding_hash = v.sha_file(build / 'python/foo_pb2.py')
            source_pins = {'message.proto': v.sha_file(source / 'message.proto'),
                           v.COMPAT: v.sha_file(compat)}
            candidate = 'a' * 40
            provenance_data = {
                'candidate': candidate, 'source_root': str(source),
                'source_sha256': source_pins,
                'profiles': {
                    'Debug': {'build': str(build), 'generated_python_sha256': {'python/foo_pb2.py': binding_hash}},
                    'ASan-UBSan': {'generated_python_sha256': {'python/foo_pb2.py': binding_hash}},
                },
            }
            provenance = root / 'provenance.json'
            provenance.write_text(json.dumps(provenance_data))
            contract = {
                'schema': 1, 'candidate': candidate, 'provenance_path': str(provenance),
                'provenance_sha256': v.sha_file(provenance), 'source_root': str(source),
                'build_dir': str(build), 'production_off_python_path': str(production),
                'artifact_parent': str(parent), 'source_file_count': 2, 'recipe_count': 1,
                'candidate_python_output_count': 1, 'production_off_python_output_count': 1,
                'cpp_output_count': 2, 'round_output_count': 3,
                'toolchain': {name: {'version': values[0], 'sha256': values[1], 'closure_sha256': values[2]}
                              for name, values in v.PINNED_TOOLS.items()},
            }
            contract_path = root / 'contract.json'
            contract_path.write_text(json.dumps(contract))
            contract_sha = v.sha_file(contract_path)

            def fake_recipes(*args):
                return ([{'fixture': 'reviewed recipe'}], 'c' * 64)

            def fake_replay(label, stage, *args):
                staged = stage / 'build'
                for name, body in {'python/foo_pb2.py': b'generated binding',
                                   'cpp/foo.pb.cc': b'cpp source', 'cpp/foo.pb.h': b'cpp header'}.items():
                    target = staged / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                return staged

            def fake_check_output(argv, **kwargs):
                if argv[0] == 'git':
                    key = argv[3] if len(argv) > 3 else ''
                    value = {'rev-parse': candidate, 'status': '', 'show': '1792600000'}.get(key, '')
                    return value + ('\n' if value else '')
                if argv[0] == str(v.PROTOC):
                    return 'libprotoc 3.21.12\n'
                if argv[0] == str(v.CMAKE):
                    return 'cmake version 3.28.3\n'
                if argv[0] == str(v.NINJA):
                    return '1.11.1\n'
                raise AssertionError(argv)

            with contextlib.ExitStack() as stack:
                for name, value in {'CANDIDATE': v.CANDIDATE, 'PROVENANCE': v.PROVENANCE,
                                    'PROVENANCE_SHA': v.PROVENANCE_SHA, 'SOURCE': v.SOURCE,
                                    'BUILD': v.BUILD, 'PRODUCTION_OFF_PY': v.PRODUCTION_OFF_PY,
                                    'OUT_PARENT': v.OUT_PARENT}.items():
                    stack.enter_context(mock.patch.object(v, name, value))
                stack.enter_context(mock.patch.object(v, 'recipes_for', side_effect=fake_recipes))
                stack.enter_context(mock.patch.object(v, 'replay_round', side_effect=fake_replay))
                stack.enter_context(mock.patch.object(v.subprocess, 'check_output', side_effect=fake_check_output))
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(v.main(['--contract', str(contract_path), '--artifact-root', str(parent / 'run')]), 0)
            proof_path = parent / 'run/protobuf-reproducibility.json'
            proof = json.loads(proof_path.read_text())
            self.assertEqual(proof['contract_sha256'], contract_sha)
            self.assertEqual(proof['contract_manifest_sha256'], v.canon_sha(contract))
            self.assertEqual(proof['candidate_output_comparison']['candidate_python_output_count'], 1)
            self.assertEqual(proof['round_a']['output_count'], 3)

    def test_outer_main_rejects_wrong_provenance_pin_before_output_creation(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / 'source'
            build = root / 'build'
            provenance = root / 'provenance.json'
            production = root / 'production/python'
            parent = root / 'artifacts'
            source.mkdir(); build.mkdir(); production.mkdir(parents=True); parent.mkdir()
            provenance.write_text('{}')
            contract = dict(v.DEFAULT_CONTRACT)
            contract.update(candidate='a' * 40, provenance_path=str(provenance),
                            provenance_sha256='0' * 64, source_root=str(source), build_dir=str(build),
                            production_off_python_path=str(production), artifact_parent=str(parent))
            cp = root / 'contract.json'
            cp.write_text(__import__('json').dumps(contract))
            with mock.patch.object(v.subprocess, 'check_output', side_effect=AssertionError('must refuse before subprocess')):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(v.Refusal):
                        v.main(['--contract', str(cp), '--artifact-root', str(parent / 'out')])
            self.assertFalse((parent / 'out').exists())


    def test_replay_creates_python_output_directory_for_real_protoc(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            source = root / 'source'
            build = root / 'original-build'
            stage = root / 'round-a'
            proto_dir = source / 'proto'
            proto_dir.mkdir(parents=True)
            stage.mkdir()
            proto = proto_dir / 'tiny.proto'
            proto.write_text('syntax = "proto3"; package fixture; message Tiny { string value = 1; }\n')
            rel = 'proto/tiny.proto'
            inputs = {rel: v.sha_file(proto)}
            recipe = {
                'segments': [[str(v.PROTOC), '--python_out=' + str(build / 'python'),
                              '--proto_path=' + str(proto_dir), str(proto)]],
                'outputs': {'python': str(build / 'python')}, 'copy': None,
            }
            stage_build = v.replay_round('tiny-protoc-regression', stage, source, build,
                                         [recipe], inputs, '1792600000')
            generated = v.generated_map(stage_build)
            self.assertEqual(set(generated), {'python/tiny_pb2.py'})
            self.assertTrue((stage_build / 'python').is_dir())

if __name__ == '__main__':
    unittest.main()