#!/usr/bin/python3
import os
import shlex
import sys
try:
    command = shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
except ValueError:
    command = []
if len(command) != 2 or command[0] not in {'git-upload-pack', 'git-receive-pack'} or command[1] != '/srv/data/git/spacial_desktop.git':
    print('This key permits Git access to spacial_desktop only.', file=sys.stderr)
    sys.exit(1)
program = '/usr/bin/' + command[0]
os.execve(program, [program, command[1]], {'PATH': '/usr/bin:/bin', 'HOME': '/home/dflanag3', 'LANG': 'C'})
