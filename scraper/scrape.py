"""
Legge i fondi disponibili del Bonus colonnine domestiche dalla dashboard
Power BI pubblicata da Invitalia e aggiorna data.csv (una riga al giorno).

Variabili d'ambiente opzionali:
  REPORT_URL  link della dashboard Power BI (default: quello attuale di Invitalia)
  KEYWORD     parola vicina all'importo giusto (default: "disponibil")
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import json
import os
import re
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data.csv"
DEBUG = ROOT / "debug"

REPORT_URL = os.environ.get("REPORT_URL") or (
    "https://app.powerbi.com/view?r=eyJrIjoiM2ViMmVmYjctZmU2NS00YjFkLWE0MzQtZDdmZjdlZjhjNmM2IiwidCI6ImFmZDBhNzVjLTg2NzEtNGNjZS05MDYxLTJjYTBkOTJlNDIyZiIsImMiOjh9",
)
KEYWORD = (os.environ.get("KEYWORD") or "disponibil").lower()
TZ = ZoneInfo("Europe/Rome")

SEP = r"[.\u00a0\u202f ]"  # separatori delle migliaia usati da Power BI
# 8.331.229 | 8.331.229,45 | € 8.331.229 | 8331229 € (numeri senza separatori solo se c'è il simbolo €)
RE_GROUPED = re.compile(rf"(?<![\d,.])(\d{{1,3}}(?:{SEP}\d{{3}})+(?:,\d{{1,2}})?)(?![\d])")
RE_PLAIN_EUR = re.compile(r"(?:€\s*(\d{4,}(?:,\d{1,2})?)(?!\d))|(?<![\d,.])(\d{4,}(?:,\d{1,2})?)\s*€")
RE_ABBR = re.compile(r"(?<![\d,.])(\d{1,4}(?:,\d{1,3})?)\s*(Mln|Mio|M)\b\s*€?", re.I)


def to_number(s: str) -> float:
    s = re.sub(SEP, "", s).replace(",", ".")
    return float(s)


def find_amounts(text: str) -> list[dict]:
    """Tutti gli importi in euro plausibili in un testo, con posizione."""
    out = []
    for m in RE_GROUPED.finditer(text):
        out.append({"value": to_number(m.group(1)), "start": m.start(), "approx": False})
    for m in RE_PLAIN_EUR.finditer(text):
        g = m.group(1) or m.group(2)
        out.append({"value": to_number(g), "start": m.start(), "approx": False})
    for m in RE_ABBR.finditer(text):
        out.append({"value": to_number(m.group(1)) * 1_000_000, "start": m.start(), "approx": True})
    # scarta anni e cifre troppo piccole/grandi per essere un fondo
    return [a for a in out if 10_000 <= a["value"] < 10_000_000_000]


def collect_candidates(blocks: list[dict]) -> list[dict]:
    """blocks: [{"kind": "label"|"text", "text": str}] estratti dalla pagina."""
    cands = []
    for b in blocks:
        text = b["text"]
        low = text.lower()
        for a in find_amounts(text):
            if b["kind"] == "label":
                # aria-label / title di una visual: parola chiave nello stesso testo
                score = 0 if KEYWORD in low else None
                ctx = text[:200]
            else:
                before = low[max(0, a["start"] - 160): a["start"]]
                pos = before.rfind(KEYWORD)
                score = (len(before) - pos) if pos >= 0 else None
                ctx = text[max(0, a["start"] - 80): a["start"] + 30]
            cands.append({**a, "kind": b["kind"], "score": score, "context": " ".join(ctx.split())})
    return cands


def last_value() -> float | None:
    rows = read_rows()
    return rows[-1][1] if rows else None


def choose(cands: list[dict], previous: float | None) -> tuple[dict | None, str]:
    exact = [c for c in cands if not c["approx"]]
    pool = exact or cands
    kw = [c for c in pool if c["score"] is not None]
    if kw:
        labels = [c for c in kw if c["kind"] == "label"]
        best = min(labels or kw, key=lambda c: c["score"])
        return best, f"vicino alla parola '{KEYWORD}'"
    if previous and pool:
        best = min(pool, key=lambda c: abs(c["value"] - previous))
        if best["value"] <= previous * 1.5 and best["value"] >= previous * 0.3:
            return best, "il più vicino al valore precedente"
    distinct = {c["value"] for c in pool}
    if len(distinct) == 1:
        return pool[0], "unico importo trovato"
    return None, "nessun criterio ha individuato l'importo"


EXTRACT_JS = """
() => {
  const out = [];
  document.querySelectorAll('[aria-label],[title]').forEach(el => {
    for (const a of ['aria-label','title']) {
      const v = el.getAttribute(a);
      if (v && /\\d/.test(v)) out.push({kind:'label', text:v});
    }
  });
  const body = document.body ? document.body.innerText : '';
  if (body) out.push({kind:'text', text: body});
  // testo dentro gli SVG (le "card" di Power BI spesso sono <text>)
  const svgText = [...document.querySelectorAll('svg text, svg title')].map(t => t.textContent).join('\\n');
  if (svgText) out.push({kind:'text', text: svgText});
  return out;
}
"""


async def scrape_once() -> tuple[float, list[dict], str]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": 1600, "height": 1000}, locale="it-IT", timezone_id="Europe/Rome"
        )
        cands: list[dict] = []
        blocks: list[dict] = []
        try:
            await page.goto(REPORT_URL, wait_until="domcontentloaded", timeout=90_000)
        except Exception:
            await page.screenshot(path=str(DEBUG / "dashboard-errore.png"), full_page=True)
            raise
        # la dashboard carica i dati in modo asincrono: riprova per ~60 s
        for _ in range(20):
            await page.wait_for_timeout(3_000)
            blocks = []
            for frame in page.frames:
                try:
                    blocks += await frame.evaluate(EXTRACT_JS)
                except Exception:
                    pass
            cands = collect_candidates(blocks)
            if any(c["score"] is not None for c in cands):
                await page.wait_for_timeout(2_000)  # lascia finire il rendering
                break
        await page.screenshot(path=str(DEBUG / "dashboard.png"), full_page=True)
        (DEBUG / "blocks.json").write_text(json.dumps(blocks, ensure_ascii=False, indent=1))
        await browser.close()

    best, why = choose(cands, last_value())
    (DEBUG / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False, indent=1))
    if not best:
        raise RuntimeError(why)
    return best["value"], cands, f"{why}: «{best['context']}»" + (" (valore abbreviato!)" if best["approx"] else "")


def read_rows() -> list[tuple[str, float]]:
    if not DATA.exists():
        return []
    rows = []
    with DATA.open(newline="", encoding="utf-8") as f:
        for r in csv.reader(f, delimiter=";"):
            if len(r) >= 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0]):
                rows.append((r[0], float(r[1])))
    return sorted(rows)


def write_value(date: str, value: float) -> None:
    rows = dict(read_rows())
    rows[date] = value
    with DATA.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(["data", "importo"])
        for d in sorted(rows):
            v = rows[d]
            w.writerow([d, int(v) if v == int(v) else v])


async def main() -> int:
    DEBUG.mkdir(exist_ok=True)
    (DEBUG / "avvio.txt").write_text(f"Python {sys.version}\nURL {REPORT_URL}\nKEYWORD {KEYWORD}\n")
    last_err = None
    for attempt in range(1, 4):
        try:
            value, cands, why = await scrape_once()
            today = dt.datetime.now(TZ).date().isoformat()
            write_value(today, value)
            print(f"{today}: {value:,.2f} €  ({why})".replace(",", "X").replace(".", ",").replace("X", "."))
            print(f"Importi trovati nella pagina: {len(cands)}")
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a", encoding="utf-8") as f:
                    f.write(f"### Fondi disponibili al {today}\n\n**{value:,.0f} €**\n\n{why}\n".replace(",", "."))
            return 0
        except Exception as e:  # noqa: BLE001
            last_err = e
            tb = traceback.format_exc()
            print(f"Tentativo {attempt} non riuscito:\n{tb}", file=sys.stderr)
            with (DEBUG / "errori.txt").open("a", encoding="utf-8") as f:
                f.write(f"--- tentativo {attempt} ---\n{tb}\n")
            await asyncio.sleep(10)
    print(f"Lettura non riuscita: {last_err}. Guarda gli artifact 'debug' del workflow.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
