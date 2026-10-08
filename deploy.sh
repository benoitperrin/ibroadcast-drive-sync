#!/bin/bash
# Déploiement sur un serveur : ./deploy.sh <hôte ssh>
# Copie le code, puis la config, la state DB et le token Drive de cette machine, et installe le service.
set -e

REMOTE="${1:?usage : ./deploy.sh <hôte ssh>}"
D="$(cd "$(dirname "$0")" && pwd)"
DEST=/home/ubuntu/ib-drive-sync
CONFIG_DIR_LOCAL="$HOME/.config/ib-drive-sync"
CONFIG_DIR_REMOTE="/home/ubuntu/.config/ib-drive-sync"
DRIVE_TOKEN_LOCAL=$(python3 -c "import json; print(json.load(open('$CONFIG_DIR_LOCAL/config.json'))['drive_token_file'])")
DRIVE_TOKEN_REMOTE="$CONFIG_DIR_REMOTE/drive_token.json"

echo "=== Copie des fichiers ==="
ssh $REMOTE "mkdir -p $DEST"
scp "$D/sync.py" "$D/requirements.txt" $REMOTE:$DEST/

echo "=== Copie du token Drive ==="
ssh $REMOTE "mkdir -p -m 700 $CONFIG_DIR_REMOTE"
scp "$DRIVE_TOKEN_LOCAL" $REMOTE:"$DRIVE_TOKEN_REMOTE"
ssh $REMOTE "chmod 600 $DRIVE_TOKEN_REMOTE"

echo "=== Copie de la config et state DB ==="
# Adapter le chemin du token Drive dans la config, qui porte aussi le mot de passe iBroadcast : 600
python3 -c "
import json, sys
with open('$CONFIG_DIR_LOCAL/config.json') as f:
    c = json.load(f)
c['drive_token_file'] = '$DRIVE_TOKEN_REMOTE'
print(json.dumps(c, indent=2))
" | ssh $REMOTE "umask 077 && cat > $CONFIG_DIR_REMOTE/config.json"
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
sed "s|/usr/bin/python3|$DEST/venv/bin/python3|g" "$D/ib-drive-sync.service" | \
  sed "s|/home/ubuntu/ib-drive-sync|$DEST|g" | \
  ssh $REMOTE "cat > ~/.config/systemd/user/ib-drive-sync.service"
ssh $REMOTE "XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user daemon-reload"

echo ""
echo "=== Déploiement terminé ==="
echo "Pour tester d'abord :"
echo "  ssh $REMOTE '$DEST/venv/bin/python3 $DEST/sync.py --once --dry-run'"
echo ""
echo "Pour démarrer le service, après l'avoir arrêté sur l'ancienne machine :"
echo "  ssh $REMOTE 'XDG_RUNTIME_DIR=/run/user/\$(id -u) systemctl --user enable --now ib-drive-sync'"
