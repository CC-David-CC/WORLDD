#!/usr/bin/env python3
"""Verify this evidence bundle without extraction, network access, or runtime tests."""
import hashlib
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parent

def sha(data):
    return hashlib.sha256(data).hexdigest()

manifest = json.loads((ROOT / 'manifest.json').read_text())
for item in manifest['files']:
    data = (ROOT / item['path']).read_bytes()
    assert len(data) == item['size'], item['path']
    assert sha(data) == item['sha256'], item['path']
results = json.loads((ROOT / 'results.json').read_text())
expected = {'world-authority': 27, 'actor-geometry-world-headless': 9,
            'actor-geometry-client-hostile-peer': 19, 'actor-session-client': 51,
            'actor-session-client-tls': 31, 'actor-transition-coordinator': 35,
            'actor-transition-real-world': 15, 'session-application': 3,
            'world-identity': 27}
pin = 'd0194a7e30350f155e1b74e1e9d5c9b775e8adc0'
verified = 0
for host in ('sb16', 'sb16-2'):
    receipt = json.loads((ROOT / (host + '-collection-receipt.json')).read_text())
    with tarfile.open(ROOT / (host + '-raw.tar.gz'), 'r:gz') as tf:
        def raw(relative):
            member = tf.getmember(host + '/' + relative)
            assert member.isfile(), relative
            return tf.extractfile(member).read()
        def text(relative):
            return raw(relative).decode()
        for item in receipt['items']:
            data = raw(item['relative_path'])
            assert len(data) == item['size'] and sha(data) == item['sha256'], item['relative_path']
            assert not item['credential_pattern_hits'], item['relative_path']
            verified += 1
        qraw = raw('inputs/qualification-input.json')
        assert sha(qraw) == '46ee59d020ba998c751798ed9f98e142b20b7b2f7b54bb0cee454d6d5cf526e0'
        q = json.loads(qraw)
        overlay_raw = raw('inputs/overlay/overlay.json')
        assert sha(overlay_raw) == '2812589c0cae4bd3caa4406e577e0d27ebf6cbc7d8c67a6faba09a934d5e8323'
        overlay = json.loads(overlay_raw)
        assert overlay['source_commit'] == pin
        for item in overlay['files']:
            assert sha(raw('inputs/overlay/' + item['sourcefile'])) == item['new_sha256']
        supplement_raw = raw('inputs/supplement-debug/manifest.json')
        assert sha(supplement_raw) == 'e334fe423df829f1044185b4daa732f4afd059907cbe9e3f8294b1539f668383'
        supplement = json.loads(supplement_raw)
        assert supplement['profile'] == 'Debug' and supplement['source_commit'] == pin
        assert supplement['archive_sha256'] == q['profiles']['debug']['archive_sha256']
        assert supplement['artifacts'][0]['sha256'] == '7f1d73f2d329f2b761baf851e0a27f05a3e95b9d3c1bd098dc17b25fd485c72f'
        successful = {}
        for row in results['rows']:
            if row['host'] != host:
                continue
            attempt = row['attempt']
            fact = next(f for f in receipt['attempt_facts'] if f['attempt'] == attempt)
            directory = next(n for n in fact['suite_directories'] if n[3:] == row['suite'])
            prefix = attempt + '/' + directory + '/'
            log = text(prefix + 'stdout.log') + '\n' + text(prefix + 'stderr.log')
            command = int(text(prefix + 'command.exit').strip())
            wrapper = int(text(prefix + 'wrapper.exit').strip())
            assert command == row['command_exit'] and wrapper == row['wrapper_exit']
            summary = re.search(r'^Ran (\d+) tests in [\d.]+s$', log, re.M)
            if summary:
                assert int(summary[1]) == expected[row['suite']]
            else:
                assert len(re.findall(r'^PASS ', log, re.M)) == expected[row['suite']]
            if row['status'] == 'PASS':
                assert command == wrapper == 0
                if summary:
                    assert re.search(r'^OK$', log, re.M) and 'FAILED (' not in log
                successful[row['suite']] = expected[row['suite']]
            else:
                assert row['suite'] == 'actor-transition-real-world' and attempt == 'debug02'
                assert command == wrapper == 1
                assert 'FAILED (errors=3)' in log
                assert log.count('FileNotFoundError: [Errno 2] No such file or directory:') == 3
                assert 'bundle/bin/spatial-process-scope-probe' in log
        assert successful == expected and sum(successful.values()) == 217
        for fact in receipt['attempt_facts']:
            attempt = fact['attempt']
            selection = json.loads(raw(attempt + '/selection.json'))
            preflight = json.loads(raw(attempt + '/preflight.json'))
            assert selection['profile'] == preflight['profile'] == 'Debug'
            assert selection['archive_sha256'] == q['profiles']['debug']['archive_sha256']
            assert sha(raw(attempt + '/bundle-SHA256SUMS')) == q['profiles']['debug']['manifest_sha256']
            assert preflight['fixture_overlay']['source_commit'] == pin
            assert preflight['fixture_overlay']['overlay_spec_sha256'] == sha(overlay_raw)
            assert fact['source18_verified'] and len(fact['source18_verification']) == 18
            assert all(x['sha256'] == x['expected'] for x in fact['source18_verification'])
            assert fact['tmux_has_session_exit'] == 1 and not fact['current_matching_debug_run_processes']
            code = int(text(attempt + '/outer-runner/command.exit').strip())
            assert code == int(text(attempt + '/outer-runner/wrapper.exit').strip())
            assert code == (1 if attempt == 'debug02' else 0)
            if attempt == 'debug-retry3':
                assert 'completed 3 of 9' in text(attempt + '/suite.complete')
                assert fact['helper_present'] and fact['helper_sha256'] == supplement['artifacts'][0]['sha256']
                assert selection['artifact_supplement_sha256'] == sha(supplement_raw)
                assert preflight['artifact_supplement']['source_commit'] == pin
            else:
                assert not fact['helper_present']
        assert receipt['count_source']['full_source_sha256'] == q['source_overlay_sha256']['spatial-os-luna-handoff/src/testing/actor_session_client_probe.cpp']
        full_client = text('debug02/04-actor-session-client/stdout.log')
        assert '53 actor-session-client probe cases passed' in full_client
        source_lines = '\n'.join(l for r in receipt['count_source']['ranges'] for l in r['lines'])
        assert 'expect_closed_after_failed_key_load();' in source_lines
        assert 'expect_invalid_descriptor_rejected();' in source_lines
        assert 'std::size_t count = 2;' in source_lines
    print(host + ': all 9 selected Debug suites PASS across attempts; 217 credited named checks/unittest cases; 3 earlier missing-helper errors preserved')
assert results['credited_named_checks_and_unittest_cases_both_hosts'] == 434
print('Verified ' + str(verified) + ' exact original files. ASan and the M06 gate are outside this evidence bundle.')
