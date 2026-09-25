# rhel_kvm: Task Walkthrough

Every task, in the order it runs: **what it does**, **why it is there**, and an **error seen** where a real error led to it. "None seen" means the task was never the cause of a failure.
Errors quoted here were hit on the test hosts; the full stories are in the orchestrator's `docs/LESSONS_LEARNED_AND_FIXES.md` (item numbers in brackets).

## Execution order

| # | File | Purpose |
| :- | :--- | :--- |
| 1 | `tasks/main.yml` | Loads the OS-specific vars, then runs the steps below in order |
| 2 | `tasks/preflight.yml` | Checks the OS, loads and persists kernel modules |
| 3 | `tasks/packages.yml` | Installs the KVM/libvirt packages |
| 4 | `tasks/sysctl.yml` | Turns on IP forwarding |
| 5 | `tasks/daemons.yml` | Starts the right libvirt daemons for the OS |
| 6 | `tasks/storage.yml` | Creates the VM disk directory, SELinux label, storage pool |
| 7 | `tasks/networks.yml` | Creates the NAT network `kvm_br0` |

Big idea: **one role, two libvirt designs.** RHEL 9 must run the single `libvirtd` daemon (a fixed requirement); RHEL 10 only has separate `virtqemud` / `virtnetworkd` / `virtstoraged` daemons. The role does not branch with `if`. It loads a vars file per OS, and every task loops over whatever that file defines.

---

## Where each variable is used

Defaults are in `defaults/main.yml`; protected constants are in `vars/`. This is every place a variable is read, so nothing is hidden in `main.yml` or a template.

| Variable | Read in | Effect |
| :--- | :--- | :--- |
| `rhel_kvm_manage_sysctl` | **`tasks/main.yml`** (gate on the `sysctl.yml` include) | `true`: IP forwarding step runs; `false`: skipped |
| `rhel_kvm_packages` | `tasks/packages.yml` | mandatory packages |
| `rhel_kvm_extra_packages` | `tasks/packages.yml` | optional extra packages (skipped when empty) |
| `rhel_kvm_update_redhat_release` | `tasks/packages.yml` | `true` on RHEL 10: update `redhat-release` first |
| `rhel_kvm_disabled_services` | `tasks/daemons.yml` | units stopped and masked (RHEL 10: the legacy `libvirtd` units; RHEL 9: none) |
| `rhel_kvm_sockets` | `tasks/daemons.yml` | sockets enabled and started (RHEL 10: the modular sockets; RHEL 9: none) |
| `rhel_kvm_services` | `tasks/daemons.yml` | services enabled and started |
| `rhel_kvm_storage_pools` | `tasks/storage.yml` (all 5 tasks) | the pools to create: name and path |
| `rhel_kvm_manage_bridge_network` | `tasks/networks.yml` (`when` on all 3 tasks) | `false`: no `kvm_br0` |
| `rhel_kvm_bridge_network_name` | `tasks/networks.yml`, `templates/bridge_network.xml.j2` | network name |
| `rhel_kvm_bridge_autostart` | `tasks/networks.yml` (autostart task) | start at boot |
| `rhel_kvm_bridge_device`, `_ip`, `_netmask`, `_dhcp_start`, `_dhcp_end` | `templates/bridge_network.xml.j2` only | bridge name, address, DHCP range |

Only `rhel_kvm_manage_sysctl` lives in `main.yml`. The OS facts (`os_family`, `distribution_major_version`) are also read there, to pick the vars file.

---

## 1. `tasks/main.yml`

```yaml
- name: Include OS-specific variables
  ansible.builtin.include_vars: "{{ ansible_facts['os_family'] }}-{{ ansible_facts['distribution_major_version'] }}.yml"
```
- **What:** builds the file name from facts, e.g. `RedHat-9.yml` or `RedHat-10.yml`, and loads it.
- **Why:** this is the only place the OS decision is made. The vars file decides which sockets/services to start, which to mask and whether to update `redhat-release`. Adding an OS later means adding a file, not editing tasks.
- **Error seen:** none.

```yaml
- name: Run pre-flight hardware and OS validation
  ansible.builtin.include_tasks: preflight.yml
# ... packages.yml, sysctl.yml, daemons.yml, storage.yml, networks.yml
- name: Configure kernel sysctl parameters for KVM
  ansible.builtin.include_tasks: sysctl.yml
  when: rhel_kvm_manage_sysctl | bool
```
- **What:** each step is a separate file pulled in with `include_tasks`. `main.yml` contains **one** variable, `rhel_kvm_manage_sysctl`, which decides whether `sysctl.yml` runs. It also reads two facts (`os_family`, `distribution_major_version`) to pick the vars file.
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
| `rhel_kvm_sockets` | `[]` | no sockets are managed on RHEL 9; enabling `libvirtd.service` is enough |
| `rhel_kvm_services` | `libvirtd.service` | the service to enable/start |
| `rhel_kvm_disabled_services` | `[]` | nothing is disabled or masked; the other libvirt daemons stay as the OS ships them |

- **Error seen:** none.

### `vars/RedHat-10.yml` (modular)
| Variable | Value | Why |
| :--- | :--- | :--- |
| `rhel_kvm_update_redhat_release` | `true` | RHEL 10.1+ packages are also signed with a post-quantum key that an older 10.1 image lacks; see `packages.yml` |
| `rhel_kvm_sockets` | 6 drivers x (`socket`, `-ro`, `-admin`) | sockets for qemu, network, storage, nodedev, secret and nwfilter. The OS does not enable them on a fresh host, and each daemon starts on demand |
| `rhel_kvm_services` | `virtqemud.service` | runs the VMs |
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
- **Error seen:** `Cannot find a valid baseurl for repo: epel` (a broken `epel.repo` stub left by the Zabbix role; dnf refreshes every enabled repo, not only the ones it needs) [#8].

```yaml
- name: Install optional user-defined KVM packages
  ansible.builtin.dnf:
    name: "{{ rhel_kvm_extra_packages }}"
    state: present
  when:
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

RHEL 9 keeps this simple: only `libvirtd.service` is enabled and started. No sockets are managed and nothing is masked. RHEL 10 uses the modular sockets and services and masks the legacy `libvirtd` units.

### 6.1 Disable the daemons this OS does not use
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
- **Why:** on RHEL 10 the legacy `libvirtd` units must never run; RHEL 10 has no `libvirtd`, so this is a safeguard. On RHEL 9 the list is empty and the task does nothing.
- **`failed_when: false`:** the list is written once per OS; a unit that is not installed on a given host (or is already stopped) must not abort the run.
- **Error seen:** none.

### 6.2 Enable and start the sockets
```yaml
- name: Enable and start libvirt systemd sockets
  ansible.builtin.systemd_service:
    name: "{{ item }}"
    enabled: true
    state: started
  loop: "{{ rhel_kvm_sockets }}"
```
- **What:** enable (start at boot) and start every socket in the OS list.
- **Why:** socket activation: systemd listens on the socket and starts the daemon on the first connection. On RHEL 10 these are the 18 driver sockets. On RHEL 9 the list is empty, so this task does nothing.

### 6.3 Enable and start the services
```yaml
- name: Enable and start libvirt systemd services
  ansible.builtin.systemd_service:
    name: "{{ item }}"
    enabled: true
    state: started
  loop: "{{ rhel_kvm_services }}"
  when: rhel_kvm_services | length > 0
```
- **What:** enable and start `libvirtd` (RHEL 9) or `virtqemud` (RHEL 10).
- **On RHEL 9 the socket list is empty** because `libvirtd.service` brings its own sockets: the unit file says `Also=` (enabling the service enables them) and `Wants=` (starting the service starts them). On RHEL 10 the sockets of all drivers are listed, because the OS does not enable them.
- **Why:** sockets alone would only start a daemon on the first connection. VMs marked *autostart* need `virtqemud` running at boot, so the service itself is enabled.
- **Error seen:** none.

---

## 7. `tasks/storage.yml`: the VM disk pool

A pool is a directory where libvirt stores VM disks. It has a `name` and a `path`. Every task below loops over `rhel_kvm_storage_pools`.

```yaml
- name: Ensure storage pool directories exist with the VM image label
  ansible.builtin.file:
    path: "{{ item.path }}"
    state: directory
    owner: root
    group: root
    mode: "0711"
    setype: virt_image_t
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** creates the directory (default `/var/lib/libvirt/images`), owner `root:root`, mode `0711`, and gives it the SELinux label `virt_image_t` right away. The label is ignored when SELinux is disabled.
- **Why `0711`:** other users can pass through the directory (so QEMU can reach files inside) but cannot list or read it, so VM disk images are not readable by ordinary users.
- **Why `virt_image_t`:** with SELinux enforcing, QEMU is only allowed to use files labelled `virt_image_t`.

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
```
- **What:** records in the SELinux policy that this path and everything under it are `virt_image_t`. `(/.*)?` is the regex "the directory and anything below it". Skipped if SELinux is off.
- **Why:** the label set by the first task is lost if the filesystem is relabelled. Recording it in the policy keeps it.

```yaml
- name: Ensure libvirt storage pools are defined
  community.libvirt.virt_pool:
    name: "{{ item.name }}"
    state: present
    xml: "<pool type='dir'><name>{{ item.name }}</name><target><path>{{ item.path }}</path></target></pool>"
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** defines (registers) the pool in libvirt. The XML is one line: a directory pool with a name and a path. The mode and label are not repeated in it, because the first task already sets them on the directory.
- **Why separate from "active":** a pool must exist before it can be started; the module cannot start something that is not defined.

```yaml
- name: Activate libvirt storage pools
  community.libvirt.virt_pool:
    name: "{{ item.name }}"
    state: active
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** starts the pool. **Why:** libvirt tools such as `virt-install` fail with "Storage pool not found/not active" otherwise.

```yaml
- name: Autostart libvirt storage pools
  community.libvirt.virt_pool:
    name: "{{ item.name }}"
    autostart: true
  loop: "{{ rhel_kvm_storage_pools }}"
```
- **What:** sets the pool to start automatically at boot. Every pool autostarts.
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
```
- **What:** defines a virtual network named `kvm_br0` (bridge `virbr1`, `192.168.100.0/24`) from the template.
- **Why:** a second, dedicated network for VMs, in addition to libvirt's built-in `default` network (`virbr0`, `192.168.122.0/24`), which the role leaves as shipped. The subnet does not overlap the default one.

```yaml
- name: Activate dedicated bridge network
  community.libvirt.virt_net:
    name: "{{ rhel_kvm_bridge_network_name }}"
    state: active

- name: Autostart dedicated bridge network
  community.libvirt.virt_net:
    name: "{{ rhel_kvm_bridge_network_name }}"
    autostart: "{{ rhel_kvm_bridge_autostart | default(true) }}"
```
- **What:** start the network, then set autostart, as two tasks. **Why:** same reason as storage: `autostart` is ignored when combined with `state`.
- **Error seen [#4]:** after a reboot `kvm_br0` was `inactive` with `Autostart: no` (`virsh net-list --all`), although the play had succeeded.
- **Fixed and verified:** both hosts show `kvm_br0 active, Autostart yes` after a reboot with nothing started by hand.
- **Depends on:** IP forwarding (section 5) and `dnsmasq` being installed; libvirt starts its own `dnsmasq` for the DHCP range. The CIS group_vars keep `dnsmasq` installed for that reason.

---

## 9. Supporting files

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
| `rhel_kvm_extra_packages` | `[]` | extra packages |
| `rhel_kvm_storage_pools` | one pool: `default` at `/var/lib/libvirt/images` | the pools to create |
| `rhel_kvm_manage_bridge_network` | `true` | create `kvm_br0` |
| `rhel_kvm_bridge_network_name` / `_device` | `kvm_br0` / `virbr1` | libvirt network name / Linux bridge name |
| `rhel_kvm_bridge_ip` / `_netmask` | `192.168.100.1` / `255.255.255.0` | host address on the bridge |
| `rhel_kvm_bridge_dhcp_start` / `_end` | `.10` / `.254` | DHCP range for guests |
| `rhel_kvm_bridge_autostart` | `true` | start at boot |

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
3. **Redundant defaults:** `| default(true)` on the autostart values and `| default([])` on the loop in `daemons.yml` are unnecessary, because those variables always exist in `defaults/` or `vars/`.
4. **RHEL 10 `redhat-release` update:** confirmed by testing, not fully explained.
