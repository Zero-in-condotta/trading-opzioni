"""Sonda gratuita per Massive (ex Polygon.io): controlla cosa contiene davvero, PRIMA di pagare.

Domande a cui risponde:
  1. Esistono i contratti di SPX, SPXW, XSP (indici) oltre a SPY, anche scaduti?
  2. Con il tuo piano, arrivano le barre giornaliere di un contratto scaduto?
  3. Con il tuo piano, arrivano le quotazioni (bid/ask) storiche? (di solito serve il piano Advanced)

La chiave API si legge dalla variabile d'ambiente MASSIVE_API_KEY. NON scriverla nel codice
e NON incollarla nella chat.

Uso:
    export MASSIVE_API_KEY="..."          # fish: set -x MASSIVE_API_KEY "..."
    uv run massive_probe.py
    uv run massive_probe.py --base-url https://api.polygon.io    # se il dominio massive.com non risponde
"""
from __future__ import annotations

import argparse
import os
import sys

import requests


def get(base: str, key: str, path: str, params: dict | None = None):
    params = dict(params or {})
    r = requests.get(base + path, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:300]}
    return r.status_code, body


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="https://api.massive.com")
    args = p.parse_args()
    key = os.environ.get("MASSIVE_API_KEY")
    if not key:
        print("Manca la variabile MASSIVE_API_KEY.")
        return 1
    base = args.base_url.rstrip("/")

    print("== 1. Contratti scaduti per sottostante (5 esempi ciascuno) ==")
    first_expired = {}
    for und in ("SPX", "SPXW", "XSP", "SPY"):
        code, body = get(base, key, "/v3/reference/options/contracts",
                         {"underlying_ticker": und, "expired": "true", "limit": 5,
                          "expiration_date.gte": "2023-01-01", "order": "asc", "sort": "expiration_date"})
        res = body.get("results") or []
        print(f"{und}: HTTP {code}, contratti trovati: {len(res)}",
              [r.get("ticker") for r in res[:3]] if res else body.get("message") or body.get("error") or "")
        if res:
            first_expired[und] = res[0]["ticker"]

    print("\n== 2. Barre giornaliere di un contratto scaduto ==")
    for und, tk in first_expired.items():
        code, body = get(base, key, f"/v2/aggs/ticker/{tk}/range/1/day/2023-01-01/2026-12-31", {"limit": 5})
        print(f"{tk}: HTTP {code}, barre: {body.get('resultsCount')}, messaggio: {body.get('message') or body.get('status')}")

    print("\n== 3. Quotazioni (bid/ask) storiche di un contratto scaduto ==")
    for und, tk in first_expired.items():
        code, body = get(base, key, f"/v3/quotes/{tk}", {"limit": 3, "order": "asc"})
        n = len(body.get("results") or [])
        print(f"{tk}: HTTP {code}, quotazioni: {n}, messaggio: {body.get('message') or body.get('status')}")
    print("\nInterpretazione: 200 con risultati = accessibile; 403 = non incluso nel tuo piano; 0 risultati = dato assente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
