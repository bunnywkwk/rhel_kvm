# Ansible Role: `rhel_kvm`

An enterprise-grade Ansible role to transform a bare **RHEL 9** or **RHEL 10** server into a production-ready **KVM Hypervisor Host**.

This role dynamically adapts to the underlying operating system version, managing **Monolithic Libvirt on RHEL 9** and **Modular Libvirt Daemons on RHEL 10**, with automated storage pool provisioning, virtual networking, SELinux enforcement, and CIS Benchmark Level 1 compatibility.

---

## 🏗️ Architectural Overview & Technical Justifications

### 1. Monolithic (RHEL 9) vs. Modular (RHEL 10) Daemon Models
* **RHEL 9**: Libvirt operates as a traditional **monolithic daemon** (`libvirtd.service` / `libvirtd.socket`).
* **RHEL 10**: Red Hat completely deprecated and removed the monolithic `libvirtd`. In its place, specialized **modular daemons** handle individual subsystems (`virtqemud` for compute, `virtnetworkd` for virtual switches, `virtstoraged` for storage pools).
* **The 3-Step Decision Chain**: The role automatically detects `ansible_facts['distribution_major_version']`, loads the corresponding variable file (`vars/RedHat-9.yml` or `vars/RedHat-10.yml`), and applies the correct systemd socket units without complex inline conditionals.

### 2. CIS Benchmark Level 1 Compatibility
Default CIS hardening profiles often break virtualization hypervisors. This role addresses these conflicts:
* **Kernel IP Forwarding**: Ensures `net.ipv4.ip_forward = 1` persists in `/etc/sysctl.d/99-kvm.conf` so virtual machines on NAT bridges (`virbr0`) can route external traffic.
* **SELinux Enforcement**: Automatically applies `virt_image_t` contexts to all storage pool directories so SELinux in **Enforcing** mode allows VM disk image I/O.
* **Kernel Modules**: Loads and persists `kvm`, `vhost_net` (packet acceleration), and `tun` (virtual network driver).

---

## 📋 Requirements & Collections

### Supported Platforms
* Red Hat Enterprise Linux 9 / AlmaLinux 9 / Rocky Linux 9
* Red Hat Enterprise Linux 10 / AlmaLinux 10 / CentOS Stream 10

### Required Collections
* `ansible.posix` (>= 1.5.0)
* `community.general` (>= 7.0.0)
* `community.libvirt` (>= 1.3.0)

---

## ⚙️ Role Variables

Available default variables are defined in [`defaults/main.yml`](defaults/main.yml):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `kvm_enable_nested_virt` | `true` | Enables nested virtualization (`nested=1`) in `/etc/modprobe.d/kvm.conf`. |
| `kvm_manage_sysctl` | `true` | Configures `net.ipv4.ip_forward = 1` in `/etc/sysctl.d/99-kvm.conf`. |
| `kvm_admin_users` | `[]` | List of user accounts to add to the `libvirt` group for non-root management. |
| `kvm_packages` | *(List)* | DNF packages installed (`qemu-kvm`, `libvirt`, `virt-install`, etc.). |
| `kvm_storage_pools` | *(List)* | List of storage pools to provision (defaults to `/var/lib/libvirt/images`). |
| `kvm_manage_default_network` | `true` | Starts and enables autostart for the default `virbr0` virtual switch. |
| `kvm_default_network_name` | `default` | Name of the default libvirt virtual network. |
| `kvm_default_network_autostart` | `true` | Whether the default network starts automatically on boot. |
| `kvm_deploy_verification_tools` | `true` | Deploys `/usr/local/bin/verify_hypervisor.py` for automated compliance checks. |

---

## 🚀 Example Usage

### 1. Minimal Playbook (Default Configuration)
```yaml
---
- name: Provision KVM Hypervisors
  hosts: hypervisors
  become: true
  roles:
    - role: rhel_kvm
```

### 2. Custom Storage Pools and Admin Users
```yaml
---
- name: Provision KVM Hypervisor with Custom Storage
  hosts: hypervisors
  become: true
  vars:
    kvm_admin_users:
      - sysadmin
      - bunny
    kvm_storage_pools:
      - name: default
        path: /var/lib/libvirt/images
        type: dir
        autostart: true
        state: active
      - name: iso_pool
        path: /var/lib/libvirt/iso
        type: dir
        autostart: true
        state: active
  roles:
    - role: rhel_kvm
```

---

## 🔍 Verification & Health Checks

### 1. Automated 1-Click Verification Tool (Recommended)
This role automatically deploys a standalone Python diagnostic verification script to `/usr/local/bin/verify_hypervisor.py`.

SSH into the hypervisor host and run:
```bash
/usr/local/bin/verify_hypervisor.py
```

This tool automatically validates:
* **OS Architecture**: Detects RHEL 9 vs RHEL 10.
* **Daemon Model**: Verifies active `libvirtd` on RHEL 9 or active modular sockets (`virtqemud`, `virtnetworkd`, `virtstoraged`) and masked legacy daemons on RHEL 10.
* **Storage Pools**: Confirms active storage pools, autostart status, and `virt_image_t` SELinux context.
* **Virtual Networking**: Checks `virbr0` bridge status and autostart.
* **Kernel & Routing**: Verifies `net.ipv4.ip_forward = 1` and loaded kernel modules (`kvm`, `vhost_net`, `tun`).

---

### 2. Manual CLI Commands
Alternatively, you can manually verify individual components:

```bash
# 1. Verify Active Sockets / Services
# On RHEL 9:
systemctl is-active libvirtd.socket libvirtd.service

# On RHEL 10:
systemctl is-active virtqemud.socket virtnetworkd.socket virtstoraged.socket

# 2. Check Storage Pools
virsh pool-list --all

# 3. Check Virtual Networks
virsh net-list --all

# 4. Verify Kernel IP Forwarding
sysctl net.ipv4.ip_forward
# Expected output: net.ipv4.ip_forward = 1
```

---

## 📄 License & Author
* **License**: MIT
* **Author**: Aeron (Trainee at AIRNAV)