"""Volatilita' realizzata intraday su una fascia oraria fissa (per lo 0DTE), SOLA LETTURA.

Uso: uv run intraday_rv.py SPX --start 10:30 --end 15:45 --days 40
     uv run intraday_rv.py XSP

Per ciascuna delle ultime N sedute calcola la varianza realizzata (rendimenti log
a 5 minuti, RTH) nella fascia oraria indicata, poi annualizza mediando la VARIANZA
(non la vol, per Jensen) sugli ultimi 5 e 20 giorni:
  RV_attesa = max(RV5, RV20) + 1 punto di prudenza
stessa logica di strategies.py per RV10/RV20 giornaliere (rvs()), adattata alla
fascia oraria invece che alla giornata intera. Contesto: memoria
knowledge/METODI_OPERATIVI.md
(sezione «Come si calcola se un trade ha edge»).
"""
from __future__ import annotations
import argparse
import datetime as dt
from math import sqrt
from zoneinfo import ZoneInfo

import numpy as np

from ib_common import connect, index_contract

ET = ZoneInfo("America/New_York")


def parse_hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def fetch_bars(ib, contract, total_days: int, chunk_days: int = 20):
    """Scarica a blocchi andando indietro nel tempo (i bar a 5' hanno un limite di durata per richiesta)."""
    all_bars = []
    end = ""
    seen_dates: set[dt.date] = set()
    while True:
        chunk = ib.reqHistoricalData(
            contract, endDateTime=end, durationStr=f"{chunk_days} D",
            barSizeSetting="5 mins", whatToShow="TRADES", useRTH=True,
            formatDate=2, keepUpToDate=False,
        )
        if not chunk:
            break
        new = [b for b in chunk if b.date not in {x.date for x in all_bars}]
        all_bars = new + all_bars
        chunk_dates = {b.date.astimezone(ET).date() for b in chunk}
        seen_dates |= chunk_dates
        if len(chunk_dates) < 2 or len(seen_dates) >= total_days:
            break
        oldest = min(b.date for b in chunk)
        end = oldest.astimezone(dt.timezone.utc).strftime("%Y%m%d %H:%M:%S UTC")
        ib.sleep(1.5)  # pacing IBKR
    return sorted(all_bars, key=lambda b: b.date)


def window_rv(bars, start_hm, end_hm) -> dict[dt.date, float]:
    by_day: dict[dt.date, list[tuple[dt.datetime, float]]] = {}
    for b in bars:
        t_et = b.date.astimezone(ET)
        hm = (t_et.hour, t_et.minute)
        if start_hm <= hm <= end_hm:
            by_day.setdefault(t_et.date(), []).append((t_et, b.close))
    day_var = {}
    for d, pts in by_day.items():
        pts.sort()
        closes = np.array([p[1] for p in pts], dtype=float)
        if len(closes) < 3:
            continue
        r = np.diff(np.log(closes))
        day_var[d] = float(np.sum(r ** 2))  # varianza realizzata della finestra, quel giorno
    return day_var


def summarize(day_var: dict[dt.date, float]):
    days = sorted(day_var)
    def rv(n):
        last = [day_var[d] for d in days[-n:]]
        return sqrt(float(np.mean(last)) * 252) * 100 if last else None
    return rv(5), rv(20), days


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="SPX")
    ap.add_argument("--start", default="10:30")
    ap.add_argument("--end", default="15:45")
    ap.add_argument("--days", type=int, default=40)
    args = ap.parse_args()
    start_hm, end_hm = parse_hm(args.start), parse_hm(args.end)

    ib = connect("127.0.0.1", 7496, 143)
    try:
        c = index_contract(args.symbol)
        ib.qualifyContracts(c)
        bars = fetch_bars(ib, c, args.days)
        if not bars:
            print("nessun dato storico ricevuto"); return
        day_var = window_rv(bars, start_hm, end_hm)
        rv5, rv20, days = summarize(day_var)
        print(f"{args.symbol}: fascia {args.start}-{args.end} ET, {len(days)} sedute ({days[0]} .. {days[-1]})")
        if rv5 and rv20:
            att = max(rv5, rv20) + 1
            print(f"RV5={rv5:.1f}%  RV20={rv20:.1f}%  ->  RV attesa (max+1) = {att:.1f}%")
        else:
            print("dati insufficienti nella fascia oraria richiesta")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
