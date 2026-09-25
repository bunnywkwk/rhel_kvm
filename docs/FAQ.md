# rhel_kvm: FAQ

Short answers about lines in the role's tasks that raise questions.

---

## 1. What does `failed_when: false` do in the first task?

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

**In one line:** it tells Ansible "never mark this task as failed, even if a unit gives an error".

**Why it is here:** the list of units to disable is written once per OS (on RHEL 10 it holds the four legacy `libvirtd` units; on RHEL 9 it is empty). A unit may be missing on a host, or already stopped. Without `failed_when: false`, one such unit could stop the whole run. With it, the loop goes through every unit and moves on.

**What it does not do:** it does not make the task succeed. It only stops a problem from ending the run.

**The catch:** it also hides *real* problems on these units, so it is only used here, on a task whose failure does not matter.

---

## 2. What does `default([])` do in the loop?

```yaml
loop: "{{ rhel_kvm_disabled_services | default([]) }}"
```

**In one line:** "if `rhel_kvm_disabled_services` does not exist, use an empty list."

**Why it matters:** a `loop` over a variable that does not exist gives an error. `default([])` turns "does not exist" into "nothing to loop over", so the task does nothing instead of failing. `[]` is an empty list.

**In this role it never triggers.** Both `vars/RedHat-9.yml` and `vars/RedHat-10.yml` define the variable, so it always exists. It is a safety guard, not something the role needs today. You can say that honestly if asked. Removing it would be safe.

---

## 3. What do `is defined` and `| length > 0` do in a `when:`?

```yaml
# packages.yml: skip the task when the list is empty
when:
  - rhel_kvm_extra_packages | length > 0

# storage.yml: only when SELinux information exists on the host
when:
  - ansible_facts['selinux'] is defined
  - ansible_facts['selinux']['status'] == 'enabled'
```

| Check | Question it asks |
| :--- | :--- |
| `\| length > 0` | Does the list have at least one item? (not empty) |
| `is defined` | Does this value exist at all? |

**`| length > 0`** is used on our own list variables. In `defaults/main.yml` the list starts as `[]` (an empty list):
```yaml
rhel_kvm_extra_packages: []
```
The variable always exists, so the only question is whether anything was put in it. If the list is empty, the task is skipped.

**`is defined`** is used on *facts*, values Ansible reads from the host, not from our files:
```yaml
ansible_facts['selinux'] is defined                # storage.yml
ansible_facts['all_ipv6_addresses'] is defined     # sysctl.yml
```
A host may not report these (for example no IPv6, or no SELinux information). Asking a missing fact for `['status']` or `| length` would raise an error, so `is defined` comes first. When it is false, Ansible stops there and does not evaluate the rest.

---

## 4. Does enabling a service also enable its sockets?

**Yes.** Both libvirt designs are built that way, which is why `rhel_kvm_sockets` is short.

`libvirtd.service` (RHEL 9) and `virtqemud.service` (RHEL 10) say this in their unit files:

| Line in the unit file | What it does |
| :--- | :--- |
| `[Install] Also=libvirtd.socket libvirtd-ro.socket libvirtd-admin.socket` | `systemctl enable libvirtd.service` also enables these sockets |
| `Wants=` / `After=` (and `BindsTo=` on the modular services) | starting the service starts the sockets first |

`virtqemud.service` has the same with `virtqemud.socket`, `-ro` and `-admin`. The `-ro` and `-admin` sockets follow the main socket the same way (`Also=`).

So the role only lists sockets where it has to:
- RHEL 9: nothing. `libvirtd.service` brings all its sockets.
- RHEL 10: the sockets of every driver are listed. The OS does not enable them on a fresh host (the first run reports them as changed), and only `virtqemud.service` is started explicitly.

Check on a host:
```bash
systemctl cat libvirtd.service | grep -E '^(Wants|Requires|Also)='     # RHEL 9
systemctl cat virtqemud.service | grep -E '^(Wants|BindsTo|Also)='     # RHEL 10
```
(These lines were read from a Fedora 44 libvirt package. Run the command on the RHEL hosts to confirm.)


---

## 5. How do the storage pools work? Can I add more?

**The role creates the pools listed in `rhel_kvm_storage_pools`.** Its starting value is one pool:
```yaml
rhel_kvm_storage_pools:
  - name: default
    path: /var/lib/libvirt/images
```
Each `-` is one pool, and a pool is just a name and a folder:

| Line | Meaning |
| :--- | :--- |
| `name` | the pool's name inside libvirt (what `virsh pool-list` shows) |
| `path` | the folder on the host where the VM disks are stored |

Every pool is a plain folder and starts automatically at boot.

**What the role does for each listed pool:** create the folder (root only, mode `0711`) with the SELinux label QEMU needs, record the label in the SELinux policy, define the pool in libvirt, start it, and set autostart. All the storage tasks loop over the list, so a second pool gets the same steps as the first.

### Several pools, or not using `default`
Set the list in `sysconfig/group_vars/kvm_hosts.yml`:
```yaml
rhel_kvm_storage_pools:
  - name: default
    path: /var/lib/libvirt/images
  - name: vmdata
    path: /data/vms
```
- **Setting the list replaces the starting value.** Write `default` again if you want it created too. Leave it out, and only your pools are created.
- **The role never removes or changes pools it is not told about.** If a `default` pool already exists on the host and is not in your list, it stays exactly as it is.
- If a pool in the list already exists, its define step reports `ok` and changes nothing.

### Notes
- The role creates the folder but does not mount a disk. If `/data/vms` is meant to be a separate disk, mount it first, or the images end up on the root filesystem.
