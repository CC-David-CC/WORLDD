# M06 post-run artifact manifest diagnosis

The preserved sb16 Debug gate completed its local test stages, then failed while hashing the artifact tree. The bounded-storage integration runs privileged storage fixtures and retains a sparse 2 GiB image plus an empty, root-owned mountpoint. The ordinary gate user cannot read those two paths. The original `PermissionError` was later replaced by a generic all-profile invariant error, which obscured the cause and the unrun sanitizer profile.

The gate now validates one exact bounded-storage fixture before excluding only `bounded-storage-git/volume.img` and `bounded-storage-git/volume` from the artifact hash map. It checks the reviewed five-scenario receipt, service and owner-unit cleanup, absent scope, successful unmount and detach receipts, real private paths, image size and ownership, an empty unmounted mountpoint, and no image-specific loop mapping. It records both exclusions and their cleanup witness. All other artifact files remain strictly hashed; symlinks, special files, and unreadable files fail closed. The regression suite also proves that a similarly named image elsewhere remains hashed. Source and runtime pin checks are unchanged.

Post-run reconciliation retains the first execution exception and traceback. It records failed and unrun per-profile checks separately, then keeps the run status failed. The regression fixtures cover the exact exclusion, bad cleanup receipts, links, unrelated image names, unreadable ordinary evidence, and preservation of the original failure when a later profile did not run.

This is a gate-tool correction. It does not establish M06 qualification. The modified gate has not been deployed, and the corrected full gate has not been run.
