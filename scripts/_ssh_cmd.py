"""Izvrsava komandu na remote serveru via SSH."""
import sys
import paramiko

HOST = "192.168.1.109"
USER = "adminq"
KEY  = r"C:\Users\Miki\.ssh\id_lan_109"


def run(cmd: str) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, key_filename=KEY, timeout=15)
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    client.close()
    return out + (("\n[STDERR] " + err) if err.strip() else "")


if __name__ == "__main__":
    cmd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "whoami && hostname"
    print(run(cmd))
