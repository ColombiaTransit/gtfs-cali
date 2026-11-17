"""
Schedule reference: per route, the operating window for HABIL / SABADO / DOM_FEST
=================================================================================
Sources, best first:
  1. Live Metro Cali listadoRutas.php (the data behind the "Busca Rutas MIO" page)
  2. data/listado_rutas_snapshot_*.json (newest snapshot of the same endpoint)
  3. data/route_schedule_pdf.csv (poster dated Jan 2026) - only for route codes the
     two sources above do not have (A01B, A73, ...)
Per route code the window is the union over all variants (Normal, Desvio, Ciclovia...):
earliest start, latest end.
"""
import glob
import json
import os
import re

import pandas as pd

LISTADO_URL = "https://metrocali.gov.co/newmioapp/mioapp/views/rutas/api/listadoRutas.php"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PDF_CSV = os.path.join(DATA_DIR, "route_schedule_pdf.csv")
DAYTYPES = ["HABIL", "SABADO", "DOM_FEST"]
API_COLS = {"HABIL": "habil", "SABADO": "sabado", "DOM_FEST": "dom_fest"}
RANGE = re.compile(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})")


def parse_window(text):
    """'5:00 -19:00' / '6:00-7:00 / 14:00-22:00' -> (start_min, end_min); NO OPERA/empty -> None."""
    if not isinstance(text, str) or "NO OPERA" in text.upper():
        return None
    parts = RANGE.findall(text.replace(" ", ""))
    if not parts:
        return None
    return (min(int(a) * 60 + int(b) for a, b, _, _ in parts),
            max(int(c) * 60 + int(d) for _, _, c, d in parts))


def _union(windows):
    ws = [w for w in windows if w]
    return (min(w[0] for w in ws), max(w[1] for w in ws)) if ws else None


def table_from_listado(rows):
    """List of listadoRutas.php rows -> {route_code: {daytype: window|None}}."""
    by_code = {}
    for r in rows:
        code = str(r.get("route_code", "")).strip().upper()
        if code:
            by_code.setdefault(code, []).append(r)
    return {code: {dt: _union([parse_window(r.get(col)) for r in rs]) for dt, col in API_COLS.items()}
            for code, rs in by_code.items()}


def table_from_pdf_csv(path=PDF_CSV):
    if not os.path.exists(path):
        return {}
    df = pd.read_csv(path, dtype=str).fillna("")
    return {r["route_code"].strip().upper(): {dt: parse_window(r[col]) for dt, col in API_COLS.items()}
            for _, r in df.iterrows()}


def newest_snapshot(data_dir=DATA_DIR):
    files = sorted(glob.glob(os.path.join(data_dir, "listado_rutas_snapshot_*.json")))
    return files[-1] if files else None


def load_listado_rows(session=None, data_dir=DATA_DIR, log=print):
    """Raw listadoRutas.php rows: live first, else the newest snapshot. Returns (rows, label)."""
    try:
        import requests
        r = (session or requests).get(LISTADO_URL, timeout=30)
        r.raise_for_status()
        rows = r.json()
        if isinstance(rows, list) and rows:
            return rows, "live"
    except Exception as e:
        log(f"  listadoRutas.php not usable ({type(e).__name__}) - using the newest snapshot")
    snap = newest_snapshot(data_dir)
    if snap:
        log(f"  using snapshot {os.path.basename(snap)}")
        return json.load(open(snap, encoding="utf-8")), "snapshot"
    return None, None


def load_reference(session=None, data_dir=DATA_DIR, pdf_csv=PDF_CSV, log=print):
    """Returns (table, sources) where sources maps route_code -> 'live'|'snapshot'|'pdf'."""
    rows, label = load_listado_rows(session=session, data_dir=data_dir, log=log)
    table, sources = {}, {}
    if rows:
        table = table_from_listado(rows)
        sources = {c: label for c in table}
    for code, w in table_from_pdf_csv(pdf_csv).items():
        if code not in table:
            table[code] = w
            sources[code] = "pdf"
    return table, sources
