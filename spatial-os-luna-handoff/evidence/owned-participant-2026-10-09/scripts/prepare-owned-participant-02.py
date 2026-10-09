from pathlib import Path
r=Path(__file__).parent
s=(r/'qualify-owned-participant-01.py').read_text(encoding='utf-8')
s=s.replace("out=r/'local/m02-owned-participant-01'","out=r/'local/m02-owned-participant-02'")
s=s.replace("'-DSPATIAL_SANITIZERS='+sanitize","'-DSPATIAL_INPUT_TESTS=ON','-DSPATIAL_SANITIZERS='+sanitize")
s=s.replace("env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],","env=dict(os.environ,SPATIAL_TEST_XSERVER=server['path'],SPATIAL_XPROBE=str(build/'spatial-xprobe'),")
start=s.index("('build',[");end=s.index("\n        ('harness'",start)
s=s[:start]+"('build',['cmake','--build',str(build),'--parallel','2']),"+s[end:]
# Do not overlap another run of the same frozen candidate's transient native units.
s=s.replace("out=r/'local/m02-owned-participant-02';", "prior=json.loads((r/'local/m02-owned-participant-01/debug/run.json').read_text())\nassert prior['results'][-1]['name']=='focused' and prior['results'][-1]['exit_code']==8\nout=r/'local/m02-owned-participant-02';")
(r/'qualify-owned-participant-02.py').write_text(s,encoding='utf-8',newline='\n')
s=(r/'collect-owned-participant-01.py').read_text(encoding='utf-8').replace('m02-owned-participant-01','m02-owned-participant-02').replace("+'-01.tar.gz'","+'-02.tar.gz'")
(r/'collect-owned-participant-02.py').write_text(s,encoding='utf-8',newline='\n')
print('Prepared output02: same frozen source, complete build targets, native input enabled, independent xprobe configured.')
