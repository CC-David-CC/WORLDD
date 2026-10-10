from pathlib import Path
import os,subprocess,sys
out=Path(sys.argv[1])
for kind,binary in [('selected','trace-probe'),('unselected','trace-other')]:
 cmd=['systemd-run','--user','--wait','--pipe','--collect','--unit=spatial-trace-proof-'+str(os.getpid())+'-'+kind,'--property=RuntimeMaxSec=10',str(out/binary),kind,str(out/(kind+'.data'))]
 subprocess.run(cmd,check=True,timeout=15)
