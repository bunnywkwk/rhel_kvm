# rhel_kvm - Role Architecture and Technical Justifications

## 1. Executive Summary

The `rhel_kvm` role transforms a clean installation of Enterprise Linux (RHEL 9, RHEL 10, AlmaLinux, or Rocky Linux) into a fully functional, headless KVM Hypervisor node.

The role solves four fundamental operational problems:

1. **OS-Level Architecture Divergence**: RHEL 9 is required to run the monolithic libvirt daemon (`libvirtd`), whereas RHEL 10 mandates modular driver daemons (`virtqemud`, `virtnetworkd`, `virtstoraged`, etc.). Holding that RHEL 9 requirement in the field on RHEL 9.8 took more than masking the modular daemons — see Step 4 below and `docs/LESSONS_LEARNED_AND_FIXES.md`.
2. **The Variable Precedence Trap**: Prevents user-defined packages in `group_vars` from overriding and omitting mandatory hypervisor binaries.
3. **SELinux and Security Confinement**: Enforces `virt_image_t` contexts and restrictive file permissions (`0711`) so virtual machines execute reliably under SELinux `Enforcing` mode without permission denials.
4. **Network and Hypervisor Verification**: Guarantees kernel IP forwarding for NAT routing.

---

## 2. Directory Structure and Architectural Justification

The role conforms strictly to Ansible Galaxy standards. Each directory serves a distinct architectural purpose:

```
rhel_kvm/
├── meta/
│   └── main.yml
├── defaults/
│   └── main.yml
├── vars/
│   ├── main.yml
│   ├── RedHat-9.yml
│   └── RedHat-10.yml
├── tasks/
│   ├── main.yml
│   ├── preflight.yml
│   ├── packages.yml
│   ├── sysctl.yml
│   ├── daemons.yml
│   ├── storage.yml
│   ├── networks.yml
│   └── users.yml
├── handlers/
│   └── main.yml
└── docs/
    ├── ARCHITECTURE_AND_JUSTIFICATIONS.md
    ├── KNOWLEDGE_BASE_QA.md
    └── ROLE_GOALS_AND_CHECKLIST.md
```

### Directory-by-Directory Rationale

| Directory   | Purpose                           | Technical Justification                                                                                                                                |
| :---------- | :-------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta/`     | Role metadata and dependencies    | Defines supported OS versions and collection requirements (`ansible.posix`, `community.general`, `community.libvirt`). Required for Galaxy compliance. |
| `defaults/` | Overridable user variables        | Lowest precedence. Contains settings intended for user customization (e.g. `rhel_kvm_extra_packages`, storage paths, admin user list).                      |
| `vars/`     | Protected architectural constants | High precedence. Stores OS-specific daemon mappings and mandatory core packages. Users cannot accidentally overwrite these from `group_vars`.          |
| `tasks/`    | Sequential task execution         | Broken into modular sub-task files rather than a monolithic script. Allows isolated maintenance and conditional execution.                             |
| `handlers/` | Event-driven notifications        | Flushes service restarts and sysctl reloads only when state has physically changed, preserving idempotency.                                            |
| `docs/`     | Technical documentation           | Isolates role documentation, design justifications, and Q&A logs within the role boundary.                                                             |

---

## 3. Package Architecture: Core vs. Extra Packages

### The Problem: The List Replacement Trap

In standard Ansible roles, placing the package list in `defaults/main.yml` exposes a critical flaw. If a user defines `rhel_kvm_packages: [guestfs-tools]` in their inventory `group_vars`, Ansible completely replaces the default list. Consequently, `qemu-kvm`, `libvirt`, and `virt-install` are omitted, breaking the hypervisor.

### The Solution: Two-Tier Package Strategy

1. **`vars/main.yml` (Mandatory Core Packages)**:
   Contains the minimum set required for the hypervisor engine:
   - `qemu-kvm`: Hardware emulator and hypervisor backend.
   - `libvirt`: Virtualization management daemon suite.
   - `libvirt-client`: CLI toolset (`virsh`).
   - `virt-install`: Provisioning tool for guest creation.
   - `python3-libvirt`: Python bindings required by Ansible `community.libvirt` modules.

2. **`defaults/main.yml` (Optional User Packages)**:
   Exposes `rhel_kvm_extra_packages: []`. Users can add any extra utility without risking the core platform.

[SCREENSHOT: Terminal output of dnf package installation task executing with zero package omissions]

---

## 4. Task-by-Task Implementation and Engineering Justification

### Step 1: `tasks/preflight.yml` (Hardware Validation & Kernel Stack)

- **What it does**:
  1. Inspects `/proc/cpuinfo` for Intel VT-x (`vmx`) or AMD-V (`svm`) CPU flags.
  2. Loads and persists kernel modules: `kvm`, `tun`, and `vhost_net`.
- **Justification**:
  - **Hardware Virtualization Flag Verification**: Critical in nested environments (e.g. Proxmox). Verifies that the underlying hypervisor successfully passed physical CPU virtualization flags (`host` CPU type) down to this VM. Without these flags, KVM hardware acceleration is unavailable.
  - **Kernel Network Acceleration**: `tun` creates virtual network TAP interfaces connecting VMs to bridges, while `vhost_net` moves virtio packet transfers into the Linux kernel for 5x–10x higher throughput.
  - **Elimination of Redundant Level 3 Nested Virtualization**: Configuring `options kvm_intel nested=1` inside this hypervisor was intentionally removed. Because guest VMs provisioned inside this hypervisor are workload servers (web/database/applications) and not hypervisors themselves, enabling nested virtualization passthrough at Level 2 is redundant and adds unneeded complexity.

[SCREENSHOT: virt-host-validate command output confirming CPU virtualization and hardware pass]

---

### Step 2: `tasks/packages.yml` (Package Installation)

- **What it does**:
  1. **RHEL 10 only** (`rhel_kvm_update_redhat_release` in `vars/RedHat-10.yml`): updates the `redhat-release` package so the current Red Hat signing keys are on disk.
  2. Installs core packages from `rhel_kvm_packages` and optional packages from `rhel_kvm_extra_packages`.
- **Justification**:
  - Separates mandatory binaries from user additions, ensuring fail-safe package deployment.
  - **Why the `redhat-release` update exists**: RHEL 10 packages carry a second, post-quantum signature (ML-DSA-87+Ed448, "release key 4", ID `05707a62`) that an older 10.1 image doesn't have the key for; without it `ansible.builtin.dnf` fails with `Public key for <pkg>.rpm is not installed`. Importing the image's own older key file added nothing, so `redhat-release` is updated instead. RHEL 9 validates only the classic RSA signature and is unaffected. See `docs/LESSONS_LEARNED_AND_FIXES.md` item #5.

---

### Step 3: `tasks/sysctl.yml` (Packet Forwarding)

- **What it does**: Configures `net.ipv4.ip_forward = 1` in `/etc/sysctl.d/99-kvm.conf` and reloads sysctl via handler.
- **Justification**: Guest VMs attached to the default NAT bridge (`virbr0`) cannot transmit packets beyond the local host without kernel-level IP forwarding enabled.

---

### Step 4: `tasks/daemons.yml` (Daemon Management per OS Version)

- **What it does**:
  - **RHEL 9**: Disables and masks the modular daemons (`virtqemud`, `virtnetworkd`, `virtstoraged`, `virtnodedevd`, `virtsecretd`, `virtnwfilterd` — services and sockets), then enables and starts `libvirtd.socket` and `libvirtd.service` (Monolithic model, required).
  - **RHEL 10**: Enables and starts modular sockets (`virtqemud.socket`, `virtnetworkd.socket`, `virtstoraged.socket`, etc.) and masks legacy `libvirtd.service`.
- **Justification**:
  - RHEL 10 completely removes `libvirtd`. Trying to start `libvirtd` on RHEL 10 results in fatal systemd unit-not-found errors.
  - RHEL 9.8 still ships the modular daemon binaries alongside `libvirtd`, and its own systemd presets can bring `virtqemud`/`virtnetworkd`/`virtstoraged` up on their own. Masking them is necessary but was found to be insufficient by itself: the libvirt **client** library (used by `community.libvirt.virt_pool`/`virt_net`, and by `virsh` itself) resolves a bare `qemu:///system` connection to `/var/run/libvirt/virtqemud-sock` whenever the modular packages are installed, regardless of systemd state, and never falls back to `libvirtd-sock` on its own — masking `virtqemud` without also fixing the URI broke Ansible's own provisioning tasks outright (`Connection refused`). See Steps 5 and 7 below and `docs/LESSONS_LEARNED_AND_FIXES.md` items #6 and #7 for the full incident and the `rhel_kvm_libvirt_uri` fix that keeps RHEL 9 monolithic as required.
  - **Sockets first, then services**: the role checks whether the primary socket (`rhel_kvm_sockets | first`) is active and, if it is not, stops the libvirt services before starting the sockets and then the services. On RHEL 9 the service can come up after a reboot without `libvirtd.socket` (it binds `/run/libvirt/libvirt-sock` itself). Starting the socket then is refused ("already active, refusing") and, because the socket unit has `RemoveOnStop=yes`, systemd deletes the socket files while `libvirtd` keeps running; every client then fails with `No such file or directory`. Seen on a hardened RHEL 9 host on the playbook re-run after a reboot; see `docs/LESSONS_LEARNED_AND_FIXES.md` item #8. When the socket is already active (normal case, and RHEL 10) the stop task is skipped, so re-runs stay at `changed=0`.
  - Dynamically loading variables from `vars/RedHat-{{ major_version }}.yml` ensures clean execution without inline conditionals on every task.

[SCREENSHOT: systemctl status output showing monolithic libvirtd active (and modular virtqemud/virtnetworkd/virtstoraged masked) on RHEL 9, vs modular virtqemud active (and libvirtd masked) on RHEL 10]

---

### Step 5: `tasks/storage.yml` (Storage Pools and SELinux Labeling)

- **What it does**:
  1. Creates `/var/lib/libvirt/images` with directory permissions `0711` and ownership `root:root`.
  2. Registers and applies SELinux context `virt_image_t` using `community.general.sefcontext`.
  3. Defines the storage pool with `community.libvirt.virt_pool` and `templates/storage_pool.xml.j2`, then activates it, then sets autostart, as three separate tasks (`virt_pool` ignores `autostart` when it is combined with `state`; see `docs/LESSONS_LEARNED_AND_FIXES.md` item #4). All connect via `uri: "{{ rhel_kvm_libvirt_uri }}"`.
- **Justification**:
  - Directory permission `0711` (`drwx--x--x`) prevents unprivileged users from reading VM image files while permitting hypervisor process traversal.
  - Under SELinux `Enforcing`, QEMU cannot read or write to directories labeled with generic `var_t` or `default_t` contexts. Setting `virt_image_t` avoids permission denials.
  - **Dynamic Storage Path Customization**: Because `/var/lib/libvirt/images` is on the root partition (`/`), administrators frequently redirect VM storage to dedicated RAID/NVMe arrays (e.g. `/data/vms`). Leaving `rhel_kvm_storage_pools` in `defaults/main.yml` ensures users can customize paths without touching code, while the role dynamically creates the target folder, registers the SELinux context, and configures Libvirt seamlessly.
  - Using `community.libvirt.virt_pool` replaces shell/command calls with native libvirt API bindings, ensuring strict idempotency (`changed=0` on repeated runs).
  - **Why `uri` is a variable, not a hardcoded `qemu:///system`**: on RHEL 9.8, the libvirt client resolves a bare `qemu:///system` to the modular `virtqemud-sock` whenever the modular packages are present, regardless of the required monolithic `libvirtd` being what's actually enabled. `rhel_kvm_libvirt_uri` on RHEL 9 is the explicit `qemu+unix:///system?socket=/var/run/libvirt/libvirt-sock`, removing the ambiguity outright. See `docs/LESSONS_LEARNED_AND_FIXES.md` item #7.

[SCREENSHOT: virsh pool-list --all command output showing default storage pool in Active state with Autostart enabled]

---

### Step 6: `tasks/networks.yml` (Dedicated Hypervisor Bridge Network)

- **What it does**:
  1. Leaves the built-in `default` network (`virbr0`) intact as provided natively by the libvirt package.
  2. Idempotently defines and starts a dedicated virtual bridge network (`kvm_br0` on device `virbr1`) using `templates/bridge_network.xml.j2`.
  3. Configures an isolated IP subnet (`192.168.100.0/24`) with gateway `192.168.100.1` and automated DHCP range (`192.168.100.10` - `192.168.100.254`).
  4. Enables automatic startup on boot (`autostart: true`) in its own task, because `virt_net` ignores `autostart` when it is combined with `state` (see `docs/LESSONS_LEARNED_AND_FIXES.md` item #4).
- **Justification**:
  - **Eliminating Redundancy**: The default network is already created by RPM installation. Removing redundant tasks that re-query and manipulate `default` keeps the role clean and unbloated.
  - **Inter-VM Layer 2 Switching**: Guest VMs plugged into `kvm_br0` communicate directly with one another at near-wire kernel speeds without traversing external routers.
  - **VM-to-Host Management Gateway**: The KVM host is assigned `192.168.100.1` on the bridge, establishing a permanent, dedicated management channel between the hypervisor and all provisioned VMs.
  - **Zero Remote Connection Risk**: Provisioning a dedicated virtual bridge device (`virbr1`) avoids altering the host's physical network adapter (`enp1s0`), preventing accidental SSH disconnects or network lockouts.

[SCREENSHOT: virsh net-list --all command output showing both default and kvm_br0 networks active and autostarted]

---

### Step 7: `tasks/users.yml` (User Access and Global System URI)

- **What it does**:
  1. Adds users defined in `rhel_kvm_admin_users` to the `libvirt` group with `append: true`.
  2. Deploys `/etc/profile.d/libvirt.sh` containing `export LIBVIRT_DEFAULT_URI="{{ rhel_kvm_libvirt_uri }}"`.
- **Justification**:
  - The libvirt socket (`/run/libvirt/libvirt-sock`) is world-connectable (`srw-rw-rw- root root`, `SocketMode=0666`); what authorizes actions is a polkit rule shipped with libvirt (`/usr/share/polkit-1/rules.d/50-libvirt.rules`) that allows members of the `libvirt` group to manage the system connection without a password. Membership in this group therefore grants VM management without root or `sudo`.
  - By default, unprivileged users querying `virsh` connect to `qemu:///session` (an empty user-space instance). Deploying `/etc/profile.d/libvirt.sh` guarantees that `virsh` automatically routes to the system hypervisor for all users in the `libvirt` group.
  - Templating this from `rhel_kvm_libvirt_uri` instead of hardcoding `qemu:///system` matters specifically on RHEL 9: an admin running plain `virsh list --all` after logging in would otherwise hit the same modular-socket-vs-monolithic ambiguity as the Ansible tasks in Step 5 (see `docs/LESSONS_LEARNED_AND_FIXES.md` item #7).

[SCREENSHOT: virsh list --all executed as non-root user displaying hypervisor VMs without sudo]

---

## 5. Verification Checklist

Execute the following verification steps on the target hypervisor host:

1. **Hardware Virtualization**:

   ```bash
   egrep -c '(vmx|svm)' /proc/cpuinfo
   # Expected: Integer >= 1
   ```

2. **Kernel Modules**:

   ```bash
   lsmod | grep -E 'kvm|vhost_net|tun'
   # Expected: kvm, kvm_intel/kvm_amd, vhost_net, tun listed
   ```

3. **Storage Pool Status**:

   ```bash
   virsh pool-info default
   # Expected: State: running, Autostart: yes
   ```

4. **Network Bridge Status**:

   ```bash
   virsh net-info default
   # Expected: Active: yes, Autostart: yes, Bridge: virbr0

   virsh net-info kvm_br0
   # Expected: Active: yes, Autostart: yes, Bridge: virbr1
   ```

5. **System URI Profile**:

   ```bash
   cat /etc/profile.d/libvirt.sh
   # Expected: export LIBVIRT_DEFAULT_URI="qemu:///system"
   ```

6. **Idempotency Check**:
   Re-run the playbook against the target host:
   ```bash
   ansible-playbook -i tests/inventory tests/test.yml
   # Expected: changed=0 failed=0
   ```

---

## 6. Mentor Feedback and Architecture Changelog

This section documents the specific architectural improvements implemented based on senior mentor guidance:

### 1. Two-Tier Package Architecture (Eliminating the List Replacement Trap)

- **Mentor Feedback**: Storing core packages in `defaults/main.yml` exposes a vulnerability where a user defining extra packages in `group_vars` replaces the entire list, omitting `qemu-kvm` and breaking the role.
- **Implementation**: Moved non-negotiable core binaries (`qemu-kvm`, `libvirt`, `virt-install`, `python3-libvirt`) to `vars/main.yml` as protected role constants. Exposed `rhel_kvm_extra_packages: []` in `defaults/main.yml` for optional user utilities. Updated `tasks/packages.yml` to install core packages unconditionally, followed by extra packages only if defined.

### 2. Global Libvirt System URI Configuration

- **Mentor Feedback**: Adding users to the `libvirt` group alone is insufficient because `virsh` defaults to `qemu:///session` for unprivileged users, failing to display system hypervisor VMs unless `sudo` or `--connect` is supplied.
- **Implementation**: Deployed `/etc/profile.d/libvirt.sh` (mode `0644`, owned by `root:root`) exporting `LIBVIRT_DEFAULT_URI="qemu:///system"`. All administrative users added to the `libvirt` group automatically route `virsh` commands directly to the system hypervisor.

### 3. Dedicated Bridge Network vs. Default Network Redundancy

- **Mentor Feedback**: KVM automatically initializes the `default` NAT network upon package installation. Re-querying and starting `default` is redundant and over-engineered. Instead, provision a dedicated bridge network for guest VMs.
- **Implementation**: Removed redundant tasks managing `default`. Implemented `templates/bridge_network.xml.j2` and parameterized `kvm_br0` (device `virbr1`, subnet `192.168.100.0/24`) with automated DHCP in `tasks/networks.yml`. Provides Layer-2 inter-VM switching and a direct VM-to-host gateway without modifying the host's physical network interface.

### 4. Transition from Command Module to Native Community Modules

- **Mentor Feedback**: Avoid using raw `ansible.builtin.command` when official community collection modules are available.
- **Implementation**:
  - Replaced raw `virsh net-...` CLI calls in `tasks/networks.yml` with `community.libvirt.virt_net`.
  - Replaced raw `virsh pool-...` CLI calls in `tasks/storage.yml` with `community.libvirt.virt_pool`.
  - Retained `community.general.modprobe` in `tasks/preflight.yml` and `ansible.posix.sysctl` in `tasks/sysctl.yml`.
- **Technical Justification**: Native collection modules communicate directly with the libvirt C-API via Python bindings. This provides declarative state management (`state: active`), native idempotency reporting, and elimination of subprocess shell execution overhead.

### 5. Elimination of Redundant Level 3 Nested Virtualization

- **Technical Observation**: In nested hypervisor environments (e.g. Proxmox VE), nested virtualization is controlled and enabled by the parent L0 hypervisor (by setting the VM CPU model to `host`).
- **Implementation**: Removed `/etc/modprobe.d/kvm.conf` with `options kvm_intel nested=1` from `tasks/preflight.yml`. Because guest VMs provisioned inside this hypervisor are workload servers (Level 2: web servers, databases) and not hypervisors themselves (Level 3), passing through virtualization flags to inner VMs is redundant.
- **Retained Action**: Retained the CPU virtualization flag check (`vmx`/`svm`) in `tasks/preflight.yml` to ensure Proxmox has correctly exposed hardware virtualization to this VM.

### 6. Lean Core Hypervisor Packaging

- **Technical Observation**: `libguestfs-tools` pulls in over 200 MB of dependencies and is not required for core hypervisor daemon execution or guest provisioning.
- **Implementation**: Streamlined `kvm_core_packages` in `vars/main.yml` strictly to the essential hypervisor engine (`qemu-kvm`, `libvirt`, `libvirt-client`, `virt-install`, `python3-libvirt`). Any non-critical image inspection tools can be added via `rhel_kvm_extra_packages: []` in `defaults/main.yml`.
