# Persistent operator storage

Use sb16 and sb16-2. Keep their normal login and SSH recovery routes.
This procedure provisions storage. The retained session controller still supplies identity, native credentials, and world authorization.

Edit `config/operator-storage.textproto` before provisioning.
Its default reserves one 4 GiB image for David's broker.
Verify `broker_uid` with `id -u` on the destination host.
The default Git worker remains bounded to 256 MiB, 16 processes, and two CPUs.
Ordinary Git jobs retain 60-second deadlines. Imports retain five-minute deadlines.

Run these commands from the source directory.
Set `BUILD` to the reviewed Linux build directory.

```sh
sudo -n python3 tools/provision_storage.py create --bindings-dir "$BUILD/python" --definition config/operator-storage.textproto --apply
sudo -n python3 tools/provision_storage.py inspect --bindings-dir "$BUILD/python" --name david
```

Provisioning creates `/var/lib/spatial/volumes/david/volume.img` once.
The tool never formats an existing image.
The root-owned `record.pb` pins its inode, capacity, definition, and Btrfs UUID.
The image has allocated backing storage. It remains persistent after session shutdown.
The mount uses `nodev`, `nosuid`, and the Btrfs top-level subvolume.

Read the binary `runtime.pb` with the generated `StorageVolumeRuntime` binding.
Copy its resource template and owner launch configuration into the retained broker configuration.
Set `AuthoritySupervisorConfig.require_broker_confinement` to true.
Set both broker resource and storage descriptor targets.
Use `runtime.volume_root` as the broker's state directory.
The controller must provide its owned delegated cgroup with cpu, memory, pids, and io controllers.
The retained supervisor binds this template to the actual broker scope and broker pidfd.
Do not give the template, owner channel, controller descriptors, or administrative API to actors.

The direct private controller uses `BrokerProcessConfig.require_confinement`.
Both paths add `workspaced --production`.
That profile requires sealed aggregate resources and checked storage ownership.
It rejects fixture faults, live migration, mutable resource paths, and legacy resource envelopes.
Legacy unprofiled launches remain diagnostic component fixtures.
They do not qualify operator deployment.
Actor admission and execution authority retain their separate existing checks.

Stop the retained broker and confirm cleanup before unmounting.

```sh
sudo -n python3 tools/provision_storage.py unmount --bindings-dir "$BUILD/python" --name david --apply
sudo -n python3 tools/provision_storage.py mount --bindings-dir "$BUILD/python" --name david --apply
```

Unmount fails while the broker holds the volume.
Unmount removes the runtime descriptor after checked loop-device cleanup.
Remount verifies the persistent identity and recreates current I/O device limits.
Loop device numbers may change across mounts or reboots.
Mount the volume explicitly after reboot. Automatic operator-session startup remains later deployment work.
Do not delete the image, record, or owner policy during recovery.
A partial provisioning failure reports its exact commands and paths.
Inspect those paths before retrying. The tool refuses to overwrite a partial or existing volume.

This procedure does not replace the complete M03 qualification gate.
