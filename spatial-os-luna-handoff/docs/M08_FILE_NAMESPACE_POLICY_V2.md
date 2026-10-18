# M08 cabinet file namespace policy v2

This document versions the cabinet namespace rule for registered Git-backed project roots.
It does not change workspace identity, storage binding, or Git worktree ownership.

The registered project root's `.git` entry is workspace administration. It is outside the cabinet `FILE_*` namespace. A genuinely empty committed project tree therefore presents zero project entries even though its real linked worktree retains `.git`.

The rule applies to root listing, handle resolution, and every cabinet file action. It rejects root `.git` as a source or destination for read, edit, create, rename, move, copy, trash, and restore. It also rejects legacy handles that name that exact root entry. Replies do not expose the reserved entry's metadata or handles.

The reservation covers the registered root `.git` entry and every descendant beneath it. It does not reserve `.git` beneath another project directory, other dotfiles, or ordinary files such as `README.txt`. Whole-workspace copy remains an administrative operation. It retains its required project tree, Git history, and trusted Git rebinding. Cabinet subtree copy cannot copy the project root or cross the reserved root `.git` boundary.

Activating policy v2 requires restarting affected file engines and world peers. Process-local handles and browse cursors must be discarded. The shared file-operation digest includes the policy version. Reuse of an existing operation ID under the new digest returns `CONFLICT` without changing its retained journal row or performing effects. Completed historical records remain intact.

A retained copy job whose recorded source or complete destination path is the reserved root `.git` is unsafe under v2. Poll returns `UNCERTAIN` and retains its saved evidence. On recovery, the engine quarantines the worktree before cleanup or publication. It does not invent worker-drain proof, remove staging, rewrite receipts, or undo an already published copy. Manual reconciliation requires a separate reviewed recovery action.

This rule hides the registered Git administration entry from cabinet file operations. It does not prevent an independently authorized application from copying bytes into an ordinary project file. Process confinement and Git authority remain separate controls.
