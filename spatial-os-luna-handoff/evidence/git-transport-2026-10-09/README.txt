Direct box-git repository transport, 2026-10-09

Both world hosts can now access origin directly.
The verified repository tip was 71386ba45d9f55368e5b1bb53913bbf30bcdce27.
No fetch, merge, commit, or push changed source during this repair.
The pixel-observation qualification jobs continued with unchanged source inputs.

The initial failure was missing server trust.
The controller read box-git's public Ed25519 host key through its existing authenticated SSH connection.
Both hosts' independently observed keys matched that reference exactly.
The verified fingerprint is SHA256:bvx5684zHcsZwOy//i6Gw3+6eXHyKX4OT1d7EpzdHhc.
Each host appended this key to its own known_hosts after preserving a backup.
The next repository probe failed authentication. These failures are retained in the trust reports.

Each host then generated a separate Ed25519 key for this repository.
Each private key stayed on its originating host at ~/.ssh/spacial_desktop_boxgit.
Box-git authorized the public keys with OpenSSH restrict and a forced command.
repository_gate.py is the exact installed command source.
It accepts only git-upload-pack and git-receive-pack for /srv/data/git/spacial_desktop.git.
It rejects additional arguments and all other commands and repository paths.
It invokes an absolute executable with an explicit environment and no shell expansion.
Existing authorized keys remained intact.

Each checkout now has a repository-local core.sshCommand.
It selects only the dedicated key, disables agent use, and requires strict host-key verification.
No global SSH configuration or SSH daemon configuration changed.
The controller's existing administrative SSH route remains available.

Each host passed seven checks:
- Git ls-remote with the explicit key.
- Git push --dry-run to main. The result was Everything up-to-date.
- Rejection of an arbitrary shell command.
- Rejection of another repository path.
- Rejection of extra receive-pack arguments.
- Rejection of an empty command.
- Git ls-remote through the final repository-local configuration.

The access reports preserve exact commands, exit codes, and output.
The installation report records backup paths and before/after hashes.
Private keys, complete authorized_keys files, and Git configuration backups are not included here.
The primary agent reviewed the scripts and these access checks. No local model was used.
This qualifies repository transport only. It does not complete M16 or Spatial-M1.

Recovery commands

On box-git, restore the prior authorization only if the current file still matches this recorded change:

echo '334812066458e6c6f0756524ea8b72b356c969d5b180dba256546f304c556a24  /home/dflanag3/.ssh/authorized_keys' | sha256sum --check --status && cp -p -- /home/dflanag3/.ssh/authorized_keys.before-spacial-hosts-20261009 /home/dflanag3/.ssh/authorized_keys

This removes the two new authorizations and preserves the prior administrative keys.
If the hash differs, review later edits and remove only the two spacial_desktop host entries.
Keep the backup and inactive private keys until diagnosis is complete.

On each world host, remove only this repository's SSH override after checking its current value:

cd /home/dflanag3/src/spacial_desktop
test "$(git config --local --get core.sshCommand)" = 'ssh -i /home/dflanag3/.ssh/spacial_desktop_boxgit -o IdentitiesOnly=yes -o IdentityAgent=none -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10' && git config --local --unset core.sshCommand

The original Git config backup remains at .git/config.before-spacial-boxgit-20261009.
Restore the original known_hosts only if its current hash still matches this change:

echo 'f4f22911f78c339ce92c2aa79d66e626a91df786a18c1e72aee4ed31821c4610  /home/dflanag3/.ssh/known_hosts' | sha256sum --check --status && cp -p -- /home/dflanag3/.ssh/known_hosts.before-spacial-box-git-20261009 /home/dflanag3/.ssh/known_hosts

If that hash differs, review later edits and remove only the verified box-git line.
No rollback command was executed during qualification.
