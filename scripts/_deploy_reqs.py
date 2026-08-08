"""Upload requirements_server.txt + updated neural_model.py, then pip install."""
import sys
import paramiko

HOST = "192.168.1.109"
USER = "adminq"
KEY  = r"C:\Users\Miki\.ssh\id_lan_109"
REMOTE_DIR = "/home/adminq/botposlednji"


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, key_filename=KEY, timeout=30)
    return client


def run(client, cmd):
    _, stdout, stderr = client.exec_command(cmd, timeout=600)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out:
        sys.stdout.buffer.write((out + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    if err:
        sys.stdout.buffer.write(f"[ERR] {err}\n".encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()


def main():
    client = connect()
    sftp = client.open_sftp()

    # Posalji requirements_server.txt
    print("Saljemo requirements_server.txt...")
    sftp.put(
        r"C:\Users\Miki\Desktop\botposlednji1\requirements_server.txt",
        f"{REMOTE_DIR}/requirements_server.txt"
    )

    # Posalji azurirani neural_model.py (sa try/except za torch)
    print("Saljemo neural_model.py (bez torch dependency)...")
    sftp.put(
        r"C:\Users\Miki\Desktop\botposlednji1\app\models\neural_model.py",
        f"{REMOTE_DIR}/app/models/neural_model.py"
    )

    sftp.close()
    print("\nInstaliramo pakete (--no-cache-dir, moze trajati 5-10 min)...")
    run(client, f"cd {REMOTE_DIR} && venv/bin/pip install --no-cache-dir -r requirements_server.txt 2>&1")
    print("\nProvera instalacije:")
    run(client, f"{REMOTE_DIR}/venv/bin/python -c \"import lightgbm, xgboost, telegram; print('OK')\"")
    run(client, f"df -h /")

    client.close()


if __name__ == "__main__":
    main()
