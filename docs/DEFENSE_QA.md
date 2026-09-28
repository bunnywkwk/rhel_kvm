# rhel_kvm: Questions to expect, and simple answers

One question, one short answer, per task. Read this before presenting so you have the "why" ready without opening the code. Order follows `tasks/main.yml`.

---

## `tasks/preflight.yml`

**Why check the OS before doing anything else?**
Everything after this depends on a matching vars file existing and on the right package names for that OS. Failing early with a clear message beats a confusing failure five tasks later.

**Why load `kvm`, `vhost_net` and `tun`?**
A hypervisor needs the kernel's virtualization support (`kvm`), fast VM networking (`vhost_net`), and virtual network interfaces (`tun`) before it creates any pool, network or VM.

**Who actually uses these — `libvirt`, or the packages?**
`qemu-kvm` — specifically the process that runs each VM (`qemu-system-x86_64`). `libvirt` only manages *when* a VM starts and *with what settings*; it doesn't touch these devices itself. Checked directly: the QEMU binary references `/dev/kvm`, `/dev/net/tun` and `/dev/vhost-net`; none of the `libvirtd`/`virtqemud`/`virtnetworkd` daemon binaries reference any of them. These modules have to exist in the kernel *before* the first VM starts, because QEMU can only open devices the kernel already offers — it can't create them itself. That's why the task is in preflight, ahead of anything else.

**`kvm` and `tun` show "ok" (no change) on the very first run — why, if we never loaded them?**
They're already loaded before Ansible runs. `kvm` auto-loads because the kernel checks the CPU at boot and loads the matching module (Intel VT-x / AMD-V) by itself. `tun` auto-loads because a default udev rule touches `/dev/net/tun` at every boot, and just touching that device is enough to trigger the load — no VM has to exist yet.

**Why does `vhost_net` show "changed"?**
It has no such early trigger. It only loads the moment QEMU actually starts a VM with accelerated networking, and by the time our role runs, no VM exists yet. So our task is the first thing that ever asks the kernel to load it — hence "changed", not "already there."

**If they're often already loaded, why bother writing them to `/etc/modules-load.d/kvm.conf`?**
`modprobe` only lasts until the next reboot — it isn't remembered on its own. And relying on "something else happens to load it" isn't something a reviewer can verify by reading configs. Writing them to `/etc/modules-load.d` makes it an explicit, declared requirement for this host, on every boot, instead of an implicit side effect of udev or CPU detection.

**What if the CPU doesn't actually support virtualization?**
`modprobe kvm` still runs without erroring, but there's no real acceleration behind it. VMs would run in slow software emulation or fail to start. This role assumes it runs on hardware where virtualization is already enabled — like a Proxmox VM with nested virtualization passed through.

---

## `tasks/packages.yml`

**Why update `redhat-release` before installing anything, and only on RHEL 10?**
RHEL 10 packages carry an extra signature type that an older image's keyring doesn't recognize yet. Updating this one small package brings in the current signing keys, and installs then succeed. RHEL 9 packages don't use that signature, so it's skipped there.

**Why are the core packages in `vars/main.yml` and not `defaults/main.yml`?**
`defaults/` can be replaced by a user's own settings. If someone set their own package list there, the whole list — including `qemu-kvm` and `libvirt` — would be wiped out. `vars/` can't be overridden that way, so the core packages are protected.

**What is `rhel_kvm_extra_packages` for?**
A safe place to add extra tools (like `guestfs-tools`) without touching or risking the protected core list.

---

## `tasks/sysctl.yml`

**Why turn on IPv4 forwarding?**
The built-in `default` network is NAT — it lets VMs reach the internet through the host. NAT only works if the kernel is allowed to forward packets between the VM bridge and the outside.

**Why write it to `99-kvm.conf` specifically?**
Sysctl files load in name order, and the last one read wins. CIS hardening writes `ip_forward = 0` into a file named `60-*.conf`. Naming ours `99-kvm.conf` guarantees ours is read after CIS's, so hardening doesn't break VM internet access.

**Why also handle IPv6?**
Same idea, only if the host actually has an IPv6 address — it's skipped otherwise. Honestly: no network in this role uses IPv6 today, so this task has nothing to act on yet. It's there for when one does.

---

## `tasks/daemons.yml`

**Why does RHEL 9 do less here than RHEL 10?**
RHEL 9 only needs `libvirtd` started — the one monolithic daemon this project requires there. RHEL 10 has no `libvirtd` at all; it uses six separate daemons instead, so more units need enabling.

**Why mask units on RHEL 10 but nothing on RHEL 9?**
On RHEL 10, leftover `libvirtd` units could still exist and conflict, so they're masked so they never interfere. On RHEL 9 the modular daemons are currently left alone, to keep the role simple.

**Isn't there a risk `libvirtd` loses to the modular daemons on RHEL 9 after a reboot?**
Yes. RHEL 9 enables the modular sockets by default on a fresh install, and they can win the race after a reboot. Re-running the playbook restores `libvirtd`, because starting it explicitly stops the conflicting daemons (they declare `Conflicts=` on each other in their own unit files). This is a known, accepted trade-off for simplicity, not an oversight.

**Why loop over a list instead of writing "if RHEL 9 do X else do Y"?**
The OS-specific vars file already decided what belongs in the list. The task doesn't need to know which OS it's on — it just enables and starts whatever the list contains. Supporting a new OS later means adding a vars file, not editing this task.

---

## SELinux, in plain words (say this if they ask "why SELinux at all")

**What does normal Linux permission checking do, and what does SELinux add?**
Normal Unix permissions (owner, group, mode — `rwx`) ask one question: *are you the right user?* If you own the file, or you're root, you get in — full stop. SELinux adds a second, independent lock on top of that. Every file and every running process carries a label, and the kernel additionally checks: *is a process with this label allowed to touch a file with that label?* Even root can be blocked by this second check — normal permissions are not enough on their own.

**Why does that matter for a hypervisor specifically?**
If an attacker ever breaks into some process on the host, SELinux is what stops that process from reading or writing files it has no real business touching, even if the plain file permissions would technically allow it. For a hypervisor, that means a compromised process can't just reach into another VM's disk file — it's confined to only what its own label is allowed to see.

**What does a label actually look like?**
Confirmed on a live SELinux-enforcing host:
```
$ ls -Zd /var/lib/libvirt
system_u:object_r:virt_var_lib_t:s0 /var/lib/libvirt
```
Four fields (user:role:**type**:level). In day-to-day practice, almost every access rule is written against the **type** field — that's the part that matters here.

**Why does `/var/lib/libvirt` itself carry a different type (`virt_var_lib_t`) than what we set (`virt_image_t`)?**
This is the proof that the label isn't automatic. Even libvirt's own top-level directory is labeled as "libvirt's general files", not as "a VM disk image". A folder doesn't get the VM-disk label just by living under `/var/lib/libvirt` — something has to set it explicitly. That's exactly the gap our storage task fills.

**So why `virt_image_t` specifically on the storage pool directories?**
QEMU (the process that actually runs a VM) runs under its own confined SELinux domain (libvirt's `sVirt` system). The policy only allows that domain to open files labeled `virt_image_t` (and a few related types) — nothing else. If our storage folder kept its parent's default label instead, QEMU would get "permission denied" opening a VM's disk the instant it tried, even though the file's ordinary owner/group/mode look completely fine. The label is what libvirt's own confinement checks against.

**Why check `ansible_facts['selinux']['status'] == 'enabled'` before doing any of this?**
On a host where SELinux is disabled, none of this applies — there's no policy to update and no label to set. The check lets the role skip cleanly there instead of running a command that does nothing or errors.

**Why not just disable SELinux and avoid this step entirely?**
Because it's a hardening requirement, not an obstacle to work around. The CIS role has its own dedicated SELinux section (its rules are only skipped if an admin explicitly opts out with `rhel9cis_selinux_disable` / `rhel10cis_selinux_disable`, which we don't). The right answer is to work correctly with SELinux enforcing, which is exactly what this task does — not to turn off a security control to save one step.

---

## `tasks/storage.yml`

**Why mode `0711` on the storage folder?**
It lets the `qemu` process pass through the directory to reach files inside, but blocks ordinary users from listing or reading what's there. VM disk images stay private.

**Why the `virt_image_t` SELinux label?**
Under SELinux enforcing, `qemu` is only allowed to read and write files carrying this exact label. Without it, VM disks fail with "permission denied" even though the file permissions look fine.

**Why label it twice — once on the folder, once with `sefcontext`?**
The folder task applies the label right now, so it works immediately. `sefcontext` records the same rule in SELinux's policy database, so the label survives a full filesystem relabel later. One is immediate, the other is permanent.

**Why define, then activate, then autostart, as three separate tasks?**
A pool has to exist (defined) before it can be started (active). Autostart is its own task because the `virt_pool` module silently ignores the autostart setting whenever it's combined with a state change in the same task — so the pool would quietly fail to come back after a reboot. Found this the hard way during testing.

**Why is `default` just a starting value instead of something the role always forces?**
So the behavior is honest: if you replace the list with your own pools, only your pools get created — nothing hidden is forced back in behind your decision. Keep `default` by listing it again yourself.

---

## `tasks/networks.yml`

**Why create a second network (`kvm_br0`) when `default` already exists?**
`default` already gives VMs internet access. `kvm_br0` is a separate, private segment for VM-to-VM and VM-to-host traffic that doesn't need or want internet exposure — a deliberate second purpose, not a duplicate.

**Why isolated instead of NAT?**
`default` already covers internet access, so a second NAT network would be redundant. Removing the `<forward mode='nat'/>` line makes `kvm_br0`'s internal-only purpose explicit.

**Same three-task split as storage (define/activate/autostart) — why?**
Same reason: `virt_net` also ignores autostart when combined with a state change, so it has to be its own task or the network won't survive a reboot.

---

## Honest weak spots (say these before someone else finds them)

- RHEL 9's monolithic requirement can be temporarily lost after a reboot, until the playbook re-runs (see `daemons.yml` above).
- The IPv6 forwarding task has no network in this role that uses it yet.
- The role assumes virtualization hardware is already enabled; it does not check for it.
