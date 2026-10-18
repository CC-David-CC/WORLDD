# M08 Debug qualification package workflow

Use `tools/package_debug_qualification.sh` to build a fresh Debug qualification package. It archives the pinned source into the package and configures CMake directly at the final package source and build paths. It builds every default target with two jobs. It performs registration-only CTest inspection and rejects missing executables or noncanonical CTest paths. It validates source, build, and package manifests, checks ELF dependencies, freezes the package, and writes a checksummed archive.

Example on middle-child:

```sh
bash spatial-os-luna-handoff/tools/package_debug_qualification.sh \
  /home/dflanag3/src/m08-package-closure-relocation-20261018/spatial-os-luna-handoff \
  /home/dflanag3/spatial-artifacts/m08-package-closure-relocation-20261018/package-debug-UNQUALIFIED-<commit> \
  /home/dflanag3/spatial-artifacts/m08-package-closure-relocation-20261018/package-debug-UNQUALIFIED-<commit>.tar.gz
```

Run through the installed `run-command.sh` inside a persistent tmux session. The script refuses existing output paths and requires at least 10 GiB free before building. It does not run tests or deploy the package. A successful package remains UNQUALIFIED until its required runtime qualification passes.