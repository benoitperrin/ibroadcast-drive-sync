# ibroadcast-drive-sync

Daemon that watches a Google Drive folder and automatically uploads new audio files to [iBroadcast](https://ibroadcast.com). Also includes a one-shot script to bulk-embed artwork into existing tracks.

## Tools

### `sync.py` — Drive → iBroadcast daemon

Polls a Google Drive folder every N seconds, uploads any new audio file not already present in iBroadcast (checked by MD5), and optionally trashes removed files.

```
sync.py              # daemon (infinite loop)
sync.py --once       # single pass and exit
sync.py --dry-run    # simulate without uploading or trashing
sync.py --setup      # interactive setup wizard
```

#### Configuration

Config file: `~/.config/ib-drive-sync/config.json`

```json
{
  "ib_email": "you@example.com",
  "ib_password": "...",
  "drive_token_file": "/home/you/.config/google/drive_token.json",
  "drive_folder_id": "<Drive folder ID>",
  "auto_trash": false,
  "poll_interval": 300
}
```

Run `sync.py --setup` to create this file interactively.

#### Google Drive credentials

The Drive token (`drive_token_file`) is an OAuth2 token in the format produced by tools like [rclone](https://rclone.org/) or the Google API Python client. It must have `drive.readonly` scope at minimum.

#### iBroadcast authentication

Uses the iBroadcast web login form (`https://login.ibroadcast.com/`) — the legacy `mode=status` API is broken server-side. Credentials are read from the config file.

#### Deployment (systemd)

```bash
# Install dependencies
pip install -r requirements.txt

# Run as a systemd user service
cp ib-drive-sync.service ~/.config/systemd/user/
systemctl --user enable --now ib-drive-sync
```

For deployment to a remote server (e.g. an OCI ARM instance):

```bash
./deploy-condor.sh
ssh condor 'systemctl --user enable --now ib-drive-sync'
```

---

### `embed-artwork.py` — Bulk artwork embedding for Psaumes dans la Ville

One-shot script tailored for the *Psaumes dans la Ville* audio collection ([retraitedanslaville.org](https://psaume.retraitedanslaville.org/tous-les-psaumes)).

For each of the 188 psalm MP3s in a Drive folder:
1. Downloads the corresponding medieval illumination from the IRHT/CNRS collection (upscaled to 500×500)
2. Embeds it as an `APIC` ID3 tag in the MP3
3. Re-uploads to iBroadcast
4. Trashes the original (artwork-less) track

The illuminations are miniatures from digitised medieval psalters, sourced from the [IRHT (CNRS)](https://www.irht.cnrs.fr/) via the Retraite dans la Ville website.

Credentials are read from `~/.config/ib-drive-sync/config.json` (shared with `sync.py`), or from `IB_EMAIL` / `IB_PASSWORD` environment variables.

## Requirements

```
google-api-python-client
google-auth-oauthlib
requests
mutagen    # for embed-artwork.py
Pillow     # for embed-artwork.py
```

## Supported audio formats

`.mp3`, `.flac`, `.m4a`, `.ogg`, `.wav`, `.aac`, `.opus`, `.wma`
