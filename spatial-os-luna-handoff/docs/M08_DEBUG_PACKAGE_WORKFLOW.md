# M08 Debug qualification package workflow

Use `tools/package_debug_qualification.sh` to build a fresh Debug qualification package. Pass the Git worktree root, which contains `.git`, as its source checkout. Do not pass the nested project directory. The script archives the pinned source and configures CMake at the final package paths. It builds every default target with two jobs.

The script copies CTest registration files into a separate writable stage. It adds explicit per-test working directories that point to the package build. CTest list and JSON output stay in that stage. The frozen package contains no CTest `Testing` directory.

Example on middle-child:

```sh
bash spatial-os-luna-handoff/tools/package_debug_qualification.sh \
  /home/dflanag3/src/m08-package-closure-relocation-20261018 \
  /home/dflanag3/spatial-artifacts/m08-package-closure-relocation-20261018/package-debug-UNQUALIFIED-<commit> \
  /home/dflanag3/spatial-artifacts/m08-package-closure-relocation-20261018/package-debug-UNQUALIFIED-<commit>.tar.gz \
  /home/dflanag3/spatial-artifacts/m08-package-closure-relocation-20261018/ctest-stage-<commit>
```

Run through the installed `run-command.sh` inside persistent tmux. The script refuses existing output paths and requires at least 10 GiB free before building. It performs registration-only CTest inspection. It does not run tests or deploy the package. A successful package remains UNQUALIFIED until runtime qualification passes.