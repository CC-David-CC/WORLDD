#!/usr/bin/env python3
"""Redirect legacy locked tools without changing acceptance source or hashes."""
import argparse, os
from pathlib import Path

def configure(checkout,artifacts):
    root=Path(checkout).resolve(strict=True); out=Path(artifacts).resolve()
    if out==root or root in out.parents: raise ValueError('Artifacts must be outside the checkout')
    out.mkdir(parents=True,exist_ok=True)
    for component in ('spatial-os-luna-handoff','sovereign-kernel-local-first-handoff-v0.1.0'):
        if not (root/component).is_dir(): continue
        for name in ('evidence','local'):
            target=out/component/name; target.mkdir(parents=True,exist_ok=True)
            link=root/component/name
            if link.is_symlink():
                if link.resolve()!=target: raise ValueError('Existing link has a different target: '+str(link))
            elif link.exists(): raise ValueError('Refuse existing data directory: '+str(link))
            else: link.symlink_to(target,target_is_directory=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('checkout'); p.add_argument('artifacts'); a=p.parse_args()
    configure(a.checkout,a.artifacts)
