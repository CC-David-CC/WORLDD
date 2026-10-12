#!/usr/bin/env python3
import json, os, subprocess, sys, tempfile
from pathlib import Path

root=Path(sys.argv[1]).resolve()
drivers=[Path(sys.argv[2]).resolve(),Path(sys.argv[3]).resolve()]
report={"status":"PENDING","checks":[]}
for driver in drivers:
    label=driver.name
    with tempfile.TemporaryDirectory(prefix="m06-guard-") as temp:
        d=Path(temp); marker=d/"imported.marker"; fake=d/"fake_fixture.py"
        fake.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('imported')\nimport sys\nsys.argv=[sys.argv[0]]\n",encoding="utf-8")
        writable=d/"evidence"; writable.mkdir()
        fixture_args=[str(fake),"/usr/bin/true","/usr/bin/true"]
        expected=("has no attribute 'GeometryViewTests'" if "single_case" in label else "expected ClientTests fixture class is missing")
        if "single_case" in label: fixture_args.append("/usr/bin/true")
        fixture_args.append("sentinel_method")
        args=[sys.executable,str(driver),*fixture_args]
        cases=[("missing",None,"SPATIAL_SECURITY_EVIDENCE is required"),
               ("relative","relative-evidence","must be absolute"),
               ("unwritable","/proc","is not writable")]
        for case,path,needle in cases:
            env={"PATH":"/usr/bin:/bin","HOME":str(d)}
            if path is not None: env["SPATIAL_SECURITY_EVIDENCE"]=path
            proc=subprocess.run(args,env=env,capture_output=True,text=True,timeout=5)
            if proc.returncode==0 or needle not in proc.stderr or marker.exists():
                raise AssertionError(f"{label} {case}: rc={proc.returncode} stderr={proc.stderr!r} imported={marker.exists()}")
            report["checks"].append({"driver":label,"case":case,"exit":proc.returncode,"rejected_before_import":True,"stderr":proc.stderr.strip()})
        env={"PATH":"/usr/bin:/bin","HOME":str(d),"SPATIAL_SECURITY_EVIDENCE":str(writable)}
        proc=subprocess.run(args,env=env,capture_output=True,text=True,timeout=5)
        if proc.returncode==0 or expected not in proc.stderr or not marker.exists():
            raise AssertionError(f"{label} valid: rc={proc.returncode} stderr={proc.stderr!r} imported={marker.exists()}")
        report["checks"].append({"driver":label,"case":"valid-writable-directory","guard_passed":True,"fixture_imported_after_guard":True,"later_expected_failure":expected})
report["status"]="PASS"
(root/"guard-check-report.json").write_text(json.dumps(report,sort_keys=True,indent=2)+"\n")
print(json.dumps(report,sort_keys=True))
