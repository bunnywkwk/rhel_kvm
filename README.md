# Ansible Role: rhel_kvm

An enterprise-grade Ansible role to transform a bare **RHEL 9** or **RHEL 10** server into a production-ready **KVM Hypervisor Host**.

The role adapts to the underlying operating system version, managing **monolithic `libvirtd` on RHEL 9** and **modular libvirt daemons on RHEL 10**, with automated storage pool provisioning, virtual networking, SELinux enforcement, and CIS Benchmark Level 1 compatibility.

Task-by-task explanation (what each task does and why it exists): [docs/TASK_WALKTHROUGH.md](docs/TASK_WALKTHROUGH.md).
Architectural justifications and folder structure: [docs/ARCHITECTURE_AND_JUSTIFICATIONS.md](docs/ARCHITECTURE_AND_JUSTIFICATIONS.md).

---

## 1. Architectural Overview

### Monolithic (RHEL 9) vs. Modular (RHEL 10) daemon models

- **RHEL 9**: libvirt runs as the traditional **monolithic daemon** (`libvirtd.service` / `libvirtd.socket`). This is a fixed requirement, not just an OS default. The modular daemons (`virtqemud`, `virtnetworkd`, ...) are stopped and masked so they can never run alongside it.
- **RHEL 10**: Red Hat removed the monolithic `libvirtd`. Specialised **modular daemons** handle individual subsystems (`virtqemud` for compute, `virtnetworkd` for virtual switches, `virtstoraged` for storage pools).
- **OS adaptation**: the role reads `ansible_facts['distribution_major_version']`, loads `vars/RedHat-9.yml` or `vars/RedHat-10.yml`, and applies the matching sockets/services without inline conditionals.
- **`rhel_kvm_libvirt_uri`**: on RHEL 9.8+ the libvirt client library resolves a bare `qemu:///system` to the modular socket (`virtqemud-sock`) whenever the modular packages are on disk, regardless of which daemon is enabled. Masking the modular daemons alone therefore does not keep `libvirtd` reachable. Every `community.libvirt.*` task, and the `LIBVIRT_DEFAULT_URI` exported for interactive `virsh`, uses this variable: on RHEL 9 it is the explicit `qemu+unix:///system?socket=/var/run/libvirt/libvirt-sock`, on RHEL 10 plain `qemu:///system`.

### CIS Benchmark Level 1 compatibility

Default CIS hardening profiles often conflict with virtualization hypervisors. This role addresses that proactively:

- **Kernel IP forwarding**: `net.ipv4.ip_forward = 1` persists in `/etc/sysctl.d/99-kvm.conf`, so VMs on NAT bridges can route external traffic. The `99-` prefix sorts after CIS's `60-*.conf`, so it wins when `sysctl --system` runs.
- **SELinux enforcement**: `virt_image_t` is applied to every storage pool directory so SELinux in `Enforcing` mode allows VM disk I/O.
- **Kernel modules**: `kvm`, `vhost_net` (in-kernel packet acceleration) and `tun` (virtual network driver) are loaded and persisted.

---

## 2. Requirements

### Supported platforms

- Red Hat Enterprise Linux 9 / AlmaLinux 9 / Rocky Linux 9
- Red Hat Enterprise Linux 10 / AlmaLinux 10 / CentOS Stream 10

### Required collections

- `ansible.posix` (>= 1.5.0)
- `community.general` (>= 7.0.0)
- `community.libvirt` (>= 1.3.0)

---

## 3. Role Variables

Overridable defaults are in [defaults/main.yml](defaults/main.yml):

| Variable                         | Default         | Description                                                                  |
| :------------------------------- | :-------------- | :--------------------------------------------------------------------------- |
| `rhel_kvm_manage_sysctl`         | `true`          | Configures `net.ipv4.ip_forward = 1` in `/etc/sysctl.d/99-kvm.conf`.         |
| `rhel_kvm_admin_users`           | `[]`            | Accounts added to the `libvirt` group for non-root management.               |
| `rhel_kvm_extra_packages`        | `[]`            | Optional extra packages installed alongside the core hypervisor packages.    |
| `rhel_kvm_storage_pools`         | _(list)_        | Storage pools to provision (default: `/var/lib/libvirt/images`).             |
| `rhel_kvm_manage_bridge_network` | `true`          | Whether to provision the dedicated hypervisor bridge network.                |
| `rhel_kvm_bridge_network_name`   | `kvm_br0`       | Name of the dedicated virtual network in libvirt.                            |
| `rhel_kvm_bridge_device`         | `virbr1`        | Linux bridge interface name for that network.                                |
| `rhel_kvm_bridge_ip`             | `192.168.100.1` | Gateway IP assigned to the hypervisor on the bridge.                         |
| `rhel_kvm_bridge_netmask`        | `255.255.255.0` | Netmask of the bridge network.                                               |
| `rhel_kvm_bridge_dhcp_start/end` | `.10` / `.254`  | DHCP range handed to guests.                                                 |
| `rhel_kvm_bridge_autostart`      | `true`          | Whether the bridge network starts automatically on boot.                     |

Protected constants live in `vars/` (higher precedence, so `group_vars` cannot accidentally replace them): the core package list (`vars/main.yml`) and the per-OS daemon, socket, URI and `redhat-release` settings (`vars/RedHat-9.yml`, `vars/RedHat-10.yml`).

---

## 4. Example Usage

### Minimal playbook

```yaml
---
- name: Provision KVM Hypervisors
  hosts: hypervisors
  become: true
  roles:
    - role: rhel_kvm
```

### Custom storage pools and admin users

```yaml
---
- name: Provision KVM Hypervisor with Custom Storage
  hosts: hypervisors
  become: true
  vars:
    rhel_kvm_admin_users:
      - sysadmin
      - bunny
    rhel_kvm_extra_packages:
      - guestfs-tools
    rhel_kvm_storage_pools:
      - name: default
        path: /var/lib/libvirt/images
        type: dir
        autostart: true
      - name: iso_pool
        path: /var/lib/libvirt/iso
        type: dir
        autostart: true
  roles:
    - role: rhel_kvm
```

---

## 5. Verification

Run these on the hypervisor:

```bash
# 1. Sockets / services
# RHEL 9:
systemctl is-active libvirtd.socket libvirtd.service
systemctl is-enabled virtqemud.socket virtnetworkd.socket virtstoraged.socket   # expect "masked"
# RHEL 10:
systemctl is-active virtqemud.socket virtnetworkd.socket virtstoraged.socket

# 1b. RHEL 9 only: test with the explicit URI the role uses. A bare
# `virsh -c qemu:///system` can resolve to the masked modular socket and fail
# even though libvirtd is healthy.
virsh -c "qemu+unix:///system?socket=/var/run/libvirt/libvirt-sock" list --all

# 2. Storage pools and networks
virsh pool-list --all
virsh net-list --all

# 3. Kernel IP forwarding
sysctl net.ipv4.ip_forward     # expect net.ipv4.ip_forward = 1
```

---

## 6. License and Author

- **License**: MIT
- **Author**: Aeron (Trainee at AIRNAV)
