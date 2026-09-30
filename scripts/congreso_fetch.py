#!/usr/bin/env python3
"""Descarga (con caché en raw/) las votaciones del Pleno del Congreso de la legislatura en curso.

    python3 scripts/congreso_fetch.py            # todo (legislatura XV)
    python3 scripts/congreso_fetch.py --force    # ignora la caché de las páginas HTML (no de los JSON)

Punto de entrada real (medido el 30/09/2026, sin clave ni registro):
  1. https://www.congreso.es/es/opendata/votaciones  -> HTML con `var diasVotaciones = [YYYYMMDD, ...]`
     (todos los días con votaciones de la legislatura seleccionada) y el listado del último día.
  2. Página por día:  .../opendata/votaciones?p_p_id=votaciones&p_p_lifecycle=0&p_p_state=normal
        &p_p_mode=view&targetLegislatura=XV&targetDate=DD/MM/YYYY
     -> enlaces /webpublica/opendata/votaciones/Leg15/SesionNNN/YYYYMMDD/VotacionNNN/VOT_*.json|xml|pdf|png
        y un ZIP por sesión/día con todo lo anterior.
  3. Cada JSON = una votación: {informacion, totales, votaciones:[{asiento, diputado, grupo, voto}]}.
  4. https://www.congreso.es/es/opendata/diputados -> DiputadosActivos__*.json, DiputadosDeBaja__*.csv
     (altas y bajas con fecha). Se guardan crudos; los derivados NO copian la biografía.
No hay paginación ni API: es un portal Liferay con ficheros estáticos. No se detectó límite de tasa
(ver raw/fetch_log.json: códigos HTTP y tiempos medidos). Trampa medida: el WAF devuelve 403 si el
User-Agent contiene una URL (p.ej. «como-vota/1.0 (…; github.com/…)»); con «como-vota/1.0» responde 200.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
BASE = "https://www.congreso.es"
LEG_NUM, LEG_ROMAN = 15, "XV"
UA = "como-vota/1.0"
CONCURRENCY = 4

LOG: dict = {"started": datetime.now(timezone.utc).isoformat(), "http": {}, "errors": [], "timings_s": []}


def get(url: str, dest: Path, force: bool = False) -> bytes | None:
    if dest.exists() and not force:
        return dest.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        code, body = e.code, None
    except Exception as e:  # red
        code, body = f"EXC {type(e).__name__}", None
    LOG["http"][str(code)] = LOG["http"].get(str(code), 0) + 1
    LOG["timings_s"].append(round(time.time() - t0, 3))
    if body is None:
        LOG["errors"].append({"url": url, "code": code})
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return body


def dias_votaciones(force: bool) -> list[str]:
    html = get(f"{BASE}/es/opendata/votaciones?p_p_id=votaciones&p_p_lifecycle=0&p_p_state=normal"
               f"&p_p_mode=view&targetLegislatura={LEG_ROMAN}",
               RAW / "html" / "opendata_votaciones.html", force).decode("utf-8", "replace")
    m = re.search(r"var diasVotaciones = \[([^\]]*)\]", html)
    if not m:
        sys.exit("No se encontró diasVotaciones en la página de open data")
    sel = re.search(r'<option[^>]*selected[^>]*value="(\d+)"', html)
    assert sel and int(sel.group(1)) == LEG_NUM, "La legislatura seleccionada por defecto no es la XV"
    return sorted(set(re.findall(r"\d{8}", m.group(1))))


def links_del_dia(dia: str, force: bool) -> tuple[list[str], list[dict]]:
    d = f"{dia[6:8]}/{dia[4:6]}/{dia[0:4]}"
    url = (f"{BASE}/es/opendata/votaciones?p_p_id=votaciones&p_p_lifecycle=0&p_p_state=normal"
           f"&p_p_mode=view&targetLegislatura={LEG_ROMAN}&targetDate={d}")
    html = get(url, RAW / "html" / "dias" / f"{dia}.html", force)
    if html is None:
        return [], []
    html = html.decode("utf-8", "replace")
    jsons = sorted(set(re.findall(r'href="(/webpublica/opendata/votaciones/Leg\d+/Sesion\d+/\d{8}/Votacion\d+/[^"]+\.json)"', html)))
    xmls = sorted(set(re.findall(r'href="(/webpublica/opendata/votaciones/Leg\d+/Sesion\d+/\d{8}/Votacion\d+/[^"]+\.xml)"', html)))
    sesiones = [{"titulo": t.strip(), "fecha": f} for t, f in
                re.findall(r"<h3>\s*([^<]+?)\s*</h3>.*?Fecha : (\d{2}/\d{2}/\d{4})", html, re.S)]
    # Votaciones sin JSON pero con XML: se registran para descargarlas igualmente
    base_j = {re.sub(r"/[^/]+\.json$", "", j) for j in jsons}
    xml_only = [x for x in xmls if re.sub(r"/[^/]+\.xml$", "", x) not in base_j]
    return jsons + xml_only, sesiones


def dest_for(link: str) -> Path:
    # /webpublica/opendata/votaciones/Leg15/Sesion002/20230919/Votacion001/VOT_x.json
    parts = link.split("/")
    return RAW / "votaciones" / parts[4] / parts[5] / parts[6] / f"{parts[7]}{Path(link).suffix}"


def fetch_diputados(force: bool) -> dict:
    html = get(f"{BASE}/es/opendata/diputados", RAW / "html" / "opendata_diputados.html", force).decode("utf-8", "replace")
    out = {}
    for key, pat in {"activos_json": r"/webpublica/opendata/diputados/DiputadosActivos__\d+\.json",
                     "baja_csv": r"/webpublica/opendata/diputados/DiputadosDeBaja__\d+\.csv",
                     "todos_json": r"/webpublica/opendata/diputados/Diput__\d+\.json"}.items():
        m = re.search(pat, html)
        if not m:
            LOG["errors"].append({"diputados": key, "code": "enlace no encontrado"})
            continue
        dest = RAW / "diputados" / Path(m.group(0)).name
        get(BASE + m.group(0), dest, force)
        out[key] = {"url": BASE + m.group(0), "file": str(dest.relative_to(ROOT))}
    return out


def main() -> None:
    force = "--force" in sys.argv
    RAW.mkdir(exist_ok=True)
    dias = dias_votaciones(force)
    print(f"Legislatura {LEG_ROMAN}: {len(dias)} días con votaciones ({dias[0]} .. {dias[-1]})")
    links: list[str] = []
    index: dict[str, dict] = {}
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        futs = {ex.submit(links_del_dia, d, force): d for d in dias}
        for f in as_completed(futs):
            l, s = f.result()
            index[futs[f]] = {"sesiones": s, "n_links": len(l)}
            links += l
    links = sorted(set(links))
    print(f"{len(links)} ficheros de votación enlazados; descargando los que falten…")
    t0, done = time.time(), 0
    with ThreadPoolExecutor(CONCURRENCY) as ex:
        futs = {ex.submit(get, BASE + l, dest_for(l)): l for l in links}
        for f in as_completed(futs):
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(links)}  {time.time()-t0:.0f}s", flush=True)
    dip = fetch_diputados(force)
    prev = RAW / "fetch_log.json"
    if not LOG["http"] and prev.exists():  # todo venía de caché: conservar las medidas de red de la descarga real
        old = json.loads(prev.read_text())
        LOG["http"], LOG["errors"], LOG["started"] = old["http"], old["errors"], old["started"]
        timing = old["timing_summary"]
    else:
        timing = {"n": len(LOG["timings_s"]), "mean": round(sum(LOG["timings_s"]) / max(1, len(LOG["timings_s"])), 3),
                  "max": max(LOG["timings_s"], default=0)}
    del LOG["timings_s"]
    LOG.update({
        "finished": datetime.now(timezone.utc).isoformat(), "legislatura": LEG_ROMAN,
        "dias": dias, "n_dias": len(dias), "n_links": len(links),
        "dias_sin_enlaces": [d for d, v in index.items() if v["n_links"] == 0],
        "index": index, "diputados": dip,
        "timing_summary": timing,
    })
    (RAW / "fetch_log.json").write_text(json.dumps(LOG, ensure_ascii=False, indent=1))
    print("HTTP:", LOG["http"], "errores:", len(LOG["errors"]))


if __name__ == "__main__":
    main()
