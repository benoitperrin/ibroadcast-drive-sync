#!/usr/bin/env python3
"""
embed-artwork.py — Embarque les enluminures du site psaume.retraitedanslaville.org
dans les MP3 des Psaumes dans la Ville, puis ré-uploade sur iBroadcast.

Étapes :
  1. Liste les 188 MP3 du dossier Drive, triés par numéro de piste
  2. Télécharge et met en cache les 23 enluminures uniques (upscalées 500x500)
  3. Pour chaque MP3 : download Drive → embed artwork → upload iBroadcast
  4. Trash les anciennes pistes (sans artwork) de l'album iBroadcast
"""

import io
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image
from mutagen.id3 import ID3, APIC
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Constantes ────────────────────────────────────────────────────────────────

DRIVE_FOLDER  = "1l449OcVapj8voJ8p4i87Gv4bLYfn402a"  # folder ID to override
DRIVE_TOKEN   = Path.home() / ".config/google/drive_token.json"
IB_ALBUM_ID   = "206662993"  # album ID to override

# Credentials: read from ib-drive-sync config or set via env IB_EMAIL / IB_PASSWORD
_IB_CONFIG    = Path.home() / ".config/ib-drive-sync/config.json"
IMG_CACHE     = Path("/tmp/psaumes-enluminures")
IMG_BASE_URL  = "https://staticpsaume.retraitedanslaville.org/var/images/enluminures/"
IMG_SIZE      = 500   # px (upscale depuis 180x173)

IB_LOGIN  = "https://login.ibroadcast.com/"
IB_API    = "https://api.ibroadcast.com/"
IB_SYNC   = "https://sync.ibroadcast.com"
IB_UPLOAD = "https://upload.ibroadcast.com"
CLIENT    = "ib-drive-sync"
VERSION   = "1.0"

# ── Mapping position → enluminure ─────────────────────────────────────────────
# 188 entrées dans l'ordre du site (= ordre des fichiers triés par n° de piste)
ENLUMINURES = [
    "irht083891",   #   1 Heureux l'homme
    "irht083895",   #   2 Tu es mon fils
    "irht083900",   #   3 Tu tiens haute ma tête
    "irht083907",   #   4 Que s'illumine ton visage
    "irht083911",   #   5 L'abri de mon allégresse
    "irht083915",   #   6 Seigneur, que fais-tu
    "irht083921",   #   7 Sur mes mains
    "irht099313",   #   8 Des enfants, des tout petits
    "irht099339",   #  9a L'espoir des malheureux
    "irht099349",   #  9b Justice rendue
    "irht099353",   #  10 Garder les yeux ouverts
    "irht099362-p", #  11 Tenir parole
    "irht099364",   #  12 La lumière pour mes yeux
    "irht099366",   #  13 Manger mon peuple
    "irht099369",   #  14 La tente de la parole
    "irht099386",   #  15 Toi mon refuge
    "irht099384",   #  16 Ton visage au réveil
    "irht099386",   #  17(1-30) Libéré et mis au large
    "irht099389",   #  17(31-51) Une agilité de chamois
    "irht099391",   #  18a L'ouvrage de tes mains
    "irht099395",   #  18b Le regard clair
    "irht099399",   #  19 À la mesure de ton cœur
    "irht099404",   #  20 Dresse-toi dans ta force
    "irht083891",   #  21 Rejeté par le peuple
    "irht083895",   #  22 Le seigneur est mon berger
    "irht083900",   #  23 Portes, levez vos frontons
    "irht083907",   #  24 Ton amour est de toujours
    "irht083911",   #  25 J'aime ta maison
    "irht083915",   #  26 Espère le Seigneur
    "irht083921",   #  27 Seigneur, mon rocher
    "irht099313",   #  28 Voix du Seigneur
    "irht099339",   #  29 Au matin, les cris de joie
    "irht099349",   #  30 Ma forteresse et mon roc
    "irht099353",   #  31 Tu as enlevé l'offense
    "irht099362-p", #  32 Criez de joie pour le Seigneur
    "irht099364",   #  33 Goûtez et voyez
    "irht099366",   #  34 Pour me défendre
    "irht099369",   #  35 En toi la source de vie
    "irht099377",   #  36(1-22) Laisse ta colère
    "irht099384",   #  36(23-40) Il n'abandonne pas ses amis
    "irht099386",   #  37 Corrige-moi sans colère
    "irht099389",   #  38 L'homme n'est qu'un souffle
    "irht099391",   #  39 Voici, je viens
    "irht099395",   #  40 Frappé du talon
    "irht099399",   #  41 Comme un cerf altéré
    "irht099404",   #  42 Ta lumière et ta vérité
    "irht083891",   #  43 Réveille-toi, Seigneur
    "irht083895",   #  44 Une onction de joie
    "irht083900",   #  45 Dieu, notre citadelle
    "irht083900",   #  46 Battre des mains
    "irht083907",   #  47 Dans la ville de notre Dieu
    "irht083911",   #  48 Racheter son frère
    "irht083915",   #  49 Le chemin d'action de grâce
    "irht083921",   #  50 Pitié pour moi, mon Dieu
    "irht099313",   #  51 Comme un bel olivier
    "irht099339",   #  52 Qui cherche Dieu
    "irht099349",   #  53 Mon appui entre tous
    "irht099353",   #  54 Un frisson me saisit
    "irht099362-p", #  55 Le jour où j'ai peur
    "irht099364",   #  56 À l'ombre de tes ailes
    "irht099366",   #  57 Venin de vipères
    "irht099366",   #  58 Au temps de ma détresse
    "irht099377",   #  59 Vin de vertige
    "irht099384",   #  60 Des terres lointaines
    "irht099386",   #  61 En Dieu, mon repos
    "irht099389",   #  62 Je te cherche dès l'aube
    "irht099391",   #  63 Les flèches de la langue
    "irht099395",   #  64 Tout exulte et chante
    "irht099399",   #  65 Il a gardé nos pieds de la chute
    "irht099404",   #  66 La terre a donné son fruit
    "irht083891",   #  67(1-19) Comme fond la cire
    "irht083895",   #  67(20-36) Montre ta force
    "irht083900",   #  68(1-13) Dans la vase du gouffre
    "irht083907",   #  68(14-37) Je suffoque
    "irht083911",   #  69 Je suis pauvre et malheureux
    "irht083915",   #  70 Aux jours des cheveux blancs
    "irht083921",   #  71 Tous les rois devant lui
    "irht099313",   #  72 Jaloux des superbes
    "irht099339",   #  73 Dieu, mon roi dès l'origine
    "irht099349",   #  74 Il abaisse et il relève
    "irht099353",   #  75 Mon âme refuse le réconfort
    "irht099362-p", #  76 Tes exploits, je médite
    "irht099364",   #  77(1-18) Tends l'oreille
    "irht099366",   #  77(19-42) Foi en Dieu
    "irht099377",   #  77(43-72) Comme un arc infidèle
    "irht099384",   #  78 Risée
    "irht099386",   #  79 Que ton visage s'éclaire
    "irht099389",   #  80 Les eaux de la discorde
    "irht099391",   #  81 Libérez le faible
    "irht099395",   #  82 Contre ton peuple
    "irht099399",   #  83 Un jour dans tes parvis
    "irht099399",   #  84 La joie de ton peuple
    "irht099404",   #  85 Toi que j'appelle
    "irht083891",   #  86 Naître
    "irht083895",   #  87 Au plus profond de la fosse
    "irht083900",   #  88(1-19) Qui est comme toi
    "irht083907",   #  88(20-38) Un trône pour David
    "irht083911",   #  88(39-53) Où donc, Seigneur
    "irht083915",   #  89 Nos jours s'enfuient
    "irht083921",   #  90 Sous son aile un refuge
    "irht099313",   #  91 Fougue du taureau
    "irht099339",   #  92 Vêtu de magnificence
    "irht099349",   #  93 Caché en Dieu-silence
    "irht099353",   #  94 Le cœur égaré
    "irht099362-p", #  95 La joie des arbres
    "irht099364",   #  96 Cœur simple
    "irht099366",   #  97 Sonnez, chantez, jouez
    "irht099369",   #  98 Au pied de son trône
    "irht099377",   #  99 L'allégresse chantante
    "irht099384",   # 100 Cœur tortueux et ambitieux
    "irht099386",   # 101 D'âge en âge
    "irht099391",   # 102 Bénis le Seigneur
    "irht099395",   # 103(1-23) Aux ânes et aux marmottes
    "irht099399",   # 103(24-35) Profusion dans tes œuvres
    "irht099404",   # 104(1-22) Une poignée d'immigrants
    "irht083891",   # 104(23-45) Plus puissant que ses adversaires
    "irht083895",   # 105(1-22) Une poignée d'immigrants
    "irht083900",   # 105(24-48) S'enfoncer dans sa faute
    "irht083907",   # 106(1-22) Sur des chemins perdus
    "irht083911",   # 106(1-3;23-43) Chaos sans chemin
    "irht083915",   # 107 Ton amour plus grand que les cieux
    "irht083921",   # 108(1-15) Propos haineux
    "irht099313",   # 108(16-31) Mes accusateurs
    "irht099339",   # 109 Prince éblouissant
    "irht099349",   # 110 Mémoire de son alliance
    "irht099353",   # 111 Lumière des cœurs droits
    "irht099362-p", # 112 Du levant au couchant du soleil
    "irht099364",   # 113a Comme des béliers bondissants
    "irht099366",   # 113b Il bénira
    "irht099369",   # 114 Sur la terre des vivants
    "irht099377",   # 115 Je crois et je parlerai
    "irht099384",   # 116 Tous les peuples
    "irht099386",   # 117(1-18) Voici le jour
    "irht099389",   # 117(19-29) La Pierre rejetée
    "irht099391",   # 118 aleph — Observer entièrement
    "irht099395",   # 118 beth — En tes commandements, mon plaisir
    "irht099399",   # 118 ghimel — Brûlé de Désir
    "irht099404",   # 118 daleth — Au large, mon cœur
    "irht083891",   # 118 hé — Incline mon cœur
    "irht083895",   # 118 waw — Librement
    "irht083900",   # 118 zain — Ma consolation
    "irht083907",   # 118 heth — Au milieu de la nuit
    "irht083911",   # 118 thet — Plus qu'un monceau d'or
    "irht083915",   # 118 yod — Pour consolation, ton amour
    "irht083921",   # 118 caph — Comme une outre durcie
    "irht099313",   # 118 lamed — Pour toujours
    "irht099339",   # 118 men — Tout le jour, je médite
    "irht099349",   # 118 nun — Exposer ma vie
    "irht099353",   # 118 samech — Ne déçois pas mon attente
    "irht099362-p", # 118 ain — Apprends-moi
    "irht099364",   # 118 phé — Pour qui aime ton nom
    "irht099366",   # 118 çade — Justice éternelle
    "irht099369",   # 118 qoph — Tu es proche
    "irht099377",   # 118 resh — Soutiens notre cause
    "irht099384",   # 118 shin — Un grand butin
    "irht099386",   # 118 taw — Que mon âme te loue
    "irht099389",   # 119 Vivre en exil
    "irht099391",   # 120 Le Seigneur, ton ombrage
    "irht099395",   # 121 Paix sur toi
    "irht099399",   # 122 Les yeux levés vers Toi
    "irht099404",   # 123 Filet rompu
    "irht083891",   # 124 Pour l'homme au cœur droit
    "irht083895",   # 125 Le Seigneur ramène les captifs
    "irht083900",   # 126 Quand tu dors
    "irht083907",   # 127 Tu verras le bonheur
    "irht083911",   # 128 Sur mon dos, des laboureurs
    "irht083915",   # 129 Des profondeurs, je crie
    "irht083921",   # 130 Comme un enfant
    "irht099313",   # 131 Le repos de Dieu
    "irht099339",   # 132 Comme un parfum sur la tête
    "irht099339",   # 133 Au long des nuits
    "irht099349",   # 134 Souffle(s)
    "irht099353",   # 135 Car éternel est son amour
    "irht099362-p", # 136 Au sommet de ma joie
    "irht099362-p", # 137 En présence des anges
    "irht099364",   # 138 Tu me scrutes
    "irht099366",   # 139 Contre l'homme violent
    "irht099369",   # 140 Garde-moi du filet
    "irht099377",   # 141 Piège tendu
    "irht099377",   # 142 Au matin ton amour
    "irht099384",   # 143 Une ombre qui passe
    "irht099386",   # 144 La bonté pour tous
    "irht099389",   # 145 Espoir dans le Seigneur
    "irht099391",   # 146 L'action de grâce
    "irht099395",   # 147 Dégel de printemps
    "irht099399",   # 148 Sous une loi
    "irht099404",   # 149 Aux humbles, l'éclat
    "irht083891",   # 150 Chante louange au Seigneur
]

assert len(ENLUMINURES) == 188, f"Expected 188 entries, got {len(ENLUMINURES)}"


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_drive():
    with open(DRIVE_TOKEN) as f:
        d = json.load(f)
    creds = Credentials(token=d["token"], refresh_token=d["refresh_token"],
        token_uri=d["token_uri"], client_id=d["client_id"], client_secret=d["client_secret"])
    if creds.expired or not creds.valid:
        creds.refresh(Request())
        d["token"] = creds.token
        with open(DRIVE_TOKEN, "w") as f:
            json.dump(d, f)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def ib_login():
    import os
    if _IB_CONFIG.exists():
        cfg = json.loads(_IB_CONFIG.read_text())
        email, password = cfg["ib_email"], cfg["ib_password"]
    else:
        email    = os.environ["IB_EMAIL"]
        password = os.environ["IB_PASSWORD"]
    s = requests.Session()
    s.post(IB_LOGIN, data={"email": email, "password": password},
           headers={"User-Agent": CLIENT}, allow_redirects=True)
    user_id = s.cookies.get("user_id")
    token   = s.cookies.get("token")
    if not user_id or not token:
        raise RuntimeError("iBroadcast login failed")
    return user_id, token


def get_album_track_ids(user_id, token):
    resp = requests.post("https://library.ibroadcast.com",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"mode": "library", "user_id": user_id, "token": token,
                         "client": CLIENT, "version": VERSION,
                         "device_name": "embed-artwork", "user_agent": CLIENT}))
    lib = resp.json()["library"]
    albums = lib["albums"]
    album = albums.get(IB_ALBUM_ID)
    if not album:
        return []
    # album[1] = liste des track_ids
    return [str(t) for t in album[1]]


def get_ib_md5s(user_id, token):
    resp = requests.post(IB_SYNC,
        data=f"user_id={user_id}&token={token}",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": CLIENT})
    resp.raise_for_status()
    return set(resp.json().get("md5", []))


def fetch_enluminure(irht_id: str) -> bytes:
    """Télécharge et upscale une enluminure. Cache dans /tmp."""
    IMG_CACHE.mkdir(exist_ok=True)
    cache_path = IMG_CACHE / f"{irht_id}.jpg"
    if cache_path.exists():
        return cache_path.read_bytes()

    url = f"{IMG_BASE_URL}home_{irht_id}.png"
    r = requests.get(url, timeout=10)
    r.raise_for_status()

    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    # Upscale à IMG_SIZE px (côté le plus grand)
    img = img.resize((IMG_SIZE, IMG_SIZE), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    cache_path.write_bytes(data)
    return data


def embed_artwork(mp3_path: Path, jpeg_data: bytes):
    tags = ID3(str(mp3_path))
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=jpeg_data))
    tags.save(str(mp3_path))


def ib_upload_file(user_id, token, mp3_path: Path, filename: str) -> str | None:
    with open(mp3_path, "rb") as fh:
        resp = requests.post(IB_UPLOAD,
            data={"client": CLIENT, "version": VERSION,
                  "file_path": str(mp3_path), "method": CLIENT,
                  "user_id": user_id, "token": token},
            headers={"User-Agent": CLIENT},
            files={"file": (filename, fh, "audio/mpeg")},
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("result"):
        raise RuntimeError(f"Upload failed: {data}")
    m = re.search(r"\((\d+)\) uploaded successfully", data.get("message", ""))
    return m.group(1) if m else None


def ib_trash(user_id, token, track_ids: list):
    if not track_ids:
        return
    # Trash par lots de 50
    for i in range(0, len(track_ids), 50):
        batch = track_ids[i:i+50]
        resp = requests.post(IB_API,
            headers={"Content-Type": "application/json"},
            data=json.dumps({"mode": "trash", "tracks": batch,
                             "user_id": user_id, "token": token,
                             "client": CLIENT, "version": VERSION}))
        resp.raise_for_status()
        log.info(f"  Trashé {len(batch)} pistes")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv

    log.info("Connexion Drive...")
    drive = get_drive()

    log.info("Listing des MP3 dans Drive...")
    all_files = []
    page_token = None
    while True:
        resp = drive.files().list(
            q=f"'{DRIVE_FOLDER}' in parents and trashed=false",
            fields="nextPageToken,files(id,name,md5Checksum)",
            pageSize=200, pageToken=page_token
        ).execute()
        all_files.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    mp3_files = [f for f in all_files if f["name"].endswith(".mp3")]

    # Trier par numéro de piste (nombre en début de filename)
    def track_num(f):
        m = re.match(r"^(\d+)", f["name"])
        return int(m.group(1)) if m else 999

    mp3_files.sort(key=track_num)
    log.info(f"{len(mp3_files)} MP3 trouvés")

    if len(mp3_files) != 188:
        log.warning(f"Attendu 188 fichiers, trouvé {len(mp3_files)} — le mapping peut être décalé")

    log.info("Login iBroadcast...")
    user_id, token = ib_login()

    log.info("Récupération des pistes existantes (pour trash final)...")
    old_track_ids = get_album_track_ids(user_id, token)
    log.info(f"  {len(old_track_ids)} pistes existantes dans l'album")

    log.info("MD5s iBroadcast...")
    ib_md5s = get_ib_md5s(user_id, token)
    log.info(f"  {len(ib_md5s)} fichiers sur iBroadcast")

    # Pré-télécharger toutes les enluminures uniques
    log.info("Téléchargement des enluminures...")
    unique_irht = set(ENLUMINURES)
    img_cache = {}
    for irht_id in sorted(unique_irht):
        img_cache[irht_id] = fetch_enluminure(irht_id)
        log.info(f"  {irht_id}: {len(img_cache[irht_id])} bytes")

    # Traitement des fichiers
    uploaded = errors = skipped = 0
    new_track_ids = []

    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, (drive_file, irht_id) in enumerate(zip(mp3_files, ENLUMINURES)):
            name    = drive_file["name"]
            file_id = drive_file["id"]
            pos     = i + 1
            log.info(f"[{pos}/188] {name} → {irht_id}")

            if dry_run:
                log.info("  (dry-run)")
                continue

            mp3_path = Path(tmpdir) / name
            try:
                # Download
                req  = drive.files().get_media(fileId=file_id)
                buf  = io.BytesIO()
                dl   = MediaIoBaseDownload(buf, req, chunksize=10*1024*1024)
                done = False
                while not done:
                    _, done = dl.next_chunk()
                mp3_path.write_bytes(buf.getvalue())

                # Embed artwork
                embed_artwork(mp3_path, img_cache[irht_id])

                # Upload
                track_id = ib_upload_file(user_id, token, mp3_path, name)
                new_track_ids.append(track_id)
                uploaded += 1
                log.info(f"  → track_id={track_id}")

            except Exception as e:
                errors += 1
                log.error(f"  ERREUR: {e}")
            finally:
                if mp3_path.exists():
                    mp3_path.unlink()

    log.info(f"\n=== Résultat : {uploaded} uploadés, {errors} erreurs ===")

    if not dry_run and old_track_ids and uploaded > 0:
        log.info(f"Trash des {len(old_track_ids)} anciennes pistes (sans artwork)...")
        # Re-login au cas où la session a expiré
        user_id, token = ib_login()
        ib_trash(user_id, token, old_track_ids)
        log.info("Trash terminé.")


if __name__ == "__main__":
    main()
