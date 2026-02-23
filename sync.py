#!/usr/bin/env python3
"""
ibroadcast-drive-sync
Surveille un dossier Google Drive et synchronise avec iBroadcast.

Usage:
  sync.py              # daemon (boucle infinie)
  sync.py --once       # une passe et quitte
  sync.py --dry-run    # simule sans uploader ni trasher
  sync.py --setup      # assistant de configuration initiale
"""

import io
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ── Constantes ───────────────────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / ".config" / "ib-drive-sync"
CONFIG_FILE = CONFIG_DIR / "config.json"
STATE_DB    = CONFIG_DIR / "state.db"

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".aac", ".opus", ".wma"}

IB_LOGIN  = "https://login.ibroadcast.com/"
IB_API    = "https://api.ibroadcast.com/"
IB_SYNC   = "https://sync.ibroadcast.com"
IB_UPLOAD = "https://upload.ibroadcast.com"

CLIENT  = "ib-drive-sync"
VERSION = "1.0"

log = logging.getLogger("ib-drive-sync")

# ── iBroadcast ────────────────────────────────────────────────────────────────

def ib_login(email: str, password: str) -> tuple[str, str]:
    """Login iBroadcast via le formulaire web, retourne (user_id, token)."""
    session = requests.Session()
    session.post(IB_LOGIN,
        data={"email": email, "password": password},
        headers={"User-Agent": CLIENT},
        allow_redirects=True,
    )
    user_id = session.cookies.get("user_id")
    token   = session.cookies.get("token")
    if not user_id or not token:
        raise RuntimeError("iBroadcast login failed: cookies user_id/token absents")
    return user_id, token


def ib_get_md5s(user_id: str, token: str) -> set[str]:
    """Retourne l'ensemble des MD5 déjà présents sur iBroadcast."""
    resp = requests.post(IB_SYNC,
        data=f"user_id={user_id}&token={token}",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": CLIENT},
    )
    resp.raise_for_status()
    return set(resp.json().get("md5", []))


def ib_upload(user_id: str, token: str, filepath: Path, label: str) -> str | None:
    """Upload un fichier sur iBroadcast. Retourne le track_id ou None."""
    with open(filepath, "rb") as fh:
        resp = requests.post(IB_UPLOAD,
            data={"client": CLIENT, "version": VERSION,
                  "file_path": str(filepath), "method": CLIENT,
                  "user_id": user_id, "token": token},
            headers={"User-Agent": CLIENT},
            files={"file": fh},
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("result"):
        raise RuntimeError(f"Upload failed: {data}")
    m = re.search(r"\((\d+)\) uploaded successfully", data.get("message", ""))
    return m.group(1) if m else None


def ib_trash(user_id: str, token: str, track_ids: list[str]):
    """Déplace des pistes dans la corbeille iBroadcast."""
    resp = requests.post(IB_API,
        data=json.dumps({
            "mode": "trash", "tracks": track_ids,
            "user_id": user_id, "token": token,
            "client": CLIENT, "version": VERSION,
        }),
        headers={"Content-Type": "application/json"},
    )
    resp.raise_for_status()
    if not resp.json().get("result"):
        raise RuntimeError(f"Trash failed: {resp.json()}")


# ── Google Drive ──────────────────────────────────────────────────────────────

def get_drive_service(token_file: Path):
    with open(token_file) as f:
        d = json.load(f)
    creds = Credentials(
        token=d["token"],
        refresh_token=d["refresh_token"],
        token_uri=d["token_uri"],
        client_id=d["client_id"],
        client_secret=d["client_secret"],
    )
    if creds.expired or not creds.valid:
        creds.refresh(Request())
        d["token"] = creds.token
        with open(token_file, "w") as f:
            json.dump(d, f)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def list_drive_audio(service, folder_id: str) -> list[dict]:
    """Liste récursivement tous les fichiers audio dans un dossier Drive."""
    results = []

    def _recurse(fid: str):
        page_token = None
        while True:
            resp = service.files().list(
                q=f"'{fid}' in parents and trashed=false",
                fields="nextPageToken,files(id,name,md5Checksum,mimeType,size,modifiedTime)",
                pageToken=page_token,
                pageSize=1000,
            ).execute()
            for f in resp.get("files", []):
                if f["mimeType"] == "application/vnd.google-apps.folder":
                    _recurse(f["id"])
                elif Path(f["name"]).suffix.lower() in AUDIO_EXTS:
                    results.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    _recurse(folder_id)
    return results


def download_drive_file(service, file_id: str, dest: Path):
    request = service.files().get_media(fileId=file_id)
    with open(dest, "wb") as fh:
        dl = MediaIoBaseDownload(fh, request, chunksize=10 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()


# ── State DB ──────────────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS synced (
            drive_file_id TEXT PRIMARY KEY,
            drive_md5     TEXT,
            drive_name    TEXT,
            ib_track_id   TEXT,
            synced_at     INTEGER
        )
    """)
    conn.commit()
    return conn


# ── Sync ──────────────────────────────────────────────────────────────────────

def sync(config: dict, dry_run: bool = False):
    db = init_db()

    # Auth iBroadcast
    log.info("Login iBroadcast...")
    user_id, token = ib_login(config["ib_email"], config["ib_password"])

    # Drive
    log.info("Connexion Drive...")
    drive = get_drive_service(Path(config["drive_token_file"]))

    # Lister les fichiers Drive
    log.info(f"Scan Drive {config['drive_folder_id']}...")
    drive_files = list_drive_audio(drive, config["drive_folder_id"])
    log.info(f"{len(drive_files)} fichiers audio dans Drive")

    # MD5s iBroadcast
    log.info("Récupération MD5 iBroadcast...")
    ib_md5s = ib_get_md5s(user_id, token)
    log.info(f"{len(ib_md5s)} fichiers déjà sur iBroadcast")

    # Fichiers à uploader (pas encore sur iBroadcast selon MD5)
    to_upload = [f for f in drive_files if f.get("md5Checksum") not in ib_md5s]
    log.info(f"À uploader : {len(to_upload)}")

    uploaded = errors = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, f in enumerate(to_upload, 1):
            name = f["name"]
            log.info(f"[{i}/{len(to_upload)}] {name}")
            if dry_run:
                log.info("  (dry-run, skip)")
                continue
            dest = Path(tmpdir) / name
            try:
                download_drive_file(drive, f["id"], dest)
                track_id = ib_upload(user_id, token, dest, label=name)
                db.execute(
                    "INSERT OR REPLACE INTO synced VALUES (?,?,?,?,?)",
                    (f["id"], f.get("md5Checksum"), name, track_id, int(time.time()))
                )
                db.commit()
                uploaded += 1
                log.info(f"  → track_id={track_id}")
            except Exception as e:
                errors += 1
                log.error(f"  → ERREUR: {e}")

    # Suppression automatique (si activée)
    if config.get("auto_trash"):
        current_ids = {f["id"] for f in drive_files}
        to_trash = [
            (fid, tid)
            for fid, tid in db.execute("SELECT drive_file_id, ib_track_id FROM synced WHERE ib_track_id IS NOT NULL")
            if fid not in current_ids
        ]
        if to_trash:
            log.info(f"Trash {len(to_trash)} pistes supprimées du Drive...")
            if not dry_run:
                ib_trash(user_id, token, [tid for _, tid in to_trash])
                for fid, _ in to_trash:
                    db.execute("DELETE FROM synced WHERE drive_file_id=?", (fid,))
                db.commit()

    log.info(f"Terminé — uploadé: {uploaded}, erreurs: {errors}")
    return uploaded, errors


# ── Setup wizard ──────────────────────────────────────────────────────────────

def setup():
    print("\n=== Configuration ib-drive-sync ===\n")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    config = {}
    config["ib_email"]    = input("Email iBroadcast : ").strip()
    config["ib_password"] = input("Mot de passe iBroadcast : ").strip()

    default_drive_token = str(Path.home() / ".config/google/drive_token.json")
    config["drive_token_file"] = input(f"Token Drive [{default_drive_token}] : ").strip() or default_drive_token

    default_folder = "1F1pqBUiANHYzdpn0AEjvaMvbr_6wElQ0"
    config["drive_folder_id"] = input(f"ID dossier Drive [{default_folder}] : ").strip() or default_folder

    config["auto_trash"]    = input("Supprimer dans iBroadcast si effacé du Drive ? [o/N] : ").lower() == "o"
    config["poll_interval"] = int(input("Intervalle de sync en secondes [300] : ").strip() or 300)

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    print(f"\nConfig sauvée dans {CONFIG_FILE}")

    print("\nTest de connexion iBroadcast...")
    try:
        user_id, token = ib_login(config["ib_email"], config["ib_password"])
        md5s = ib_get_md5s(user_id, token)
        print(f"OK — {len(md5s)} fichiers sur iBroadcast")
    except Exception as e:
        print(f"ERREUR : {e}")
        return

    print("\nTest Drive...")
    try:
        drive = get_drive_service(Path(config["drive_token_file"]))
        files = list_drive_audio(drive, config["drive_folder_id"])
        print(f"OK — {len(files)} fichiers audio dans Drive")
    except Exception as e:
        print(f"ERREUR : {e}")
        return

    print("\nConfiguration OK. Lancer avec : python3 sync.py --once")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if "--setup" in sys.argv:
        setup()
        return

    if not CONFIG_FILE.exists():
        print(f"Pas de config. Lancer : python3 sync.py --setup")
        sys.exit(1)

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    dry_run = "--dry-run" in sys.argv

    if "--once" in sys.argv or dry_run:
        sync(config, dry_run=dry_run)
        return

    # Daemon
    interval = config.get("poll_interval", 300)
    log.info(f"Daemon démarré, intervalle={interval}s")
    while True:
        try:
            sync(config)
        except Exception as e:
            log.error(f"Erreur sync: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
