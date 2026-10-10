"""Scan verified evidence attachments without treating a clean scan as qualification."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

parser = argparse.ArgumentParser()
parser.add_argument('review', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
root = args.review.resolve().parent
review_bytes = args.review.read_bytes()
review = json.loads(review_bytes)
attachments = review['attachments']
assert attachments, 'No evidence attachments were supplied'
pattern = re.compile(
    rb'(?:ERROR|WARNING|SUMMARY): (?:AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer)'
    rb'|(?:AddressSanitizer|UndefinedBehaviorSanitizer):(?:DEADLYSIGNAL| CHECK failed)'
    rb'|LeakSanitizer has encountered a fatal error|runtime error:')
scanned = {}
hits = []
for name, expected in attachments.items():
    relative = PurePosixPath(name)
    assert not relative.is_absolute() and '..' not in relative.parts
    path = root.joinpath(*relative.parts)
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == expected, name
    if path.suffix not in ('.log', '.txt', '.xml'):
        continue
    scanned[name] = expected
    for line_number, line in enumerate(data.splitlines(), 1):
        if pattern.search(line):
            hits.append(dict(file=name, line=line_number, text=line.decode('utf-8', errors='replace')))
assert scanned, 'No raw text logs were supplied'
result = dict(status='sanitizer_markers_present' if hits else 'no_sanitizer_markers_in_verified_logs',
    input_review_sha256=hashlib.sha256(review_bytes).hexdigest(),
    scanner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    verified_attachments=len(attachments), scanned_logs=scanned, findings=hits,
    phase_complete=False,
    limitation='This scan checks preserved log bytes. It does not establish test execution, coverage, sanitizer instrumentation, or phase completion.')
with args.output.open('x', encoding='utf-8', newline='\n') as stream:
    stream.write(json.dumps(result, indent=2)+'\n')
print(json.dumps(dict(status=result['status'], verified_attachments=len(attachments),
    scanned_logs=len(scanned), findings=len(hits))))
raise SystemExit(1 if hits else 0)
