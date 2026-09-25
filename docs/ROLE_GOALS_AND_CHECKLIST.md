# rhel_kvm - Role Goals & Progress Checklist

## Role Mission
Provision a bare RHEL 9 or RHEL 10 server into a production-grade KVM Hypervisor Host with:
* Dynamic detection of **Monolithic Libvirt (RHEL 9)** vs **Modular Libvirt (RHEL 10)** via a 3-step variable decision chain.
* Automated storage pool provisioning with SELinux `virt_image_t` context.
* Virtual network (`virbr0`) and kernel IP forwarding (`net.ipv4.ip_forward = 1`).
* Full readiness for Cockpit web management and CIS hardening.

---

## Role Checklist

### Step 1: Metadata, Defaults & OS Variables (Completed)
- [x] **`meta/main.yml`**: Galaxy metadata, EL 9 & 10 platform support, collection requirements (`ansible.posix`, `community.general`, `community.libvirt`).
- [x] **`defaults/main.yml`**: Configurable defaults for packages, nested virt, storage pools, sysctl, and users.
- [x] **`vars/RedHat-9.yml`**: Monolithic libvirt: `libvirtd.service` is enabled and started; no sockets are managed and nothing is masked.
- [x] **`vars/RedHat-10.yml`**: The sockets of all drivers and `virtqemud.service`, and the legacy `libvirtd` units masked.

---

### Step 2: Tasks Implementation (Completed)
- [x] **`tasks/main.yml`**: Master orchestrator calling sub-tasks in logical sequence.
- [x] **`tasks/preflight.yml`**: OS check and kernel modules (`kvm`, `vhost_net`, `tun`).
- [x] **`tasks/packages.yml`**: On RHEL 10, update `redhat-release` (`rhel_kvm_update_redhat_release`), then DNF installation of `qemu-kvm`, `libvirt`, `virt-install`, `python3-libvirt`.
- [x] **`tasks/sysctl.yml`**: Kernel IP forwarding configuration (`/etc/sysctl.d/99-kvm.conf`).
- [x] **`tasks/daemons.yml`**: Enabling/starting the appropriate sockets and services per OS major version.
- [x] **`tasks/storage.yml`**: Directory creation, SELinux labeling (`virt_image_t`), and storage pool provisioning.
- [x] **`tasks/networks.yml`**: Virtual network autostart and optional Linux bridge configuration.

---

### Step 3: Handlers, Template & Tests (Completed)
- [x] **`handlers/main.yml`**: `Reload sysctl` handler, notified when `/etc/sysctl.d/99-kvm.conf` changes.
- [x] **`templates/bridge_network.xml.j2`**: libvirt XML for the `kvm_br0` network.
- [x] **`tests/inventory` & `tests/test.yml`**: Test inventory and test playbook targeting test VM.

---

### Step 4: Documentation (Completed)
- [x] **`README.md`**: Complete architectural justifications, compatibility matrix, and variable reference table.
- [x] **`docs/`**: Isolated Q&A knowledge base and goals checklist.

---

## Task-by-Task One-Liner Quick Guide

| Task File | What each task does |
| :--- | :--- |
| **`tasks/main.yml`** | Master table of contents; dynamically loads OS variables and calls each subtask file in strict order. |
| **`tasks/preflight.yml`** | Checks the OS, loads `kvm`/`tun`/`vhost_net` kernel modules, and configures nested virtualization. |
| **`tasks/packages.yml`** | Installs `qemu-kvm`, `libvirt`, `virt-install`, and management tools via `dnf`. |
| **`tasks/sysctl.yml`** | Enables `net.ipv4.ip_forward = 1` so Linux routes traffic between the virtual switch and the outside internet. |
| **`tasks/daemons.yml`** | Starts `libvirtd.service` on RHEL 9; starts the driver sockets and `virtqemud.service` on RHEL 10. |
| **`tasks/storage.yml`** | Creates storage directories (`/var/lib/libvirt/images`), sets SELinux `virt_image_t` context, and starts disk pools. |
| **`tasks/networks.yml`** | Starts and autostarts the default internal virtual switch (`virbr0`). |
