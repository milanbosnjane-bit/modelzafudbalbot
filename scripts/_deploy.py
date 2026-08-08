"""Deploy bot ZIP + modeli + baza na remote server via SFTP."""
import os
import sys
import paramiko

HOST = "192.168.1.109"
USER = "adminq"
KEY  = r"C:\Users\Miki\.ssh\id_lan_109"
REMOTE_DIR = "/home/adminq/botposlednji"

LOCAL_FILES = [
    (r"C:\Users\Miki\Desktop\bot_deploy.zip",                   f"{REMOTE_DIR}/bot_deploy.zip"),
    (r"C:\Users\Miki\Desktop\botposlednji1\data\football_roi.db", f"{REMOTE_DIR}/football_roi.db"),
]

MODEL_DIR_LOCAL = r"C:\Users\Miki\Desktop\botposlednji1\data\models"
MODEL_DIR_REMOTE = f"{REMOTE_DIR}/models"


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, key_filename=KEY, timeout=30)
    return client


def run(client, cmd):
    _, stdout, stderr = client.exec_command(cmd)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    if out:
        print(out)
    if err:
        print(f"[ERR] {err}")


def upload(sftp, local, remote, label=""):
    size = os.path.getsize(local)
    print(f"  Saljemo {label or local} ({size//1024} KB)...", end=" ", flush=True)
    sftp.put(local, remote)
    print("OK")


def main():
    client = connect()
    sftp = client.open_sftp()

    # Kreiraj model folder
    run(client, f"mkdir -p {MODEL_DIR_REMOTE}")

    # Pošalji ZIP i bazu
    for local, remote in LOCAL_FILES:
        if os.path.exists(local):
            upload(sftp, local, remote, os.path.basename(local))
        else:
            print(f"  [SKIP] {local} ne postoji")

    # Pošalji modele
    if os.path.isdir(MODEL_DIR_LOCAL):
        for fname in os.listdir(MODEL_DIR_LOCAL):
            local = os.path.join(MODEL_DIR_LOCAL, fname)
            remote = f"{MODEL_DIR_REMOTE}/{fname}"
            if os.path.isfile(local):
                upload(sftp, local, remote, fname)

    sftp.close()

    # Raspakuj ZIP na serveru
    print("\nRaspakivanje na serveru...")
    run(client, f"cd {REMOTE_DIR} && unzip -o bot_deploy.zip && rm bot_deploy.zip")

    # Premesti bazu i modele
    run(client, f"mkdir -p {REMOTE_DIR}/data && mv {REMOTE_DIR}/football_roi.db {REMOTE_DIR}/data/ && mv {REMOTE_DIR}/models {REMOTE_DIR}/data/models 2>/dev/null || true")

    # Provjeri šta ima
    print("\n=== Sadrzaj na serveru ===")
    run(client, f"ls -la {REMOTE_DIR}/ && echo '---' && ls {REMOTE_DIR}/data/ 2>/dev/null")

    client.close()
    print("\nTransfer kompletiran.")


if __name__ == "__main__":
    main()
