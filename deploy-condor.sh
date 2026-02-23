#!/bin/bash
# Déploiement sur Condor
set -e

REMOTE=condor
DEST=/home/ubuntu/ib-drive-sync
CONFIG_DIR_LOCAL="$HOME/.config/ib-drive-sync"
CONFIG_DIR_REMOTE="/home/ubuntu/.config/ib-drive-sync"
DRIVE_TOKEN_LOCAL="$HOME/.config/google/drive_token.json"
DRIVE_TOKEN_REMOTE="/home/ubuntu/.config/google/drive_token.json"

echo "=== Copie des fichiers ==="
ssh $REMOTE "mkdir -p $DEST"
scp sync.py requirements.txt $REMOTE:$DEST/

echo "=== Copie du token Drive ==="
ssh $REMOTE "mkdir -p /home/ubuntu/.config/google"
scp "$DRIVE_TOKEN_LOCAL" $REMOTE:"$DRIVE_TOKEN_REMOTE"

echo "=== Copie de la config et state DB ==="
ssh $REMOTE "mkdir -p $CONFIG_DIR_REMOTE"
# Adapter le chemin du token Drive dans la config
python3 -c "
import json, sys
with open('$CONFIG_DIR_LOCAL/config.json') as f:
    c = json.load(f)
c['drive_token_file'] = '$DRIVE_TOKEN_REMOTE'
print(json.dumps(c, indent=2))
" | ssh $REMOTE "cat > $CONFIG_DIR_REMOTE/config.json"
# Copier la state DB si elle existe (évite de re-uploader)
if [ -f "$CONFIG_DIR_LOCAL/state.db" ]; then
    scp "$CONFIG_DIR_LOCAL/state.db" $REMOTE:"$CONFIG_DIR_REMOTE/state.db"
    echo "  state.db copiée"
fi

echo "=== Création du venv et installation des dépendances ==="
ssh $REMOTE "python3 -m venv $DEST/venv && $DEST/venv/bin/pip install -q -r $DEST/requirements.txt"

echo "=== Installation du service systemd ==="
ssh $REMOTE "mkdir -p ~/.config/systemd/user"
# Adapter les chemins dans le service (utilise le venv)
sed "s|/usr/bin/python3|$DEST/venv/bin/python3|g" ib-drive-sync.service | \
  sed "s|/home/ubuntu/ib-drive-sync|$DEST|g" | \
  ssh $REMOTE "cat > ~/.config/systemd/user/ib-drive-sync.service"
ssh $REMOTE "systemctl --user daemon-reload"

echo ""
echo "=== Déploiement terminé ==="
echo "Pour démarrer le service :"
echo "  ssh $REMOTE 'systemctl --user enable --now ib-drive-sync'"
echo ""
echo "Pour tester d'abord :"
echo "  ssh $REMOTE '$DEST/venv/bin/python3 $DEST/sync.py --once --dry-run'"
