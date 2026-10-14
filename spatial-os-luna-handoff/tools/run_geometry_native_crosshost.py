#!/usr/bin/env python3
"""Start one persistent native cross-host direction from a deployed-path manifest."""
import argparse
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--authority',choices=('sb16','sb16-2'),required=True)
    parser.add_argument('--run-id',required=True)
    options=parser.parse_args()
    if not re.fullmatch('[a-z0-9-]{1,48}',options.run_id):parser.error('Use a new lowercase run ID')
    manifest=json.loads(options.manifest.read_text())
    if manifest['profile'] not in ('debug','asan'):parser.error('Profile must be debug or asan')
    if set(manifest['hosts'])!={'sb16','sb16-2'}:parser.error('Both qualification hosts are required')
    for name,entry in manifest['hosts'].items():
        for field in ('source','bin','python','xserver','evidence','command_runner'):
            if not PurePosixPath(entry[field]).is_absolute():parser.error(name+' '+field+' must be absolute')
        address=ipaddress.ip_address(entry['address'])
        if address.is_loopback or address.is_unspecified:parser.error('Use a real reachable peer-host address')
    authority=options.authority
    observer='sb16-2' if authority=='sb16' else 'sb16'
    local,remote=(manifest['hosts'][name] for name in (authority,observer))
    directory=str(PurePosixPath(local['evidence'])/options.run_id/('authority-'+authority))
    remote_evidence=str(PurePosixPath(remote['evidence'])/options.run_id/('observer-for-'+authority))
    session='m06-geometry-'+manifest['profile']+'-'+authority+'-'+options.run_id
    environment=dict(
        PYTHONPATH=local['python']+':'+local['source'],
        SPATIAL_TEST_XSERVER=local['xserver'],SPATIAL_SECURITY_EVIDENCE=directory+'/services',
        SPATIAL_GEOMETRY_EVIDENCE=directory+'/geometry',
        SPATIAL_GEOMETRY_OBSERVER_HOST=observer,SPATIAL_GEOMETRY_AUTHORITY_ADDRESS=local['address'],
        SPATIAL_GEOMETRY_REMOTE_SOURCE=remote['source'],SPATIAL_GEOMETRY_REMOTE_BIN=remote['bin'],
        SPATIAL_GEOMETRY_REMOTE_PYTHON=remote['python'],SPATIAL_GEOMETRY_REMOTE_XSERVER=remote['xserver'],
        SPATIAL_GEOMETRY_REMOTE_EVIDENCE=remote_evidence,
        ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    binary=PurePosixPath(local['bin']);source=PurePosixPath(local['source'])
    command=['tmux','new-session','-d','-s',session,'bash',local['command_runner'],local['source'],directory,
        'env',*(name+'='+value for name,value in environment.items()),'python3',str(source/'tools/with_test_display.py'),
        '--','python3',str(source/'tests/core/test_geometry_native_crosshost.py'),
        str(binary/'identityd'),str(binary/'spatial-issuer-client'),str(binary/'worldd')]
    subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',authority,shlex.join(command)],check=True,timeout=15)
    print(json.dumps(dict(authority=authority,observer=observer,profile=manifest['profile'],tmux=session,
        run_directory=directory,source_commit=manifest.get('source_commit'),status='RUNNING_UNQUALIFIED')))


if __name__=='__main__':main()
