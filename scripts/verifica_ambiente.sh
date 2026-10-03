#!/usr/bin/env bash
# Controlli dopo l'installazione. Non invia ordini. Esce con codice 1 se qualcosa di essenziale manca.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORTA="${TWS_PORT:-7496}"     # 7496 conto reale, 7497 paper: TWS_PORT=7497 scripts/verifica_ambiente.sh
ok=0; ko() { echo "  [ERRORE] $1"; ok=1; }; bene() { echo "  [ok] $1"; }
echo "== strumenti"
command -v uv >/dev/null && bene "uv $(uv --version | cut -d' ' -f2)" || ko "uv mancante"
command -v systemctl >/dev/null && bene "systemd presente" || ko "systemctl mancante (servizi e timer non disponibili)"
command -v notify-send >/dev/null && bene "notify-send (avvisi di drawdown)" || echo "  [facoltativo] notify-send mancante: niente avvisi desktop di drawdown"
command -v chromium >/dev/null && bene "chromium (utile per gli screenshot di verifica)" || echo "  [facoltativo] chromium mancante"
echo "== TWS"
if ss -ltn 2>/dev/null | grep -qE "[:.]${PORTA}\b"; then bene "porta ${PORTA} in ascolto"; else ko "porta ${PORTA} NON in ascolto: aprire TWS e attivare l'API (docs/RICOSTRUZIONE.md)"; fi
echo "== codice"
( cd "$REPO/opzioni" && uv run python -m py_compile *.py ) && bene "tutti gli script compilano" || ko "errori di sintassi"
echo "== test offline (nessun TWS)"
( cd "$REPO" && uv run python tests/test_offline.py ) && bene "test offline superati" || ko "test offline falliti"
echo "== connessione a TWS e dati"
( cd "$REPO/opzioni" && timeout 90 uv run check_connection.py --port "$PORTA" --market-data-type 1 ) && bene "connessione e lettura prezzi" || ko "connessione a TWS non riuscita"
[ $ok -eq 0 ] && echo "TUTTO OK" || echo "ALCUNI CONTROLLI FALLITI"
exit $ok
