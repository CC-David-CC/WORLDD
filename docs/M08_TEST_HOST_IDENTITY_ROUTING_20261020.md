# M08 test-host identity routing check

Date: 2026-10-20
Status: BLOCKED. Do not transfer test packages or launch qualification jobs until host identity is reconciled.

## Observations

The dedicated ROG-to-test SSH key works. No additional key is required.

The saved LAN routes to 10.0.7.49 and 10.0.7.94 return different hostnames and different SSH host keys. Both remote sessions expose the same ROG machine identity and execution context.

Tailscale lists sb16 at 100.87.42.112 and sb16-2 at 100.95.242.43. Tailscale ping reports those peers through 10.0.7.49:41641 and 10.0.7.94:41641. SSH to each Tailscale address succeeds when HostKeyAlias checks the already trusted LAN host key.

However, ROG and both named test endpoints report matching machine-id, boot-id, DMI product UUID, board serial, PID/network/mount namespace IDs, Wi-Fi MAC, LAN/Tailscale interface addresses, root filesystem UUID, NVMe serial, and NVMe WWN. The remote shell hostname and SSH key are distinct, but the host execution context is not.

This may be address routing, shared namespaces, or another host setup issue. The cause is not confirmed. Treat the two SSH names as unverified test hosts until corrected.

## Action

Wait for David to confirm whether these are intended isolated environments or to provide the correct physical test-host addresses. Then repeat the identity preflight. Do not run the two-host gate against aliases that share ROG's execution context.

No test package was transferred. No runtime test was launched.

## Candidate available

The sanitizer package is built and source-pinned to commit 136b7eb250d1b5ac04a31ec43a624bf2e1f7e5df. It remains on ROG pending valid test-host identity.

External package: /home/dflanag3/spatial-artifacts/m08-adapter-cleanup-timeout-fix-20261020/asan-package-v2.tar.gz
Package SHA-256: dacde77511dc97f73a677ac0b2fd33d71145705c6468ea8668536bf0bd99af0f
Source manifest SHA-256: dbcacadcca2783b9ac43670ad6d2d3e55b14baec81618c954f4817cef024e304
Package manifest SHA-256: a1f5baedbb845c934abb46f27ee6707b52a1ff43b2953b752c6de86ea5025e07
Build manifest SHA-256: 1d35c6f1b41c7253bd3553f7f958116aeb0d3a4e715b6f3ab379143de828de46
