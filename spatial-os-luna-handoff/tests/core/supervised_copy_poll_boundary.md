# Supervised copy polling boundary

FileEngine::POLL can return RUNNING until the worker reports completion.
A single test poll may therefore finish before it drives the post-rename barrier.
The recovery fixture now repeats POLL for queued or running jobs and stops on a terminal state or transport closure.

The focused runtime batch covers three real supervised crash boundaries:

- Prepared output before publication; recovery cancels and removes the exact stage.
- Published output before directory sync; recovery syncs before reporting complete.
- Published output with injected directory-sync failure; recovery retains uncertainty and the exact stage.

Each case still uses the active generation, real SIGKILL, pidfd death observation, checked cleanup and fence, issuer retirement, and a fresh same-journal generation. Runtime execution remains pending on the designated test hosts.
