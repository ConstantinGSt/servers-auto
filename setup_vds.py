import paramiko
import os
import sys
import subprocess
from getpass import getpass

#conf
SERVER_IP = "ip"
ROOT_PASSWORD = "ROOT_PASSWORD"

ADMIN_USER = "username"
ADMIN_PASSWORD = "ADMIN_PASSWORD"

SSH_PUBLIC_KEY = "/home/username/.ssh/server_key.pub"
SSH_PORT = "ports"

# SSH ROOT
def connect_root():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    client.connect(
        SERVER_IP,
        port=22,
        username="root",
        password=ROOT_PASSWORD,
        look_for_keys=False,
        allow_agent=False,
        timeout=10
    )

    return client


def run_command(client, command):
    stdin, stdout, stderr = client.exec_command(command)

    output = stdout.read().decode()
    error = stderr.read().decode()

    exit_code = stdout.channel.recv_exit_status()

    if output:
        print(output, end="")

    if exit_code != 0:
        if error:
            print(error, end="")

        raise RuntimeError(f"Command failed: {command}")

    return output


# CHECK KEY
if not os.path.isfile(SSH_PUBLIC_KEY):
    print(f"ERROR: public key not found: {SSH_PUBLIC_KEY}")
    sys.exit(1)

with open(SSH_PUBLIC_KEY, "r") as f:
    public_key = f.read().strip()

if not public_key:
    print("ERROR: public key is empty")
    sys.exit(1)

# CONNECT ROOT
print(f"Connecting to {SERVER_IP} as root...")

root = connect_root()

print("Root connection: OK")

# CREATE USER
print("Creating admin user...")

run_command(
    root,
    f"id {ADMIN_USER} >/dev/null 2>&1 || "
    f"useradd -m -s /bin/bash {ADMIN_USER}"
)

run_command(
    root,
    f"echo '{ADMIN_USER}:{ADMIN_PASSWORD}' | chpasswd"
)

run_command(
    root,
    f"usermod -aG sudo {ADMIN_USER}"
)

print("Admin user: OK")

# INSTALL PUBLIC KEY
print("Installing SSH public key...")

run_command(
    root,
    f"mkdir -p /home/{ADMIN_USER}/.ssh"
)

run_command(
    root,
    f"chmod 700 /home/{ADMIN_USER}/.ssh"
)

escaped_key = public_key.replace("'", "'\\''")

run_command(
    root,
    f"echo '{escaped_key}' > "
    f"/home/{ADMIN_USER}/.ssh/authorized_keys"
)

run_command(
    root,
    f"chmod 600 /home/{ADMIN_USER}/.ssh/authorized_keys"
)

run_command(
    root,
    f"chown -R {ADMIN_USER}:{ADMIN_USER} "
    f"/home/{ADMIN_USER}/.ssh"
)

print("SSH key: OK")

# BACKUP SSH CONFIG
print("Backing up SSH configuration...")

run_command(
    root,
    "cp /etc/ssh/sshd_config.d/40-hosting.conf "
    "/etc/ssh/sshd_config.d/40-hosting.conf.backup"
)

print("SSH config backup: OK")

# SSH CONFIG
print("Configuring SSH...")

ssh_config = f"""Port {SSH_PORT}
PermitRootLogin yes
PubkeyAuthentication yes
PasswordAuthentication yes
PermitEmptyPasswords no
"""

command = (
    "cat > /etc/ssh/sshd_config.d/40-hosting.conf <<'EOF'\n"
    + ssh_config +
    "EOF"
)

run_command(root, command)

run_command(
    root,
    "sshd -t"
)

print("SSH configuration: OK")

# UFW
print("Configuring UFW...")

run_command(
    root,
    f"ufw allow {SSH_PORT}/tcp"
)

print(f"UFW: port {SSH_PORT}/tcp allowed")

# DISABLE IPV6
print("Disabling IPv6 in UFW...")

run_command(
    root,
    "sed -i 's/^IPV6=.*/IPV6=no/' /etc/default/ufw"
)

print("UFW IPv6: disabled")

# RELOAD SSH
print("Reloading SSH...")

run_command(
    root,
    "systemctl reload ssh"
)

print("SSH reloaded")

root.close()


# TEST ADMIN SSH
print("Testing admin SSH login...")

private_key = SSH_PUBLIC_KEY[:-4]

result = subprocess.run(
    [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes",
        "-i", private_key,
        "-p", str(SSH_PORT),
        f"{ADMIN_USER}@{SERVER_IP}",
        "exit"
    ]
)

if result.returncode != 0:
    print()
    print("ERROR: admin SSH login failed.")
    print("Root login was NOT disabled by this script.")
    sys.exit(1)

print("Admin SSH login: OK")

# TEST SUDO
print("Testing sudo...")

result = subprocess.run(
    [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes",
        "-i", private_key,
        "-p", str(SSH_PORT),
        f"{ADMIN_USER}@{SERVER_IP}",
        "sudo -n whoami"
    ],
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print(result.stderr)
    print()
    print("ERROR: sudo test failed.")
    print("Root login was NOT disabled by this script.")
    sys.exit(1)

if result.stdout.strip() != "root":
    print("ERROR: sudo did not return root.")
    sys.exit(1)

print("Sudo: OK")

# DISABLE ROOT + PASSWORD
print("Disabling root login and SSH password authentication...")

result = subprocess.run(
    [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes",
        "-i", private_key,
        "-p", str(SSH_PORT),
        f"{ADMIN_USER}@{SERVER_IP}",
        "sudo sed -i "
        "'s/^PermitRootLogin.*/PermitRootLogin no/' "
        "/etc/ssh/sshd_config.d/40-hosting.conf"
    ]
)

if result.returncode != 0:
    raise RuntimeError("Failed to disable root login")


result = subprocess.run(
    [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes",
        "-i", private_key,
        "-p", str(SSH_PORT),
        f"{ADMIN_USER}@{SERVER_IP}",
        "sudo sed -i "
        "'s/^PasswordAuthentication.*/PasswordAuthentication no/' "
        "/etc/ssh/sshd_config.d/40-hosting.conf"
    ]
)

if result.returncode != 0:
    raise RuntimeError("Failed to disable password authentication")


result = subprocess.run(
    [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes",
        "-i", private_key,
        "-p", str(SSH_PORT),
        f"{ADMIN_USER}@{SERVER_IP}",
        "sudo sshd -t && sudo systemctl reload ssh"
    ]
)

if result.returncode != 0:
    raise RuntimeError("Failed to reload SSH")


print("Root login: disabled")
print("SSH password authentication: disabled")

# REMOVE PORT 22

if SSH_PORT != 22:

    print("Removing old SSH port 22 from UFW...")

    subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "IdentitiesOnly=yes",
            "-i", private_key,
            "-p", str(SSH_PORT),
            f"{ADMIN_USER}@{SERVER_IP}",
            "sudo ufw delete allow 22/tcp"
        ]
    )

    print("UFW port 22: removed")

# FINAL TEST
print("Final SSH test...")

result = subprocess.run(
    [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "-o", "IdentitiesOnly=yes",
        "-i", private_key,
        "-p", str(SSH_PORT),
        f"{ADMIN_USER}@{SERVER_IP}",
        "whoami"
    ],
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print("ERROR: final SSH test failed.")
    sys.exit(1)

print(result.stdout, end="")

print()
print("==============================")
print("VDS INITIAL SETUP COMPLETE")
print("==============================")
print(f"Server: {SERVER_IP}")
print(f"Admin: {ADMIN_USER}")
print(f"SSH port: {SSH_PORT}")
print("Root SSH login: disabled")
print("SSH password authentication: disabled")
print("UFW IPv6: disabled")
