"""Valutazione di edge ad apertura di sessione (SOLA LETTURA), su richiesta ("fai apertura europea" / "fai apertura USA").
Lancia strategies.py sull'indice della sessione + almeno 30 azioni molto liquide, poi filtra i candidati
con la regola d'ingresso: margine >= +2 punti, spread di ogni gamba <= 10%, open interest >= 100 su ogni gamba.
Uso: uv run --with yfinance session_scan.py eu    (ESTX50 + 30 azioni EU su Eurex)
     uv run --with yfinance session_scan.py us    (SPX, XSP + 32 azioni USA molto liquide)
Dati frozen se MDT=2 (mercato chiuso); di norma va lanciato a mercato aperto (MDT=1, default).
"""
import json, os, re, subprocess, sys
from pathlib import Path
from strategies import EU_LIST, US_LIST

HERE = Path(__file__).parent
SESSIONS = {
    "eu": dict(label="Europea (Eurex/Xetra)", underlyings=["ESTX50"] + EU_LIST),
    "us": dict(label="USA", underlyings=["SPX", "XSP"] + US_LIST),
}


def run(names):
    env = dict(os.environ)
    cmd = ["uv", "run", "--with", "yfinance", "strategies.py"] + names
    return subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=str(HERE), env=env)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SESSIONS:
        print("Uso: session_scan.py eu|us"); return 1
    sess = SESSIONS[sys.argv[1]]
    print(f"=== Sessione {sess['label']}: {len(sess['underlyings'])} sottostanti ===\n")
    proc = run(sess["underlyings"])
    out = proc.stdout
    print(out)
    if proc.stderr.strip():
        print("--- stderr (ultime righe) ---\n" + "\n".join(proc.stderr.strip().splitlines()[-15:]))

    cands, errors = [], []
    for line in out.splitlines():
        if line.startswith("SUMMARY|"):
            _, name, js = line.split("|", 2)
            for r in json.loads(js):
                if r.get("margin") is not None and r["margin"] >= 2 and r.get("sp", 99) <= 10:
                    ois = [o for o in r.get("oi", []) if o is not None]
                    if ois and min(ois) >= 100:
                        cands.append(f"{name} {r['nm']} (margine {r['margin']:+.1f}, EV {r['ev']:+.0f}, spread max {r['sp']:.0f}%, OI min {min(ois):.0f})")
        m = re.match(r"^(\S+)\s*:\s", line)
        if m and ("errore" in line or "senza prezzo" in line or "incompleta" in line or "nessuna scadenza" in line):
            errors.append(line.strip())

    print("\n=== Verdetto ===")
    if cands:
        print("OPERAZIONI CANDIDATE ESEGUIBILI (margine >= +2, spread <= 10%, open interest >= 100 su ogni gamba):")
        for c in cands:
            print(" - " + c)
    else:
        print("NESSUNA OPERAZIONE CONSIGLIATA: nessuna struttura supera insieme margine, spread e open interest richiesti.")
    if errors:
        print(f"\n{len(errors)} sottostanti senza dati validi: " + "; ".join(e.split(":")[0] for e in errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
