#!/usr/bin/env bash
# Prepara l'ambiente dopo il clone: dipendenze, cartelle, configurazioni di esempio, servizi systemd utente.
# TWS va installato e configurato PRIMA (vedi docs/RICOSTRUZIONE.md, sezione "TWS").
#   scripts/install.sh               installa tutto ma non avvia i servizi
#   scripts/install.sh --abilita     in piu' abilita e avvia server e timer
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
UV="$(command -v uv || true)"
[ -n "$UV" ] || { echo "uv non trovato: installarlo (https://docs.astral.sh/uv/) e rilanciare"; exit 1; }

echo "== dipendenze Python (uv sync)"
( cd "$REPO" && uv sync )

echo "== cartelle locali (non versionate)"
mkdir -p "$REPO/opzioni/data" "$REPO/opzioni/logs"
for f in portfolio_map price_rules; do
  if [ ! -f "$REPO/opzioni/data/$f.json" ]; then
    cp "$REPO/config.example/$f.example.json" "$REPO/opzioni/data/$f.json"
    echo "   creato opzioni/data/$f.json DAL MODELLO: va adattato alle proprie posizioni"
  fi
done

echo "== servizi systemd utente"
DEST="$HOME/.config/systemd/user"
mkdir -p "$DEST"
for f in "$REPO"/systemd/*; do
  sed "s|@REPO@|$REPO|g; s|@UV@|$UV|g" "$f" > "$DEST/$(basename "$f")"
  echo "   installato $(basename "$f")"
done
systemctl --user daemon-reload
if [ "${1:-}" = "--abilita" ]; then
  systemctl --user enable --now opzioni-chain-builder.service opzioni-dashboard.timer opzioni-opportunita.timer
  echo "   servizi abilitati"
else
  echo "   servizi installati ma NON abilitati: rilanciare con --abilita quando TWS e' pronto"
fi
echo "Fatto. Prossimo passo: scripts/verifica_ambiente.sh"
