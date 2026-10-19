# M06 cache generator selection

`prepare` now retains the generator recorded in each existing build cache. It
accepts only Ninja and Unix Makefiles. The cache must contain one generator and
one canonical source-root entry that matches the source being configured.
Missing caches use Ninja. Malformed, duplicate, unsupported, or foreign-source
caches fail before the attempt output directory is created.

Test and production-off caches select their generators independently. This
prevents CMake from being invoked with a generator that conflicts with an
existing cache. Configure flags and build budgets remain unchanged.

## Validation

The source change is based on `1fe85fa688d3910fbd1fc158dd9c9ef82c643d1a`
on branch `m08/prepare-generator-cache-20261019`. The focused tooling file
passed 30 tests. No configure, build, CTest, or native execution was run.
