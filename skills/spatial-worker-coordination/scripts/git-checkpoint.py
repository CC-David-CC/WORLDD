#!/usr/bin/env python3
"""Checked staged commits and explicit pushes. Does not stage or integrate files."""
import argparse
import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ORIGIN = "dflanag3@box-git:/srv/data/git/spacial_desktop.git"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("commit", "push"))
    p.add_argument("--checkout", required=True)
    p.add_argument("--branch", required=True)
    p.add_argument("--head", required=True, help="Full reviewed HEAD before this operation")
    p.add_argument("--origin", default=ORIGIN, help="Expected origin; override only for isolated tool tests")
    p.add_argument("--receipt", required=True, help="New JSON receipt outside the index")
    p.add_argument("--qualification", required=True, choices=("UNQUALIFIED", "FOCUSED-PASS"))
    p.add_argument("--path", action="append", default=[], help="Exact reviewed staged path; repeat for each file")
    p.add_argument("--message-file", help="Reviewed UTF-8 commit message")
    a = p.parse_args()
    root = Path(a.checkout).resolve(strict=True)
    receipt = Path(a.receipt).resolve()
    receipt.parent.mkdir(parents=True, exist_ok=True)
    # Reserve once. Repeated calls must never overwrite earlier failure evidence.
    with receipt.open("x", encoding="utf-8") as f:
        f.write("{}\n")
    record = dict(host=socket.gethostname(), checkout=str(root), branch=a.branch,
                  expected_head=a.head, qualification=a.qualification, action=a.action,
                  state="PREFLIGHT", started=datetime.datetime.now(datetime.timezone.utc).isoformat())

    def save():
        temporary = receipt.with_name(receipt.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, receipt)

    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
        if result.returncode:
            raise RuntimeError(f"git {args[0]} exited {result.returncode}: " +
                               result.stderr.decode("utf-8", errors="replace").strip())
        return result.stdout.decode("utf-8").strip("\r\n")

    try:
        save()
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
            raise RuntimeError("Checkout must be the repository root")
        if git("branch", "--show-current") != a.branch:
            raise RuntimeError("Branch differs from the assignment")
        if git("rev-parse", "HEAD") != a.head:
            raise RuntimeError("HEAD differs from the reviewed commit")
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
            path = Path(git("rev-parse", "--git-path", marker))
            if not path.is_absolute():
                path = root / path
            if path.exists():
                raise RuntimeError("Finish the existing Git operation before checkpointing: " + marker)
        if git("remote", "get-url", "--all", "origin") != a.origin or git("remote", "get-url", "--push", "--all", "origin") != a.origin:
            raise RuntimeError("Origin fetch/push destination differs from the assignment")
        staged = git("diff", "--cached", "--name-only", "--no-renames", "-z").split("\0")
        staged = [path for path in staged if path]
        if a.action == "commit":
            if not a.path or len(set(a.path)) != len(a.path) or set(staged) != set(a.path):
                raise RuntimeError("Staged files differ from the explicit reviewed path list")
            if not a.message_file:
                raise RuntimeError("Commit requires --message-file")
            if git("config", "--local", "user.name") != "David" or git("config", "--local", "user.email") != "david@code.contractors":
                raise RuntimeError("Repository-local Git identity differs from project identity")
            for identity in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
                if git("var", identity).partition(">")[0] + ">" != "David <david@code.contractors>":
                    raise RuntimeError("Effective Git identity differs from project identity: " + identity)
            relative_receipt = os.path.relpath(receipt, root).replace(os.sep, "/")
            if relative_receipt in staged:
                raise RuntimeError("Receipt must not be staged")
            git("diff", "--cached", "--check")
            record["staged_tree"] = git("write-tree")
            record["paths"] = staged
            save()
            git("commit", "--file", str(Path(a.message_file).resolve(strict=True)))
            record["commit"] = git("rev-parse", "HEAD")
            record["state"] = "LOCAL COMMIT"
            save()
            if git("rev-parse", "HEAD^{tree}") != record["staged_tree"] or git("rev-list", "--parents", "-n", "1", "HEAD").split() != [record["commit"], a.head]:
                raise RuntimeError("Created commit differs from reviewed tree or parent; inspect before pushing")
        else:
            if staged or a.path or a.message_file:
                raise RuntimeError("Push requires no staged changes, paths, or message")
            ref = "refs/heads/" + a.branch
            git("check-ref-format", ref)
            record["commit"] = a.head
            record["destination"] = ref
            record["state"] = "LOCAL COMMIT"
            save()
            git("push", "origin", a.head + ":" + ref)
            remote = git("ls-remote", "--exit-code", "origin", ref).split()
            if remote != [a.head, ref]:
                raise RuntimeError("Remote ref does not match expected commit")
            record["remote_commit"] = remote[0]
            record["state"] = "BOX-GIT VERIFIED"
        record["remaining_status"] = git("status", "--porcelain=v1")
        save()
        print(json.dumps(record, indent=2))
        return 0
    except Exception as error:
        record["error"] = str(error)
        try:
            record["observed_head"] = git("rev-parse", "HEAD")
        except Exception:
            pass
        save()
        print(json.dumps(record, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
