# rhel_kvm - Knowledge Base & Q&A Reference

This document contains the core technical questions, architecture decisions, networking fundamentals, task cheat sheets, and deep-dive justifications for the `rhel_kvm` role.

---

## ❓ Q&A Reference

### Q1: What does "Take a bare RHEL 9 or RHEL 10 machine to a hardened KVM host" mean?

- **A**: It is the complete automated transformation from a fresh minimal Linux installation into a fully operational, secure hypervisor:
  1. **Bare Machine**: Initial install with no virtualization packages and default unhardened OS configuration.
  2. **Hypervisor Engine**: Installing `qemu-kvm`, `libvirt`, configuring storage pools (`/var/lib/libvirt/images`), enabling virtual bridges (`virbr0`), and starting the correct libvirt daemon architecture.
  3. **Hardened Hypervisor**: Complying with CIS benchmarks while ensuring hypervisor-specific exceptions (IP forwarding, virtualization kernel modules, SELinux contexts) remain intact.

---

### Q2: Why does RHEL 9 use Monolithic Libvirt while RHEL 10 uses Modular Libvirt?

- **A**:
  - **RHEL 9 (Monolithic)**: A single service `libvirtd` handles everything (QEMU management, storage pools, network interfaces, secret management).
  - **RHEL 10 (Modular)**: Red Hat removed the monolithic `libvirtd` daemon. Instead, specialized daemons manage individual subsystems:
    - `virtqemud`: Handles QEMU/KVM virtual machine instances.
    - `virtnetworkd`: Handles virtual bridges, NAT, and network routing.
    - `virtstoraged`: Handles storage pools and disk volumes.
    - `virtnodedevd`: Handles host hardware devices / PCI passthrough.
    - `virtsecretd`: Handles encryption keys and passwords.
    - `virtnwfilterd`: Handles network firewall filters.
  - **Why this change occurred**: **Security & Isolation (Least Privilege)**. If one driver (e.g. storage) fails or encounters an exploit, it cannot compromise the hypervisor kernel or other drivers.

---

### Q3: How does Ansible determine and apply the correct daemon model between RHEL 9 and RHEL 10?

- **A**: The role uses a clean **3-Step Decision Chain** without any messy, hardcoded `if/else` conditions in the task files:

```
[1. Target Host Fact] ──> [2. Dynamic include_vars in main.yml] ──> [3. Generic Loop in daemons.yml]
  (RHEL 9 or RHEL 10)       (Loads RedHat-9.yml or RedHat-10.yml)       (Iterates through loaded vars)
```

#### Step 1: Automatic OS Detection (Ansible Facts)

When Ansible connects to the server, it automatically runs fact gathering and discovers the OS major version:

- On RHEL 9: `ansible_facts['distribution_major_version'] = "9"`
- On RHEL 10: `ansible_facts['distribution_major_version'] = "10"`

#### Step 2: Dynamic Variable Loading in `tasks/main.yml`

The very first task in `tasks/main.yml` dynamically points to the matching file in `vars/`:

```yaml
- name: Include OS-specific variables
  ansible.builtin.include_vars: "{{ ansible_facts['os_family'] }}-{{ ansible_facts['distribution_major_version'] }}.yml"
```

- **If RHEL 9**: It reads `vars/RedHat-9.yml` and loads:
  - `kvm_sockets`: `[libvirtd.socket, libvirtd-ro.socket, libvirtd-admin.socket]`
  - `kvm_services`: `[libvirtd.service]`
  - `kvm_disabled_services`: `[]`
- **If RHEL 10**: It reads `vars/RedHat-10.yml` and loads:
  - `kvm_sockets`: `[virtqemud.socket, virtnetworkd.socket, virtstoraged.socket, ...]`
  - `kvm_services`: `[]`
  - `kvm_disabled_services`: `[libvirtd.service, libvirtd.socket, ...]`

#### Step 3: Clean Execution in `tasks/daemons.yml`

`daemons.yml` stays 100% generic and simply iterates through the variables loaded in Step 2:

1. **Disabling conflicting legacy daemons**:

   ```yaml
   - name: Disable conflicting services if applicable
     ansible.builtin.systemd_service:
       name: "{{ item }}"
       enabled: false
       state: stopped
       masked: true
     loop: "{{ kvm_disabled_services | default([]) }}"
     failed_when: false
   ```

   - _On RHEL 9_: `kvm_disabled_services` is empty `[]` → nothing is disabled.
   - _On RHEL 10_: It actively disables and masks legacy `libvirtd` units so they can never interfere.

2. **Starting the appropriate Sockets**:

   ```yaml
   - name: Enable and start libvirt systemd sockets
     ansible.builtin.systemd_service:
       name: "{{ item }}"
       enabled: true
       state: started
     loop: "{{ kvm_sockets }}"
   ```

   - _On RHEL 9_: Starts `libvirtd.socket` units.
   - _On RHEL 10_: Starts all modular sockets (`virtqemud.socket`, `virtnetworkd.socket`, `virtstoraged.socket`, etc.).

3. **Starting Monolithic Service (RHEL 9 only)**:
   ```yaml
   - name: Enable and start libvirt systemd services (Monolithic)
     ansible.builtin.systemd_service:
       name: "{{ item }}"
       enabled: true
       state: started
     loop: "{{ kvm_services }}"
     when: kvm_services | length > 0
   ```

   - _On RHEL 9_: `kvm_services` has `[libvirtd.service]` → starts the monolithic service.
   - _On RHEL 10_: `kvm_services` is `[]` (empty) → Ansible automatically skips this task cleanly.

---

### Q4: What is the difference between a `.service` and a `.socket` in Systemd?

- **A**:
  - **`.service` (The Worker)**: The actual running program/process. It consumes RAM and CPU as long as it is active.
  - **`.socket` (The Doorbell / Socket Activation)**: A lightweight communication endpoint managed by Systemd. When an application (like `virsh` or Cockpit) tries to connect, Systemd automatically starts the `.service` on demand.
  - **Why RHEL 10 uses `.socket` for modular daemons**: Instead of running 6 separate daemon processes 24/7 and wasting RAM, Systemd listens on their sockets and only starts individual services when needed.
  - **Socket Types**:
    - Standard socket: Read/write connection for management.
    - `-ro.socket`: Read-only socket for monitoring tools.
    - `-admin.socket`: Hypervisor administrative and tuning socket.

---

### Q5: How does KVM Networking Work (Bridges, NAT, and sysctl)?

- **A**:

```
 [ Guest VM ] (e.g. 192.168.122.50)
       │
       ▼ (vnet0 interface)
 ┌────────────────────────────────────────────────────────┐
 │ Virtual Bridge: virbr0 (192.168.122.1)                │
 │ • Acts as an internal virtual switch + router         │
 │ • Runs dnsmasq to provide DHCP/DNS to VMs             │
 └────────────────────────────────────────────────────────┘
       │
       ▼  <─── sysctl: net.ipv4.ip_forward = 1 (Routes traffic)
 ┌────────────────────────────────────────────────────────┐
 │ Host Physical NIC: eth0 (e.g. 10.0.0.15)              │
 └────────────────────────────────────────────────────────┘
       │
       ▼
 [ External Network / Internet ]
```

1. **Virtual Bridge (`virbr0`)**: A software switch created on the host. VMs plug into this virtual switch.
2. **`net.ipv4.ip_forward = 1`**: Instructs the Linux kernel to forward network packets between the virtual bridge (`virbr0`) and the physical NIC (`eth0`). Without this, VMs cannot reach the internet.
3. **Variables in `defaults/main.yml`**:
   - `kvm_manage_default_network`: Toggles management of the default NAT switch.
   - `kvm_default_network_name`: Name of the libvirt network (`default`).
   - `kvm_default_network_autostart`: Ensures the bridge starts automatically at host boot.
   - `kvm_custom_bridges`: Used when VMs need direct physical IP addresses on the external LAN (bridged mode) instead of private NAT IPs.

---

### Q6: Why must CIS Level 1 variables be tailored for a KVM Host?

- **A**: Default CIS benchmarks are built for generic servers. If run without hypervisor overrides:
  1. **`net.ipv4.ip_forward = 0`**: CIS disables packet forwarding. This immediately destroys VM network connectivity across virtual bridges/NAT (`virbr0`). We set `net.ipv4.ip_forward = 1`.
  2. **Kernel Modules**: CIS blacklists unused kernel modules. We ensure `kvm`, `vhost_net`, and `tun` are loaded and persistent.
  3. **SELinux Contexts**: Storage pools must have `virt_image_t` context so SELinux in Enforcing mode allows KVM to write disk images.

---

### Q7: Why do CIS Roles (like Ansible Lockdown) use `import_tasks` while `rhel_kvm` uses `include_tasks`?

- **A**:
  - **Ansible Lockdown CIS Benchmark Roles**:
    - Have 300–500 static compliance rules.
    - **Require `import_tasks`** so that `--tags rule_1.1.1` and `--skip-tags` work from the CLI, `--list-tasks` outputs the complete auditor rule inventory, and `--start-at-task` can resume failed runs.
  - **`rhel_kvm` Provisioning Role**:
    - Has an 8-step sequential infrastructure pipeline.
    - **Uses `include_tasks`** because features like `sysctl.yml` and `users.yml` are conditionally toggled (`when: kvm_manage_sysctl | bool`). `include_tasks` allows skipping entire disabled feature files in 1 quick step at runtime instead of outputting 10 noisy skipped task lines.

---

### Q8: What is the exact behavior of `include_tasks` and why did we choose it for `rhel_kvm`?

- **A**:
  1. **Runtime "Lazy Loading"**: It is evaluated only when execution reaches that line, ensuring it uses the freshest runtime facts and registered variables.
  2. **Single-Step Feature Skipping**: When a condition like `when: kvm_manage_sysctl | bool` is `false`, Ansible evaluates it once on the `include_tasks` statement and skips the whole file in 1 millisecond.
  3. **Clean Execution Logs**: Keeps the terminal output concise and professional for infrastructure provisioning.

---

### Q9: Is the default storage pool already included and activated by KVM out of the box?

- **A**:
  - **Filesystem Directory**: **YES**. The RPM package `libvirt-daemon` creates `/var/lib/libvirt/images` on disk.
  - **Libvirt Logical Storage Pool**: **NO**. On a fresh minimal RHEL 9 or RHEL 10 CLI install, `virsh pool-list --all` returns an **empty table**. Libvirt does not register the directory as a storage pool until someone defines it (`virsh pool-define-as` or `community.libvirt.virt_pool`) and starts it.
  - **Why our role manages it**: If our automation did not activate it, CLI tools like `virt-install` and acceptance scripts would fail with `error: Storage pool not found`. Our role uses `community.libvirt.virt_pool` with `state: active` and `autostart: true` to guarantee the hypervisor is 100% operational immediately.

---

### Q10: Should the storage directory path be exposed in `defaults/main.yml` or locked in `vars/main.yml`? Does it work dynamically?

- **A**:
  - **It MUST be exposed in `defaults/main.yml`**: `/var/lib/libvirt/images` resides on the root filesystem (`/`), which is typically a small OS boot drive (50GB–100GB). In enterprise datacenters, virtual machine disks (100GB–1TB each) are stored on dedicated secondary RAID or NVMe mounts (e.g. `/data/vms`). Locking this path in `vars/` would prevent administrators from redirecting storage to dedicated disks.
  - **Yes, it works 100% dynamically**: When an administrator sets `path: /data/vms` in `defaults/main.yml` or `group_vars`:
    1. `tasks/storage.yml` creates the directory `/data/vms` with mode `0711`.
    2. `community.general.sefcontext` registers `/data/vms` in SELinux policy as `virt_image_t`.
    3. `tasks/storage.yml` applies `virt_image_t` so SELinux in `Enforcing` mode does not block VM writes.
    4. `templates/storage_pool.xml.j2` renders `<path>/data/vms</path>`.
    5. `community.libvirt.virt_pool` defines and activates the pool in Libvirt.

---

### Q11: Does the directory live inside the storage pool, or does the storage pool live inside the directory?

- **A**:
  - **The directory is the physical home of the storage pool.**
  - In Libvirt, a **Storage Pool** is a logical label/pointer (e.g. named `default`).
  - In Linux, the **Directory** is the actual folder on the hard drive (e.g. `/var/lib/libvirt/images` or `/data/vms`).
  - Inside that directory live the actual virtual machine hard drive files (`vm1.qcow2`, `database.qcow2`).
  - When you run `virt-install --disk pool=default,size=20`, Libvirt looks up the pool `default`, sees its target path, and creates the `.qcow2` file inside that physical directory.

---

### Q12: What packages technically can a user add under `kvm_extra_packages` on KVM?

- **A**: Real-world enterprise packages and their justifications:
  1. **`libguestfs-tools` / `guestfs-tools`**: Offline disk image manipulation utilities (`virt-customize` to inject SSH keys into images, `virt-sysprep` to reset golden templates, and `guestfish`).
  2. **`virt-top`**: Terminal monitoring tool that displays real-time CPU %, RAM usage, and disk I/O per virtual machine domain (like `top` for VMs).
  3. **`qemu-img`**: Standalone disk utility to convert formats (`vmdk` to `qcow2`), resize virtual drives, and inspect snapshots.
  4. **`libosinfo`**: Operating system database used by `virt-install` to auto-tune virtual hardware for Windows, Ubuntu, Debian, etc.
  5. **`numactl`**: NUMA tuning utility to pin virtual CPUs and RAM to specific physical CPU sockets, eliminating cross-bus latency for high-performance databases.
  6. **`bridge-utils`**: Traditional Layer-2 Linux bridge inspection tool (`brctl show`, `brctl showmacs`).
  7. **`tuned`**: System tuning daemon to apply the `virtual-host` kernel profile (`tuned-adm profile virtual-host`).

---

### Q13: What does Level 3 nested virtualization mean, and why is `options kvm_intel nested=1` inside the VM redundant on Proxmox?

- **A**:
  - **Virtualization Levels**:
    - Level 0 (Bare Metal): Proxmox VE server in the company datacenter.
    - Level 1 (Target VM): Our RHEL hypervisor node running inside Proxmox.
    - Level 2 (Inner Guest VMs): Workload servers created on our RHEL node (web servers, databases).
    - Level 3 (Nested Hypervisor inside Inner VM): Installing KVM _again_ inside Level 2!
  - **Why `nested=1` in our VM is redundant**: Proxmox (L0) already enabled nested virtualization by setting the VM CPU model to `host`. Our RHEL node (L1) creates standard workload VMs (L2). Because L2 VMs do not act as hypervisors, enabling nested virtualization passthrough inside our VM is redundant and adds unneeded complexity.

---

### Q14: Why is the dedicated bridge approach (`kvm_br0` / `virbr1`) beneficial over default NAT?

- **A**:
  1. **Inter-VM Layer 2 Switching**: Guest VMs attached to `kvm_br0` communicate directly with each other at kernel memory speeds without traversing external routers, ideal for multi-tier applications and clustering.
  2. **VM-to-Host Gateway**: The KVM host interface at `192.168.100.1` acts as a direct management gateway and router for all guest VMs.
  3. **Zero Remote Connection Risk**: Using a dedicated virtual bridge device (`virbr1`) avoids modifying the host's physical network adapter (`enp1s0`), preventing accidental SSH disconnects during automation runs.
  4. **Proxmox Compatibility**: Avoids needing Promiscuous Mode or MAC Spoofing configured on Proxmox virtual network interfaces.

---

### Q15: Why should `vars/main.yml` NOT be renamed to `kvm.yml`?

- **A**:
  - In Ansible's role engine, **`vars/main.yml` is automatically loaded into memory** the moment the role starts.
  - If renamed to `vars/kvm.yml`, Ansible will ignore it. Any task referencing `{{ kvm_packages }}` will fail with an undefined variable error unless an explicit `ansible.builtin.include_vars: kvm.yml` task is added to `tasks/main.yml`.
  - Keeping it named `vars/main.yml` conforms to standard Ansible role architecture.

---

### Q16: What is the core fundamental behavior of Ansible in simple terms?

- **A**:
  1. **Declarative**: You define the _Goal_ (`state: present`, `state: started`), not the manual steps.
  2. **Idempotent**: Safe to run 100 times; changes only what does not match the goal (`changed=0` on clean state).
  3. **Top-to-Bottom Sequential Execution**: Executes tasks in strict order; stops immediately on error to prevent partial breakage.
  4. **Agentless**: Operates purely over SSH and temporary Python scripts; leaves no agent installed on target machines.
  5. **Auto-Loading**: Files named `main.yml` in `tasks/`, `defaults/`, `vars/`, and `handlers/` are loaded automatically.
