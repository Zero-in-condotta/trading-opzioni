"""Beta dei sottostanti del portafoglio rispetto all'S&P 500 (rendimenti giornalieri, 1 e 2 anni).
Scrive data/betas.json, letto da stress.py. Uso: uv run --with yfinance calcola_beta.py [TICKER ...]
Gli indici (ESTX50, XSP, ...) hanno beta 1 per definizione dello scenario: lo scenario e' un
movimento dell'azionario, non di un singolo titolo."""
import datetime as dt, json, sys
import yfinance as yf
from ib_common import DATA

def _simboli_da_mappa():
    """Sottostanti NON indice presenti in data/portfolio_map.json (le gambe sono opzioni: il simbolo e' il sottostante)."""
    try:
        m = json.loads((DATA / "portfolio_map.json").read_text())
        indici = {"ESTX50", "XSP", "SPX", "NDX", "XND", "RUT", "MRUT", "DJX", "VIX"}
        return sorted({l["symbol"] for g in m["groups"] for l in g["legs"]} - indici)
    except Exception:
        return []


SIMBOLI = sys.argv[1:] or _simboli_da_mappa()
if not SIMBOLI:
    raise SystemExit("Nessun titolo: passa i ticker come argomenti (es. calcola_beta.py AAPL MSFT) o compila data/portfolio_map.json")
mercato = yf.download("^GSPC", period="2y", auto_adjust=True, progress=False)["Close"].squeeze().pct_change().dropna()
out = {"aggiornato": dt.date.today().isoformat(), "nota": "beta vs S&P 500; usato = max(beta 2 anni, 1.0) in stress.py (scelta prudente)", "beta": {}}
for s in SIMBOLI:
    r = yf.download(s, period="2y", auto_adjust=True, progress=False)["Close"].squeeze().pct_change().dropna()
    j = mercato.to_frame("m").join(r.to_frame("s"), how="inner").dropna()
    def beta(d):
        return float(d["s"].cov(d["m"]) / d["m"].var()) if len(d) > 60 else None
    b2, b1 = beta(j), beta(j.iloc[-252:])
    corr = float(j["s"].corr(j["m"]))
    out["beta"][s] = {"beta_2a": b2, "beta_1a": b1, "correlazione": corr, "giorni": len(j)}
    print(f"{s}: beta 2a {b2:.2f} | 1a {b1:.2f} | correlazione {corr:.2f} ({len(j)} giorni)")
(DATA / "betas.json").write_text(json.dumps(out, indent=1))
