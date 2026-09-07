# rhel_kvm - Role Architecture and Technical Justifications

## 1. Executive Summary

The `rhel_kvm` role transforms a clean installation of Enterprise Linux (RHEL 9, RHEL 10, AlmaLinux, or Rocky Linux) into a fully functional, headless KVM Hypervisor node.

The role solves four fundamental operational problems:

1. **OS-Level Architecture Divergence**: RHEL 9 relies on a monolithic libvirt daemon (`libvirtd`), whereas RHEL 10 mandates modular driver daemons (`virtqemud`, `virtnetworkd`, `virtstoraged`, etc.).
2. **The Variable Precedence Trap**: Prevents user-defined packages in `group_vars` from overriding and omitting mandatory hypervisor binaries.
3. **SELinux and Security Confinement**: Enforces `virt_image_t` contexts and restrictive file permissions (`0711`) so virtual machines execute reliably under SELinux `Enforcing` mode without permission denials.
4. **Network and Hypervisor Verification**: Guarantees kernel IP forwarding for NAT routing and provides an automated, on-host verification script for immediate operational acceptance.

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
├── files/
│   └── verify_hypervisor.py
└── docs/
    ├── ARCHITECTURE_AND_JUSTIFICATIONS.md
    ├── KNOWLEDGE_BASE_QA.md
    └── ROLE_GOALS_AND_CHECKLIST.md
```

### Directory-by-Directory Rationale

| Directory   | Purpose                           | Technical Justification                                                                                                                                |
| :---------- | :-------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `meta/`     | Role metadata and dependencies    | Defines supported OS versions and collection requirements (`ansible.posix`, `community.general`, `community.libvirt`). Required for Galaxy compliance. |
| `defaults/` | Overridable user variables        | Lowest precedence. Contains settings intended for user customization (e.g. `kvm_extra_packages`, storage paths, admin user list).                      |
| `vars/`     | Protected architectural constants | High precedence. Stores OS-specific daemon mappings and mandatory core packages. Users cannot accidentally overwrite these from `group_vars`.          |
| `tasks/`    | Sequential task execution         | Broken into modular sub-task files rather than a monolithic script. Allows isolated maintenance and conditional execution.                             |
| `handlers/` | Event-driven notifications        | Flushes service restarts and sysctl reloads only when state has physically changed, preserving idempotency.                                            |
| `files/`    | Static files and tools            | Contains the standalone Python verification script deployed to `/usr/local/bin/verify_hypervisor.py`.                                                  |
| `docs/`     | Technical documentation           | Isolates role documentation, design justifications, and Q&A logs within the role boundary.                                                             |

---

## 3. Package Architecture: Core vs. Extra Packages

### The Problem: The List Replacement Trap

In standard Ansible roles, placing the package list in `defaults/main.yml` exposes a critical flaw. If a user defines `kvm_packages: [guestfs-tools]` in their inventory `group_vars`, Ansible completely replaces the default list. Consequently, `qemu-kvm`, `libvirt`, and `virt-install` are omitted, breaking the hypervisor.

### The Solution: Two-Tier Package Strategy

1. **`vars/main.yml` (Mandatory Core Packages)**:
   Contains the minimum set required for the hypervisor engine:
   - `qemu-kvm`: Hardware emulator and hypervisor backend.
   - `libvirt`: Virtualization management daemon suite.
   - `libvirt-client`: CLI toolset (`virsh`).
   - `virt-install`: Provisioning tool for guest creation.
   - `python3-libvirt`: Python bindings required by Ansible `community.libvirt` modules.

2. **`defaults/main.yml` (Optional User Packages)**:
   Exposes `kvm_extra_packages: []`. Users can add any extra utility without risking the core platform.

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

- **What it does**: Installs core packages from `kvm_core_packages` and optional packages from `kvm_extra_packages`.
- **Justification**: Separates mandatory binaries from user additions, ensuring fail-safe package deployment.

---

### Step 3: `tasks/sysctl.yml` (Packet Forwarding)

- **What it does**: Configures `net.ipv4.ip_forward = 1` in `/etc/sysctl.d/99-kvm.conf` and reloads sysctl via handler.
- **Justification**: Guest VMs attached to the default NAT bridge (`virbr0`) cannot transmit packets beyond the local host without kernel-level IP forwarding enabled.

---

### Step 4: `tasks/daemons.yml` (Daemon Management per OS Version)

- **What it does**:
  - **RHEL 9**: Enables and starts `libvirtd.socket` and `libvirtd.service` (Monolithic model).
  - **RHEL 10**: Enables and starts modular sockets (`virtqemud.socket`, `virtnetworkd.socket`, `virtstoraged.socket`, etc.) and masks legacy `libvirtd.service`.
- **Justification**:
  - RHEL 10 completely removes `libvirtd`. Trying to start `libvirtd` on RHEL 10 results in fatal systemd unit-not-found errors.
  - Dynamically loading variables from `vars/RedHat-{{ major_version }}.yml` ensures clean execution without inline conditionals on every task.

[SCREENSHOT: systemctl status output showing monolithic libvirtd on RHEL 9 vs modular virtqemud on RHEL 10]

---

### Step 5: `tasks/storage.yml` (Storage Pools and SELinux Labeling)

- **What it does**:
  1. Creates `/var/lib/libvirt/images` with directory permissions `0711` and ownership `root:root`.
  2. Registers and applies SELinux context `virt_image_t` using `community.general.sefcontext`.
  3. Defines, builds, and starts the storage pool idempotently using `community.libvirt.virt_pool` with `templates/storage_pool.xml.j2`.
- **Justification**:
  - Directory permission `0711` (`drwx--x--x`) prevents unprivileged users from reading VM image files while permitting hypervisor process traversal.
  - Under SELinux `Enforcing`, QEMU cannot read or write to directories labeled with generic `var_t` or `default_t` contexts. Setting `virt_image_t` avoids permission denials.
  - **Dynamic Storage Path Customization**: Because `/var/lib/libvirt/images` is on the root partition (`/`), administrators frequently redirect VM storage to dedicated RAID/NVMe arrays (e.g. `/data/vms`). Leaving `kvm_storage_pools` in `defaults/main.yml` ensures users can customize paths without touching code, while the role dynamically creates the target folder, registers the SELinux context, and configures Libvirt seamlessly.
  - Using `community.libvirt.virt_pool` replaces shell/command calls with native libvirt API bindings, ensuring strict idempotency (`changed=0` on repeated runs).

[SCREENSHOT: virsh pool-list --all command output showing default storage pool in Active state with Autostart enabled]

---

### Step 6: `tasks/networks.yml` (Dedicated Hypervisor Bridge Network)

- **What it does**:
  1. Leaves the built-in `default` network (`virbr0`) intact as provided natively by the libvirt package.
  2. Idempotently defines and starts a dedicated virtual bridge network (`kvm_br0` on device `virbr1`) using `templates/bridge_network.xml.j2`.
  3. Configures an isolated IP subnet (`192.168.100.0/24`) with gateway `192.168.100.1` and automated DHCP range (`192.168.100.10` - `192.168.100.254`).
  4. Enables automatic startup on boot (`autostart: true`).
- **Justification**:
  - **Eliminating Redundancy**: The default network is already created by RPM installation. Removing redundant tasks that re-query and manipulate `default` keeps the role clean and unbloated.
  - **Inter-VM Layer 2 Switching**: Guest VMs plugged into `kvm_br0` communicate directly with one another at near-wire kernel speeds without traversing external routers.
  - **VM-to-Host Management Gateway**: The KVM host is assigned `192.168.100.1` on the bridge, establishing a permanent, dedicated management channel between the hypervisor and all provisioned VMs.
  - **Zero Remote Connection Risk**: Provisioning a dedicated virtual bridge device (`virbr1`) avoids altering the host's physical network adapter (`enp1s0`), preventing accidental SSH disconnects or network lockouts.

[SCREENSHOT: virsh net-list --all command output showing both default and kvm_br0 networks active and autostarted]

---

### Step 7: `tasks/users.yml` (User Access and Global System URI)

- **What it does**:
  1. Adds users defined in `kvm_admin_users` to the `libvirt` group with `append: true`.
  2. Deploys `/etc/profile.d/libvirt.sh` containing `export LIBVIRT_DEFAULT_URI="qemu:///system"`.
- **Justification**:
  - The libvirt socket (`/run/libvirt/libvirt-sock`) is owned by group `libvirt` (mode `0660`). Membership in this group grants VM management permissions without requiring root or `sudo`.
  - By default, unprivileged users querying `virsh` connect to `qemu:///session` (an empty user-space instance). Deploying `/etc/profile.d/libvirt.sh` guarantees that `virsh` automatically routes to `qemu:///system` for all users in the `libvirt` group.

[SCREENSHOT: virsh list --all executed as non-root user displaying hypervisor VMs without sudo]

---

### Step 8: Verification Deployment (`tasks/main.yml`)

- **What it does**: Deploys `/usr/local/bin/verify_hypervisor.py` (executable mode `0755`, owned by `root:root`).
- **Justification**: Provides an automated, on-host health check script that verifies CPU virtualization, active daemons, storage pools, network bridges, and sysctl settings, outputting a clear PASS/FAIL compliance report.

[SCREENSHOT: verify_hypervisor.py output displaying all criteria passed (100%)]

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

6. **Automated Verification Script**:

   ```bash
   /usr/local/bin/verify_hypervisor.py
   # Expected: [OK] ALL ACCEPTANCE CRITERIA PASSED! (100%)
   ```

7. **Idempotency Check**:
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
- **Implementation**: Moved non-negotiable core binaries (`qemu-kvm`, `libvirt`, `virt-install`, `python3-libvirt`) to `vars/main.yml` as protected role constants. Exposed `kvm_extra_packages: []` in `defaults/main.yml` for optional user utilities. Updated `tasks/packages.yml` to install core packages unconditionally, followed by extra packages only if defined.

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
- **Implementation**: Streamlined `kvm_core_packages` in `vars/main.yml` strictly to the essential hypervisor engine (`qemu-kvm`, `libvirt`, `libvirt-client`, `virt-install`, `python3-libvirt`). Any non-critical image inspection tools can be added via `kvm_extra_packages: []` in `defaults/main.yml`.
