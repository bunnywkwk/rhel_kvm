# rhel_kvm: Task Walkthrough

Every task, in the order it runs: **what it does**, **why it is there**, and an **error seen** where a real error led to it. "None seen" means the task was never the cause of a failure.
Errors quoted here were hit on the test hosts; the full stories are in the orchestrator's `docs/LESSONS_LEARNED_AND_FIXES.md` (item numbers in brackets).

## Execution order

| # | File | Purpose |
| :- | :--- | :--- |
| 1 | `tasks/main.yml` | Loads the OS-specific vars, then runs the steps below in order |
| 2 | `tasks/preflight.yml` | Checks OS + CPU, loads and persists kernel modules |
| 3 | `tasks/packages.yml` | Installs the KVM/libvirt packages |
| 4 | `tasks/sysctl.yml` | Turns on IP forwarding |
| 5 | `tasks/daemons.yml` | Starts the right libvirt daemons for the OS |
| 6 | `tasks/storage.yml` | Creates the VM disk directory, SELinux label, storage pool |
| 7 | `tasks/networks.yml` | Creates the NAT network `kvm_br0` |
| 8 | `tasks/users.yml` | Lets admin users manage VMs, sets the default `virsh` connection |

Big idea: **one role, two libvirt designs.** RHEL 9 must run the single `libvirtd` daemon (a fixed requirement); RHEL 10 only has separate `virtqemud` / `virtnetworkd` / `virtstoraged` daemons. The role does not branch with `if`. It loads a vars file per OS, and every task loops over whatever that file defines.

---

## 1. `tasks/main.yml`

```yaml
- name: Include OS-specific variables
  ansible.builtin.include_vars: "{{ ansible_facts['os_family'] }}-{{ ansible_facts['distribution_major_version'] }}.yml"
```
- **What:** builds the file name from facts, e.g. `RedHat-9.yml` or `RedHat-10.yml`, and loads it.
- **Why:** this is the only place the OS decision is made. The vars file decides which sockets/services to start, which to mask, which URI to use and whether to update `redhat-release`. Adding an OS later means adding a file, not editing tasks.
- **Error seen:** none.

```yaml
- name: Run pre-flight hardware and OS validation
  ansible.builtin.include_tasks: preflight.yml
# ... packages.yml, sysctl.yml, daemons.yml, storage.yml, networks.yml, users.yml
- name: Configure kernel sysctl parameters for KVM
  ansible.builtin.include_tasks: sysctl.yml
  when: rhel_kvm_manage_sysctl | bool
```
- **What:** each step is a separate file pulled in with `include_tasks`. The sysctl step can be switched off with `rhel_kvm_manage_sysctl: false`.
- **Why:** small files are easier to maintain and explain; the order matters (packages before daemons, daemons before storage/networks, because pools and networks need a running libvirt).
- **Error seen:** none.

---

## 2. `vars/`: the OS decision and protected constants

Role `vars/` beats `group_vars`, so inventory variables cannot replace these by accident.

### `vars/main.yml`
```yaml
rhel_kvm_packages: [qemu-kvm, libvirt, libvirt-client, virt-install, python3-libvirt]
```
- **What:** the mandatory package list.
- **Why it is in `vars/`, not `defaults/`:** if it were a default and someone set `rhel_kvm_packages: [guestfs-tools]` in their inventory, Ansible would replace the whole list and `qemu-kvm`/`libvirt` would never be installed. Extra packages go in `rhel_kvm_extra_packages` (a default) instead.
- **Error seen:** none.

### `vars/RedHat-9.yml` (monolithic `libvirtd`, required)
| Variable | Value | Why |
| :--- | :--- | :--- |
| `rhel_kvm_update_redhat_release` | `false` | RHEL 9 packages only carry the classic signature, no key update needed |
| `rhel_kvm_libvirt_uri` | `qemu+unix:///system?socket=/var/run/libvirt/libvirt-sock` | every libvirt task and `virsh` connects straight to `libvirtd`'s own socket, so nothing depends on how the client chooses between `libvirtd` and the modular daemons |
| `rhel_kvm_sockets` | `libvirtd.socket`, `-ro`, `-admin` | the sockets to enable/start |
| `rhel_kvm_services` | `libvirtd.service` | the service to enable/start |
| `rhel_kvm_disabled_services` | all `virtqemud/virtnetworkd/virtstoraged/virtnodedevd/virtsecretd/virtnwfilterd` services and sockets | RHEL 9.8 ships both daemon families and its systemd presets start the modular ones on their own; they are stopped and masked so they can never run next to `libvirtd` |

- **Error seen [#6]:** `libvirtd` was `inactive (dead)` while `virtqemud`, `virtnetworkd`, `virtstoraged` were `active (running)`.
- **Error seen [#7]:** `Failed to connect socket to '/var/run/libvirt/virtqemud-sock': Connection refused` (fixed by the explicit URI).

### `vars/RedHat-10.yml` (modular)
| Variable | Value | Why |
| :--- | :--- | :--- |
| `rhel_kvm_update_redhat_release` | `true` | RHEL 10.1+ packages are also signed with a post-quantum key that an older 10.1 image lacks; see `packages.yml` |
| `rhel_kvm_libvirt_uri` | `qemu:///system` | `libvirtd` does not exist on RHEL 10, so the plain URI is unambiguous |
| `rhel_kvm_sockets` | 6 drivers x (`socket`, `-ro`, `-admin`) | modular sockets for qemu, network, storage, nodedev, secret, nwfilter |
| `rhel_kvm_services` | `virtqemud`, `virtnetworkd`, `virtstoraged` | started explicitly so networks, pools and VMs set to autostart really come up at boot |
| `rhel_kvm_disabled_services` | the four `libvirtd` units | stopped and masked so the legacy daemon can never conflict |

- **Error seen [#5]:** `Failed to validate GPG signature for libvirt-daemon-log-11.10.0-12.4.el10_2.x86_64: Public key for ...rpm is not installed`.

---

## 3. `tasks/preflight.yml`

```yaml
- name: Validate target operating system family
  ansible.builtin.assert:
    that:
      - ansible_facts['os_family'] == 'RedHat'
      - ansible_facts['distribution_major_version'] in ['9', '10']
    fail_msg: ...
```
- **What:** stops the run with a clear message unless the host is RedHat-family 9 or 10.
- **Why:** package names, daemon layout and paths differ elsewhere; failing early is better than half-configuring a wrong OS. It also guarantees a matching `vars/RedHat-N.yml` exists.
- **Error seen:** none.

```yaml
- name: Check CPU hardware virtualization extensions support
  ansible.builtin.shell:
    cmd: "grep -E -c '(vmx|svm)' /proc/cpuinfo"
  register: rhel_kvm_cpu_virt_check
  changed_when: false
  failed_when: false
```
- **What:** counts the CPU lines that list `vmx` (Intel) or `svm` (AMD). The result goes into `rhel_kvm_cpu_virt_check`.
- **`changed_when: false`:** it only reads; it must not report a change.
- **`failed_when: false`:** `grep -c` exits with code 1 when the count is 0. Without this, a CPU with no virtualization flags would abort the run instead of producing the warning below.
- **Why:** KVM needs hardware virtualization. The role checks, but does not block, because a VM host may have nested virtualization off and still be usable (slowly) for testing.
- **Error seen:** none.

```yaml
- name: Warn if CPU hardware virtualization is missing
  ansible.builtin.debug:
    msg: "WARNING: Hardware virtualization (VT-x/AMD-V) is not detected ..."
  when: rhel_kvm_cpu_virt_check.stdout | int == 0
```
- **What:** prints the warning only when the count is 0.
- **Why:** makes a slow or failing VM later easy to explain.

```yaml
- name: Load required virtualization kernel modules
  community.general.modprobe:
    name: "{{ item }}"
    state: present
  loop: [kvm, vhost_net, tun]
```
- **What:** loads three kernel modules into the running kernel now.
  - `kvm`: the KVM core, i.e. hardware-accelerated virtualization.
  - `vhost_net`: moves VM network processing into the kernel, so guest networking is faster.
  - `tun`: virtual network devices (TAP) that VM network cards attach to.
- **Why:** libvirt/QEMU need them before the first VM starts; loading them here means the role does not depend on something else loading them lazily.
- **Error seen:** none.

```yaml
- name: Ensure virtualization modules load on boot
  ansible.builtin.copy:
    dest: /etc/modules-load.d/kvm.conf
    content: |
      kvm
      vhost_net
      tun
    owner: root
    group: root
    mode: "0644"
```
- **What:** writes the same three names into `/etc/modules-load.d/kvm.conf`, which `systemd-modules-load` reads at every boot.
- **Why:** `modprobe` only lasts until reboot. `copy` only reports a change if the content differs, so re-runs stay at `changed=0`.
- **Error seen:** none.

---

## 4. `tasks/packages.yml`

```yaml
- name: Update redhat-release so the current Red Hat signing keys are on disk
  ansible.builtin.dnf:
    name: redhat-release
    state: latest
  when: rhel_kvm_update_redhat_release | bool
```
- **What:** upgrades the small `redhat-release` package, only where the OS vars file says so (RHEL 10).
- **Why:** on an older RHEL 10.1 image the package install failed signature validation (packages carry a second, post-quantum signature whose key was missing). Importing the key with Ansible's `rpm_key` does not work (Red Hat known issue RHEL-126844), and importing the file with `rpmkeys` changed nothing; updating `redhat-release` was the fix that worked on a fresh rollback. **Why it works is not fully explained**; it is confirmed by testing.
- **What it does not do:** it does not update the OS. `redhat-release` ships identity files and key files; the reported version changes (10.1 to 10.2), the kernel and other packages do not.
- **`when`:** RHEL 9 skips it (`false` in `RedHat-9.yml`).
- **Error seen [#5]:** `Failed to validate GPG signature for libvirt-daemon-log-11.10.0-12.4.el10_2.x86_64: Public key for ...rpm is not installed`.

```yaml
- name: Install core KVM hypervisor packages
  ansible.builtin.dnf:
    name: "{{ rhel_kvm_packages }}"
    state: present
    update_cache: true
```
- **What:** installs the five packages from `vars/main.yml`:
  - `qemu-kvm`: the emulator/hypervisor process that runs each VM.
  - `libvirt`: the management daemon(s), plus default networks and storage drivers.
  - `libvirt-client`: `virsh`.
  - `virt-install`: command-line VM creation.
  - `python3-libvirt`: the Python bindings **on the target**, required by the `community.libvirt` modules used in `storage.yml` and `networks.yml`.
- **`state: present`:** install if missing, never upgrade what is already there (keeps runs repeatable).
- **`update_cache: true`:** refresh repo metadata first so the newest builds are found.
- **Error seen:** `Cannot find a valid baseurl for repo: epel` (a broken `epel.repo` stub left by the Zabbix role; dnf refreshes every enabled repo, not only the ones it needs) [#10].

```yaml
- name: Install optional user-defined KVM packages
  ansible.builtin.dnf:
    name: "{{ rhel_kvm_extra_packages }}"
    state: present
  when:
    - rhel_kvm_extra_packages is defined
    - rhel_kvm_extra_packages | length > 0
```
- **What:** installs anything the user lists in `rhel_kvm_extra_packages` (e.g. `guestfs-tools`); skipped when the list is empty.
- **Why:** a separate list so users can add tools without touching the protected core list.

---

## 5. `tasks/sysctl.yml`

```yaml
- name: Enable IPv4 packet forwarding for virtual networking
  ansible.posix.sysctl:
    name: net.ipv4.ip_forward
    value: "1"
    sysctl_set: true
    state: present
    reload: true
    sysctl_file: /etc/sysctl.d/99-kvm.conf
  notify: Reload sysctl
```
- **What:** sets `net.ipv4.ip_forward = 1`.
  - `sysctl_file: /etc/sysctl.d/99-kvm.conf`: written to a file of its own, so it persists across reboots.
  - `sysctl_set: true`: also applied to the running kernel now.
  - `reload: true`: reloads sysctl settings after the change.
  - `notify: Reload sysctl`: runs `sysctl --system` (handler) only when something changed.
- **Why:** the `kvm_br0` network is NAT (`forward mode='nat'`); NAT only works if the host forwards packets between the VM bridge and the real network.
- **Why file `99-`:** files in `/etc/sysctl.d/` are read in name order and the last value wins. The CIS roles write `ip_forward = 0` into `60-netipv4_sysctl.conf`; `99-kvm.conf` loads after it and overrides it, so hardening does not break VM networking. Checked on both hardened hosts: `sysctl net.ipv4.ip_forward` = `1`.
- **Error seen:** none. It is a conflict avoided by design [#2].

```yaml
- name: Enable IPv6 packet forwarding (if IPv6 enabled)
  ansible.posix.sysctl:
    name: net.ipv6.conf.all.forwarding
    value: "1"
    ...
  when: ansible_facts['all_ipv6_addresses'] is defined and ansible_facts['all_ipv6_addresses'] | length > 0
```
- **What:** the same for IPv6, only if the host has an IPv6 address.
- **Why:** forwarding for guests that use IPv6. See "Review notes": the NAT network in this role is IPv4 only.
- **Error seen:** none.

---

## 6. `tasks/daemons.yml`

### 6.1 Stop and mask the daemons this OS must not run
```yaml
- name: Disable conflicting services if applicable
  ansible.builtin.systemd_service:
    name: "{{ item }}"
    enabled: false
    state: stopped
    masked: true
  loop: "{{ rhel_kvm_disabled_services | default([]) }}"
  failed_when: false
```
- **What:** for each unit in `rhel_kvm_disabled_services`: disable it, stop it, **mask** it (a masked unit cannot be started by anything, not even another unit or a preset).
- **Why:** RHEL 9: the modular daemons; RHEL 10: the legacy `libvirtd`. This is what enforces the monolithic requirement on RHEL 9.
- **`failed_when: false`:** the list is written once for all hosts; a unit that is not installed on a given host (or is already stopped) must not abort the run.
- **Error seen [#6]:** `libvirtd` inactive while `virtqemud`, `virtnetworkd`, `virtstoraged` were running.

### 6.2 Make sure the socket comes before the service
```yaml
- name: Check whether the primary libvirt socket is active
  ansible.builtin.command:
    argv: [systemctl, is-active, "{{ rhel_kvm_sockets | first }}"]
  register: rhel_kvm_primary_socket
  changed_when: false
  failed_when: false

- name: Stop libvirt services that are running without their socket
  ansible.builtin.systemd_service:
    name: "{{ item }}"
    state: stopped
  loop: "{{ rhel_kvm_services }}"
  when: rhel_kvm_primary_socket.stdout != 'active'
```
- **What:** asks systemd whether the first socket is active (`is-active` exits non-zero when it is not, hence `failed_when: false`; `changed_when: false` because it only reads). If it is not active, the libvirt service is stopped first.
- **Why:** on RHEL 9, after a reboot `libvirtd.service` started without `libvirtd.socket`. When the playbook then tried to start the socket, systemd refused ("already active"), and because the socket unit has `RemoveOnStop=yes`, the socket files in `/run/libvirt/` were deleted while the daemon kept running: every client got "No such file". Stopping the service first lets the socket bind cleanly; the next tasks start socket, then service.
- **When it is skipped:** if the socket is already active (normal case, and RHEL 10), so re-runs stay `changed=0`. Stopping `libvirtd` does not stop running VMs.
- **Error seen [#8]:** `Unable to start service libvirtd.socket: Job failed` (journal: `Socket service libvirtd.service already active, refusing`), then `Failed to connect socket to '/var/run/libvirt/libvirt-sock': No such file or directory`.
- **Tested:** rhel9 hardened, rebooted into the broken state, re-run `failed=0`.

### 6.3 Enable and start the sockets
```yaml
- name: Enable and start libvirt systemd sockets
  ansible.builtin.systemd_service:
    name: "{{ item }}"
    enabled: true
    state: started
  loop: "{{ rhel_kvm_sockets }}"
```
- **What:** enable (start at boot) and start every socket in the OS list.
- **Why:** socket activation: systemd listens on the socket and starts the daemon on the first connection. On RHEL 10 all 18 driver sockets are used.
- **Note:** no `failed_when: false` here on purpose, so a real failure shows up.

### 6.4 Enable and start the services
```yaml
- name: Enable and start libvirt systemd services
  ansible.builtin.systemd_service:
    name: "{{ item }}"
    enabled: true
    state: started
  loop: "{{ rhel_kvm_services }}"
  when: rhel_kvm_services | length > 0
```
- **What:** enable and start `libvirtd` (RHEL 9) or the three modular daemons (RHEL 10).
- **Why:** sockets alone would only start a daemon on the first connection. Networks, pools and VMs marked *autostart* need the daemon running at boot, so the service itself is enabled.
- **Error seen:** none by itself; see 6.2 for the ordering problem.

---

## 7. `tasks/storage.yml`: the VM disk pool

```yaml
- name: Ensure storage pool directories exist with correct permissions
  ansible.builtin.file:
    path: "{{ item.path }}"
    state: directory
    owner: root
    group: root
    mode: "0711"
  loop: "{{ rhel_kvm_storage_pools }}"
  when: item.type == 'dir'
```
- **What:** creates the directory (default `/var/lib/libvirt/images`), owner `root:root`, mode `0711`, for every pool of type `dir`.
- **Why `0711`:** other users can pass through the directory (so QEMU can reach files inside) but cannot list or read it, so VM disk images are not readable by ordinary users.

```yaml
- name: Set persistent SELinux context on storage directories
  community.general.sefcontext:
    target: "{{ item.path }}(/.*)?"
    setype: virt_image_t
    state: present
  loop: "{{ rhel_kvm_storage_pools }}"
  when:
    - ansible_facts['selinux'] is defined
    - ansible_facts['selinux']['status'] == 'enabled'
    - item.type == 'dir'
```
- **What:** records in the SELinux policy that this path and everything under it are `virt_image_t`. `(/.*)?` is the regex "the directory and anything below it". Skipped if SELinux is off.
- **Why:** with SELinux enforcing, QEMU is only allowed to use files labelled `virt_image_t`. Recording it in the policy keeps the label after a relabel of the filesystem.

```yaml
- name: Apply SELinux context to storage pool directory contents
  ansible.builtin.file:
    path: "{{ item.path }}"
    setype: virt_image_t
    recurse: false
    state: directory
  loop: ...
```
- **What:** applies the label to the directory right now.
- **Why:** `sefcontext` only writes the rule; this makes it effective immediately without running `restorecon`.

```yaml
- name: Ensure libvirt storage pools are defined
  community.libvirt.virt_pool:
    name: "{{ item.name }}"
    state: present
    xml: "{{ lookup('ansible.builtin.template', 'storage_pool.xml.j2') }}"
    uri: "{{ rhel_kvm_libvirt_uri }}"
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** defines (registers) the pool in libvirt from the XML template.
- **`uri:`** the per-OS connection string (section 2). This is what keeps RHEL 9 on `libvirtd`.
- **Why separate from "active":** a pool must exist before it can be started; the module cannot start something that is not defined.
- **Error seen [#7]:** `Failed to connect socket to '/var/run/libvirt/virtqemud-sock': Connection refused` (the task's default URI reached the wrong daemon socket).
- **Error seen [#8]:** `Failed to connect socket to '/var/run/libvirt/libvirt-sock': No such file or directory` (socket file deleted, see 6.2).

```yaml
- name: Activate libvirt storage pools
  community.libvirt.virt_pool:
    name: "{{ item.name }}"
    state: active
    uri: "{{ rhel_kvm_libvirt_uri }}"
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** starts the pool. **Why:** libvirt tools such as `virt-install` fail with "Storage pool not found/not active" otherwise.

```yaml
- name: Autostart libvirt storage pools
  community.libvirt.virt_pool:
    name: "{{ item.name }}"
    autostart: "{{ item.autostart | default(true) }}"
    uri: "{{ rhel_kvm_libvirt_uri }}"
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** sets the pool to start automatically at boot.
- **Why a separate task:** `community.libvirt.virt_pool` stops at `state` and silently ignores `autostart` when both are in one task, so the pool did not come back after a reboot.
- **Error seen [#4]:** no Ansible error; the play passed, but after reboot the pool/network showed `inactive` and `Autostart: no`.

---

## 8. `tasks/networks.yml`: the NAT network `kvm_br0`

All three tasks run only when `rhel_kvm_manage_bridge_network` is true.

```yaml
- name: Ensure dedicated bridge network is defined
  community.libvirt.virt_net:
    name: "{{ rhel_kvm_bridge_network_name }}"
    state: present
    xml: "{{ lookup('ansible.builtin.template', 'bridge_network.xml.j2') }}"
    uri: "{{ rhel_kvm_libvirt_uri }}"
```
- **What:** defines a virtual network named `kvm_br0` (bridge `virbr1`, `192.168.100.0/24`) from the template.
- **Why:** a second, dedicated network for VMs, in addition to libvirt's built-in `default` network (`virbr0`, `192.168.122.0/24`), which the role leaves as shipped. The subnet does not overlap the default one.

```yaml
- name: Activate dedicated bridge network
  community.libvirt.virt_net:
    name: "{{ rhel_kvm_bridge_network_name }}"
    state: active
    uri: "{{ rhel_kvm_libvirt_uri }}"

- name: Autostart dedicated bridge network
  community.libvirt.virt_net:
    name: "{{ rhel_kvm_bridge_network_name }}"
    autostart: "{{ rhel_kvm_bridge_autostart | default(true) }}"
    uri: "{{ rhel_kvm_libvirt_uri }}"
```
- **What:** start the network, then set autostart, as two tasks. **Why:** same reason as storage: `autostart` is ignored when combined with `state`.
- **Error seen [#4]:** after a reboot `kvm_br0` was `inactive` with `Autostart: no` (`virsh net-list --all`), although the play had succeeded.
- **Fixed and verified:** both hosts show `kvm_br0 active, Autostart yes` after a reboot with nothing started by hand.
- **Depends on:** IP forwarding (section 5) and `dnsmasq` being installed; libvirt starts its own `dnsmasq` for the DHCP range. The CIS group_vars keep `dnsmasq` installed for that reason.

---

## 9. `tasks/users.yml`

```yaml
- name: Add administrative users to libvirt group
  ansible.builtin.user:
    name: "{{ item }}"
    groups: libvirt
    append: true
  loop: "{{ rhel_kvm_admin_users }}"
  when:
    - rhel_kvm_admin_users is defined
    - rhel_kvm_admin_users | length > 0
```
- **What:** adds each listed user to the `libvirt` group. `append: true` adds to the group without removing the user from their other groups (without it, `groups:` would replace the whole list).
- **Why:** libvirt ships a polkit rule that lets members of the `libvirt` group manage the system connection without a password, so `virsh`/Cockpit work without `sudo`. (The socket itself is world-connectable, mode `0666`; polkit is what authorizes the actions.)
- **Where the user comes from:** the orchestrator sets `rhel_kvm_admin_users: ["{{ ansible_user }}"]` (`frqadmin`).

```yaml
- name: Configure system-wide default libvirt URI
  ansible.builtin.copy:
    dest: /etc/profile.d/libvirt.sh
    content: |
      export LIBVIRT_DEFAULT_URI="{{ rhel_kvm_libvirt_uri }}"
    owner: root
    group: root
    mode: "0644"
```
- **What:** writes a login-shell script that exports `LIBVIRT_DEFAULT_URI` with the per-OS URI.
- **Why:** an ordinary user's `virsh` otherwise connects to `qemu:///session` (the user's own empty instance), not the system hypervisor. Using the same variable as the tasks keeps `virsh` and Ansible on the same daemon.
- **Limits:** only login shells read `/etc/profile.d`; `sudo virsh` does not inherit it.
- **Error seen:** without the system URI, `virsh` as a normal user shows an empty list ([#9]).

---

## 10. Supporting files

### `handlers/main.yml`
```yaml
- name: Reload sysctl
  ansible.builtin.command:
    cmd: sysctl --system
  changed_when: false
```
- **What:** re-reads every file in `/etc/sysctl.d/` in name order. Runs only when `sysctl.yml` reports a change.
- **Why:** applies the settings in the same order the system does at boot, so `99-kvm.conf` is applied last. `changed_when: false` because it is a refresh, not a state change.

### `defaults/main.yml` (what users may override)
| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `rhel_kvm_manage_sysctl` | `true` | run `sysctl.yml` |
| `rhel_kvm_admin_users` | `[]` | users added to the `libvirt` group |
| `rhel_kvm_extra_packages` | `[]` | extra packages |
| `rhel_kvm_storage_pools` | one `dir` pool `default` at `/var/lib/libvirt/images`, autostart | pool list |
| `rhel_kvm_manage_bridge_network` | `true` | create `kvm_br0` |
| `rhel_kvm_bridge_network_name` / `_device` | `kvm_br0` / `virbr1` | libvirt network name / Linux bridge name |
| `rhel_kvm_bridge_ip` / `_netmask` | `192.168.100.1` / `255.255.255.0` | host address on the bridge |
| `rhel_kvm_bridge_dhcp_start` / `_end` | `.10` / `.254` | DHCP range for guests |
| `rhel_kvm_bridge_autostart` | `true` | start at boot |

### `templates/storage_pool.xml.j2`
| Line | Meaning |
| :--- | :--- |
| `<pool type='dir'>` | a directory-backed pool (type comes from the pool entry) |
| `<name>` / `<path>` | pool name and directory |
| `<mode>0711</mode>`, `<owner>0</owner>`, `<group>0</group>` | same permissions as the directory task, root-owned |
| `<label>system_u:object_r:virt_image_t:s0</label>` | SELinux label libvirt applies to the pool directory |

### `templates/bridge_network.xml.j2`
| Line | Meaning |
| :--- | :--- |
| `<forward mode='nat'/>` | guests reach outside networks through the host (needs IP forwarding) |
| `<bridge name='virbr1' stp='on' delay='0'/>` | Linux bridge for the network; spanning tree on, no forward delay |
| `<ip address=... netmask=...>` | the host's address on the bridge, and the guests' gateway |
| `<dhcp><range start end/>` | libvirt's `dnsmasq` hands out addresses in this range |

### `meta/main.yml`
Galaxy metadata (author, platforms EL 9/10, `min_ansible_version` 2.15) and the collections the role needs: `ansible.posix`, `community.general`, `community.libvirt`.

---

## Review notes: points a reviewer may question

These are honest weak spots, not errors:
1. **IPv6 forwarding task:** the `kvm_br0` network is IPv4-only, so this task has no consumer in the role today.
2. **`Reload sysctl` handler:** the sysctl module already applies and reloads (`sysctl_set`, `reload`). The handler adds the ordered `sysctl --system` pass; it is belt-and-braces rather than strictly required.
3. **Redundant guards:** `is defined` checks in `packages.yml` and `users.yml`, and `| default(true)` on the autostart values, are unnecessary because the defaults always exist.
4. **Client URI behaviour:** it was seen once that the libvirt client reached `virtqemud-sock` on RHEL 9 while `libvirtd` was the enabled daemon. Later, on a rebooted host with the modular units masked, a plain `virsh` did connect. The exact rule the client uses to choose is not fully pinned down. The explicit URI removes the guesswork either way.
5. **`libvirtd.socket` not started at boot on RHEL 9:** seen on two boots and not explained. `daemons.yml` converges it on the next run, and the service works in the meantime.
6. **RHEL 10 `redhat-release` update:** confirmed by testing, not fully explained.
