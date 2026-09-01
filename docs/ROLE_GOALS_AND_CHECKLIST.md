# rhel_kvm - Role Goals & Progress Checklist

## 🎯 Role Mission
Provision a bare RHEL 9 or RHEL 10 server into a production-grade KVM Hypervisor Host with:
* Dynamic detection of **Monolithic Libvirt (RHEL 9)** vs **Modular Libvirt (RHEL 10)** via a 3-step variable decision chain.
* Automated storage pool provisioning with SELinux `virt_image_t` context.
* Virtual network (`virbr0`) and kernel IP forwarding (`net.ipv4.ip_forward = 1`).
* Full readiness for Cockpit web management and CIS hardening.

---

## 📋 Role Checklist

### Step 1: Metadata, Defaults & OS Variables (Completed)
- [x] **`meta/main.yml`**: Galaxy metadata, EL 9 & 10 platform support, collection requirements (`ansible.posix`, `community.general`, `community.libvirt`).
- [x] **`defaults/main.yml`**: Configurable defaults for packages, nested virt, storage pools, sysctl, and users.
- [x] **`vars/RedHat-9.yml`**: Monolithic libvirt services (`libvirtd.service`) and sockets (`libvirtd.socket`).
- [x] **`vars/RedHat-10.yml`**: Modular libvirt driver sockets (`virtqemud`, `virtnetworkd`, `virtstoraged`, etc.) and mask configuration for monolithic units.

---

### Step 2: Tasks Implementation (Completed)
- [x] **`tasks/main.yml`**: Master orchestrator calling sub-tasks in logical sequence.
- [x] **`tasks/preflight.yml`**: CPU hardware virtualization check (`vmx`/`svm`) and kernel modules (`kvm`, `vhost_net`, `tun`).
- [x] **`tasks/packages.yml`**: DNF installation of `qemu-kvm`, `libvirt`, `virt-install`, `libguestfs-tools`.
- [x] **`tasks/sysctl.yml`**: Kernel IP forwarding configuration (`/etc/sysctl.d/99-kvm.conf`).
- [x] **`tasks/daemons.yml`**: Enabling/starting the appropriate sockets and services per OS major version.
- [x] **`tasks/storage.yml`**: Directory creation, SELinux labeling (`virt_image_t`), and storage pool provisioning.
- [x] **`tasks/networks.yml`**: Virtual network autostart and optional Linux bridge configuration.
- [x] **`tasks/users.yml`**: Adding admin users to the `libvirt` group.

---

### Step 3: Handlers, Templates & Tests (Completed)
- [x] **`handlers/main.yml`**: Service reload, systemd reload, and sysctl refresh handlers.
- [x] **`templates/storage_pool.xml.j2`**: Idempotent libvirt storage pool XML template.
- [x] **`tests/inventory` & `tests/test.yml`**: Test inventory and test playbook targeting test VM.

---

### Step 4: Documentation (Completed)
- [x] **`README.md`**: Complete architectural justifications, compatibility matrix, and variable reference table.
- [x] **`docs/`**: Isolated Q&A knowledge base and goals checklist.

---

## ⚡ Task-by-Task One-Liner Quick Guide

| Task File | What each task does |
| :--- | :--- |
| **`tasks/main.yml`** | Master table of contents; dynamically loads OS variables and calls each subtask file in strict order. |
| **`tasks/preflight.yml`** | Verifies CPU hardware virtualization (`vmx`/`svm`), loads `kvm`/`tun`/`vhost_net` kernel modules, and configures nested virtualization. |
| **`tasks/packages.yml`** | Installs `qemu-kvm`, `libvirt`, `virt-install`, and management tools via `dnf`. |
| **`tasks/sysctl.yml`** | Enables `net.ipv4.ip_forward = 1` so Linux routes traffic between the virtual switch and the outside internet. |
| **`tasks/daemons.yml`** | Starts `libvirtd.socket` & `.service` on RHEL 9; starts the 6 modular driver sockets on RHEL 10. |
| **`tasks/storage.yml`** | Creates storage directories (`/var/lib/libvirt/images`), sets SELinux `virt_image_t` context, and starts disk pools. |
| **`tasks/networks.yml`** | Starts and autostarts the default internal virtual switch (`virbr0`). |
| **`tasks/users.yml`** | Adds admin users to the `libvirt` system group for non-root VM management. |
