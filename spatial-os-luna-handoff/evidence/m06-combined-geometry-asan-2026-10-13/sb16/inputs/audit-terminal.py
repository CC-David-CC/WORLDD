import json,pathlib,re,hashlib,sys,subprocess
base=pathlib.Path('/home/dflanag3/spatial-qualification/m06-candidate-fc302-20261013')
profile=sys.argv[1]
attempt=base/(profile+'-runtime01'); outer=base/(profile+'-runtime-command01')
r=json.loads((attempt/'results.json').read_text())
assert all((outer/name).read_text().strip()==value for name,value in [('command.exit','0'),('wrapper.exit','0'),('terminal-status.txt','COMPLETE')])
assert len(r['selected'])==13 and len(set(r['selected']))==13 and set(r['passed'])==set(r['selected'])
assert not r['failed'] and not r['blocked'] and not r['unrun'] and not r['changed_inputs']
assert r['input_invariant']=='PASS' and r['input_pre']==r['input_post']
rows=[]
for name in r['selected']:
    record=json.loads((attempt/(name+'.result.json')).read_text())
    assert record['status']=='passed' and record['return_code']==0 and not record['timed_out'] and not record['interrupted'] and record['cleanup_resolved'] and not record['unexpected_group_members']
    directory=pathlib.Path(record['argv'][3])
    assert directory.parent==attempt
    assert all((directory/n).read_text().strip()==v for n,v in [('command.exit','0'),('wrapper.exit','0'),('terminal-status.txt','COMPLETE')])
    texts=[]; scans=0
    for root in (directory,attempt/'witness'/name):
        for p in root.rglob('*'):
            if p.is_file() and p.name.endswith(('.log','.stderr','.stdout','.txt')):
                text=p.read_text(errors='replace');scans+=1
                assert not re.search(r'AddressSanitizer|LeakSanitizer|UndefinedBehaviorSanitizer|runtime error:|SUMMARY:.*Sanitizer|ERROR:.*Sanitizer',text,re.I),str(p)
                assert not re.search(r'\bskipped\b|\bSKIP\b',text,re.I),str(p)
                texts.append(text)
    combined='\n'.join(texts)
    summaries=re.findall(r'Ran \d+ tests? in [^\n]+|^\d+ .* cases passed$',combined,re.M)
    markers=re.findall(r'^PASS ([^\r\n]+)$',combined,re.M)
    rows.append({'suite':name,'summaries':summaries,'pass_markers':len(markers),'scanned_logs':scans,'seconds':round(record['ended_epoch']-record['started_epoch'],3)})
processes=subprocess.check_output(['ps','-eo','pid,ppid,args'],text=True)
left=[line for line in processes.splitlines() if str(attempt) in line and 'audit-terminal.py' not in line]
assert not left,left
out={'host':r['host'],'profile':profile,'candidate_commit':r['candidate_commit'],'status':'PRIMARY_TERMINAL_AUDIT_PASS','suites':rows,'input_hashes':len(r['input_pre']),'remaining_attempt_processes':left}
dest=base/(profile+'-terminal-audit01.json');assert not dest.exists()
dest.write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
