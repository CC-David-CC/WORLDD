# M08 qualification package workflow

Use tools/package_debug_qualification.sh to build one profile package.
Pass the Git worktree root as the source checkout.
It must contain .git.
Do not pass the nested project directory.

The profile argument must be Debug or ASan-UBSan.
Both profiles use Ninja and CMAKE_BUILD_TYPE=Debug.
Both set BUILD_TESTING=ON and SPATIAL_INPUT_TESTS=ON.
Debug sets SPATIAL_SANITIZERS=OFF.
ASan-UBSan sets SPATIAL_SANITIZERS=ON.
Each profile uses a separate build directory.

Run the focused profile and registration check first:

~~~sh
python3 spatial-os-luna-handoff/tools/test_package_qualification_profiles.py
~~~

Build each profile with a new package, archive, and CTest stage.
Do not reuse output paths.

~~~sh
bash spatial-os-luna-handoff/tools/package_debug_qualification.sh \
  /home/dflanag3/src/REVIEWED-CHECKOUT Debug \
  /home/dflanag3/spatial-artifacts/PROJECT/package-debug-UNQUALIFIED-COMMIT \
  /home/dflanag3/spatial-artifacts/PROJECT/package-debug-UNQUALIFIED-COMMIT.tar.gz \
  /home/dflanag3/spatial-artifacts/PROJECT/ctest-debug-COMMIT
~~~

~~~sh
bash spatial-os-luna-handoff/tools/package_debug_qualification.sh \
  /home/dflanag3/src/REVIEWED-CHECKOUT ASan-UBSan \
  /home/dflanag3/spatial-artifacts/PROJECT/package-asan-ubsan-UNQUALIFIED-COMMIT \
  /home/dflanag3/spatial-artifacts/PROJECT/package-asan-ubsan-UNQUALIFIED-COMMIT.tar.gz \
  /home/dflanag3/spatial-artifacts/PROJECT/ctest-asan-ubsan-COMMIT
~~~

Replace each placeholder with a real checkout and full commit pin.
Use unique output paths.
The caller must launch each package command in a persistent tmux wrapper.
Record the exact command, source pin, output paths, and exit receipts.
The package script does not start tmux.
It records profile, sanitizer, generator, source commit, and build path.

The script keeps its inventory and manifest checks.
It checks archive hashes and relocation.
It checks dependencies and removes only verified duplicate data.
CTest metadata stays in a separate writable stage.
Each test points to the package build directory.
The script checks every test in the SPATIAL_INPUT_TESTS CMake block against CTest.
It also checks the previously missing executable set.

The script lists tests but does not run them.
It does not deploy the package.
The frozen package contains no CTest Testing directory.
A package remains UNQUALIFIED until runtime qualification passes.
