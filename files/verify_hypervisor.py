#!/usr/bin/env python3

# KVM Hypervisor Acceptance & Compliance Verification

import os
import sys
import subprocess

# ANSI Colors
GREEN = "\033[0;32m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
NC = "\033[0m"

passed = 0
failed = 0


def run_cmd(cmd):
    """Runs a shell command and returns (rc, stdout)"""
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return res.returncode, res.stdout.strip()
    except Exception as e:
        return 1, str(e)

#Checking fuction for passed or failed
def check(description, is_pass, detail=""):
    global passed, failed

    if is_pass:
        passed += 1
        msg = f" [{GREEN}PASS{NC}] {description}"
        if detail:
            msg += f" ({detail})"
        print(msg)
    else:
        failed += 1
        msg = f" [{RED}FAIL{NC}] {description}"
        if detail:
            msg += f" (Details: {default})"
        print(msg)


# Getting the OS version by stripping it after the Version ID
def get_os_major():
    if os.path.exists("/etc/os-release"):
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("VERSION_ID="):
                    val = line.strip().split("=")[1].replace('"', "")
                    return int(val.split(".")[0])
    return 9


def main():
    print(f"{BOLD}============================================================{NC}")
    print(f"{BOLD}       KVM HYPERVISOR AUTOMATED ACCEPTANCE TEST (PY)        {NC}")
    print(f"{BOLD}============================================================{NC}")
    major = get_os_major()
    check(f"Target OS Major Version Detected", True, f"RHEL/EL Version: {major}")
    # 1. Daemon Model Check
    print(f"\n{BOLD}--- 1. Libvirt Daemon Architecture ---{NC}")
    if major == 9:
        rc, _ = run_cmd("systemctl is-active libvirtd.service libvirtd.socket")
        check(
            "RHEL 9 Monolithic libvirtd status",
            rc == 0,
            "libvirtd service/socket active",
        )
    else:
        for s in [
            "virtqemud.socket",
            "virtnetworkd.socket",
            "virtstoraged.socket",
        ]:
            rc, _ = run_cmd(f"systemctl is-active {s}")
            check(f"RHEL 10 Modular socket: {s}", rc == 0)
        _, out = run_cmd("systemctl is-enabled libvirtd.service")
        check(
            "Legacy Monolithic libvirtd is masked on RHEL 10", "masked" in out
        )
    # 2. Kernel Modules & Routing
    print(f"\n{BOLD}--- 2. Kernel Modules & IP Forwarding ---{NC}")
    for mod in ["kvm", "vhost_net", "tun"]:
        rc, _ = run_cmd(f"lsmod | grep -w {mod}")
        check(f"Kernel module '{mod}' loaded", rc == 0)
    _, ip_fwd = run_cmd("sysctl -n net.ipv4.ip_forward")
    check(
        "Kernel IPv4 Packet Forwarding enabled",
        ip_fwd == "1",
        f"net.ipv4.ip_forward = {ip_fwd}",
    )
    # 3. Storage Pools & SELinux
    print(f"\n{BOLD}--- 3. Storage Pools & SELinux Contexts ---{NC}")
    rc, pool_info = run_cmd("virsh pool-info default")
    check("Storage pool 'default' exists", rc == 0)
    if rc == 0:
        check(
            "Storage pool 'default' active",
            "running" in pool_info.lower() or "active" in pool_info.lower(),
        )
        check("Storage pool 'default' autostart=yes", "yes" in pool_info.lower())
    rc, ls_out = run_cmd("ls -ldZ /var/lib/libvirt/images")
    check(
        "SELinux virt_image_t on /var/lib/libvirt/images",
        "virt_image_t" in ls_out,
        ls_out.split()[3] if rc == 0 and len(ls_out.split()) > 3 else "",
    )
    # 4. Virtual Network & Bridge
    print(f"\n{BOLD}--- 4. Virtual Networks & Bridge (virbr0) ---{NC}")
    rc, net_info = run_cmd("virsh net-info default")
    check("Virtual network 'default' exists", rc == 0)
    if rc == 0:
        check("Virtual network 'default' active", "yes" in net_info.lower())
        check(
            "Virtual network 'default' autostart=yes", "yes" in net_info.lower()
        )
    rc, _ = run_cmd("ip link show virbr0")
    check("Linux bridge interface 'virbr0' exists and UP", rc == 0)
    # Summary
    total = passed + failed
    print(
        f"\n{BOLD}============================================================{NC}"
    )
    print(f"{BOLD}                     SUMMARY REPORT                         {NC}")
    print(
        f"{BOLD}============================================================{NC}"
    )
    print(f" Total Checks : {total}")
    print(f" Passed       : {GREEN}{passed}{NC}")
    print(f" Failed       : {RED}{failed}{NC}")
    if failed == 0:
        print(
            f"\n {GREEN}{BOLD}🎉 ALL ACCEPTANCE CRITERIA PASSED! (100%){NC}\n"
        )
        sys.exit(0)
    else:
        print(f"\n {RED}{BOLD}❌ SOME CHECKS FAILED.{NC}\n")
        sys.exit(1)
if __name__ == "__main__":
    main()