#!/usr/bin/env python3
"""Convierte el crudo (raw/votaciones/**.json) en los agregados públicos (data/*.json).

    python3 scripts/congreso_build.py

Decisiones editoriales (todas medidas y publicadas en data/calidad.json):
- Unidad = una votación del Pleno con voto nominal (fichero JSON del Congreso). Las votaciones por
  asentimiento no tienen voto individual: se cuentan y se excluyen de los agregados por diputado.
- «No vota» es la categoría oficial del Congreso: incluye ausencia y presencia sin emitir voto; el
  fichero no distingue ni marca el voto telemático (que se integra en Sí/No/Abstención). Aquí se
  llama «no vota», nunca «ausencia».
- Denominador por diputado = votaciones en cuyo listado nominal aparece ese diputado (el listado de
  cada votación es el censo de escaños en esa fecha). Altas y bajas quedan así recogidas sin
  interpolar fechas. Se cruza con las fechas oficiales de alta/baja sólo para verificar.
- Posición mayoritaria del grupo en una votación = opción (Sí/No/Abstención) más votada entre los
  votos emitidos por sus miembros en ESA votación (grupo según el propio fichero, que cambia si el
  diputado cambia de grupo). Si hay empate, ese grupo no tiene posición y la votación no cuenta para
  la coincidencia de sus miembros ni para la matriz entre grupos.
- Coincidencia de un diputado = votos emitidos iguales a la posición mayoritaria de su grupo /
  votos emitidos en votaciones donde su grupo tenía posición. Los «no vota» no cuentan ni a favor
  ni en contra: no votar no es desmarcarse.
- Se publica sólo lo que el Congreso publica de cada diputado en ejercicio: nombre, grupo,
  circunscripción, formación electoral, fechas de alta/baja y sentido del voto. Nunca la biografía.
"""
from __future__ import annotations

import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW, DATA = ROOT / "raw", ROOT / "data"
LEG = "XV"
OD_VOT = "https://www.congreso.es/es/opendata/votaciones"
OD_DIP = "https://www.congreso.es/es/opendata/diputados"
VOTOS = ("Sí", "No", "Abstención", "No vota")
GRUPO_NOMBRE = {
    "GP": "Popular", "GS": "Socialista", "GVOX": "VOX", "GSUMAR": "Sumar", "GR": "Republicano",
    "GJxCAT": "Junts per Catalunya", "GEH Bildu": "Euskal Herria Bildu", "GV (EAJ-PNV)": "Vasco (EAJ-PNV)",
    "GMx": "Mixto",
}


def fecha_iso(d: str) -> str:  # '29/9/2026' -> '2026-09-29'
    dd, mm, yy = d.split("/")
    return f"{yy}-{int(mm):02d}-{int(dd):02d}"


def parse_xml(p: Path) -> dict:
    r = ET.parse(p).getroot()
    g = lambda e, t: (e.findtext(t) or "").strip()
    inf, tot = r.find("Informacion"), r.find("Totales")
    return {
        "informacion": {"sesion": int(g(inf, "Sesion")), "numeroVotacion": int(g(inf, "NumeroVotacion")),
                        "fecha": g(inf, "Fecha"), "titulo": g(inf, "Titulo"), "textoExpediente": g(inf, "TextoExpediente"),
                        "tituloSubGrupo": g(inf, "TituloSubGrupo"), "textoSubGrupo": g(inf, "TextoSubGrupo")},
        "totales": {"asentimiento": g(tot, "Asentimiento"), "presentes": int(g(tot, "Presentes") or 0),
                    "afavor": int(g(tot, "AFavor") or 0), "enContra": int(g(tot, "EnContra") or 0),
                    "abstenciones": int(g(tot, "Abstenciones") or 0), "noVotan": int(g(tot, "NoVotan") or 0)},
        "votaciones": [{"asiento": g(v, "Asiento"), "diputado": g(v, "Diputado"), "grupo": g(v, "Grupo"), "voto": g(v, "Voto")}
                       for v in r.iter("Votacion")],
    }


def load_raw() -> tuple[list[dict], dict]:
    """Lee todos los ficheros; devuelve votaciones únicas + contadores de descarte."""
    calidad = Counter()
    por_clave: dict[tuple, dict] = {}
    duplicados = []
    files = sorted((RAW / "votaciones").glob(f"Leg15/Sesion*/*/Votacion*.*"))
    for p in files:
        calidad["ficheros_leidos"] += 1
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig")) if p.suffix == ".json" else parse_xml(p)
        except Exception as e:
            calidad["ficheros_ilegibles"] += 1
            duplicados.append({"fichero": str(p.relative_to(ROOT)), "motivo": f"ilegible: {type(e).__name__}"})
            continue
        inf = d["informacion"]
        d["_fichero"] = str(p.relative_to(ROOT))
        d["_dia"] = p.parent.name
        d["_fecha"] = fecha_iso(inf["fecha"])
        clave = (inf["sesion"], d["_fecha"], inf["numeroVotacion"])
        if clave in por_clave:
            calidad["duplicadas_misma_clave"] += 1
            duplicados.append({"fichero": d["_fichero"], "motivo": "misma sesión/fecha/número que " + por_clave[clave]["_fichero"]})
            continue
        por_clave[clave] = d
    return [por_clave[k] for k in sorted(por_clave)], {"contadores": dict(calidad), "descartes": duplicados}


def load_diputados() -> dict[str, dict]:
    """Nombre -> ficha pública (sin biografía) a partir de activos + de baja."""
    fichas: dict[str, dict] = {}
    dd = RAW / "diputados"
    for p in sorted(dd.glob("DiputadosActivos__*.json"))[-1:]:
        for r in json.loads(p.read_text(encoding="utf-8-sig")):
            fichas[r["NOMBRE"]] = {"circunscripcion": r["CIRCUNSCRIPCION"], "formacion_electoral": r["FORMACIONELECTORAL"],
                                   "fecha_alta": fecha_iso(r["FECHAALTA"]), "fecha_baja": None, "grupo_oficial": r["GRUPOPARLAMENTARIO"]}
    for p in sorted(dd.glob("DiputadosDeBaja__*.csv"))[-1:]:
        with p.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f, delimiter=";"):
                fichas[r["NOMBRE"]] = {"circunscripcion": r["CIRCUNSCRIPCION"], "formacion_electoral": r["FORMACIONELECTORAL"],
                                       "fecha_alta": fecha_iso(r["FECHAALTA"]), "fecha_baja": fecha_iso(r["FECHABAJA"]),
                                       "grupo_oficial": r["GRUPOPARLAMENTARIO"]}
    return fichas


def buscar_ficha(nombre: str, fichas: dict[str, dict]) -> tuple[dict | None, str | None]:
    """Cruce exacto por «Apellidos, Nombre»; si falla, por apellidos + primer nombre si es único."""
    if nombre in fichas:
        return fichas[nombre], None
    ap, _, nom = nombre.partition(", ")
    cand = [n for n in fichas if n.partition(", ")[0] == ap and n.partition(", ")[2].split(" ")[0] == nom.split(" ")[0]]
    if len(cand) == 1:
        return fichas[cand[0]], cand[0]
    return None, None


def votaciones_por_llamamiento() -> list[dict]:
    """Votaciones que la página del día muestra con totales pero sin fichero JSON/XML (públicas por
    llamamiento o secretas: el «Detalle» enlaza al Diario de Sesiones en PDF). Se recorren TODOS los días."""
    out = []
    for p in sorted((RAW / "html" / "dias").glob("*.html")):
        h = p.read_text(encoding="utf-8", errors="replace")
        body = h[h.find("cuerpo-votaciones"):h.find("agenda-col-2")]
        ses = re.search(r"Sesión Plenaria número (\d+)", body)
        for m in re.finditer(r'<div class="result_vot">(.*?)</div>', body, re.S):
            bloque = m.group(1)
            if ".json" in bloque or ".xml" in bloque:
                continue
            heads = re.findall(r'<h5 class="con_est">(.*?)</h5>', body[:m.start()], re.S)
            texto = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", heads[-1] if heads else "")).strip()
            tot = re.search(r"Si: (\d+).*?No: (\d+).*?Abstenciones: (\d+)", bloque, re.S)
            ds = re.search(r'href="(https://www\.congreso\.es/public_oficiales/[^"]+)"', bloque)
            out.append({"fecha": f"{p.stem[:4]}-{p.stem[4:6]}-{p.stem[6:8]}", "sesion": int(ses.group(1)) if ses else None,
                        "texto": texto, "si": int(tot.group(1)) if tot else None, "no": int(tot.group(2)) if tot else None,
                        "abstencion": int(tot.group(3)) if tot else None,
                        "tipo": "pública por llamamiento (totales en la web)" if tot else "sin totales en la web (elección/votación secreta)",
                        "voto_individual": "sólo en el Diario de Sesiones (PDF)" if tot else "no se publica",
                        "url_diario_sesiones": ds.group(1) if ds else None})
    return out


def meta(source: str, period: str, url: str, unit: str, note: str | None = None) -> dict:
    m = {"source": source, "period": period, "url": url, "unit": unit}
    if note:
        m["note"] = note
    return m


def main() -> None:
    DATA.mkdir(exist_ok=True)
    votaciones, calidad = load_raw()
    fichas = load_diputados()
    fetch_log = json.loads((RAW / "fetch_log.json").read_text())
    llamamiento = votaciones_por_llamamiento()
    # Verificación independiente: los totales que muestra la PÁGINA HTML del día (no el JSON) frente a
    # nuestro recuento de las filas nominales, para todas las votaciones con fichero.
    html_totales: dict[tuple, tuple] = {}
    for p in sorted((RAW / "html" / "dias").glob("*.html")):
        body = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'<div class="result_vot">(.*?)</div>', body, re.S):
            b = m.group(1)
            j = re.search(r"/(Sesion\d+)/(\d{8})/(Votacion\d+)/[^\"]+\.json", b)
            t = re.search(r"Si: (\d+).*?No: (\d+).*?Abstenciones: (\d+)", b, re.S)
            if j and t:
                html_totales[(int(j.group(1)[6:]), f"{j.group(2)[:4]}-{j.group(2)[4:6]}-{j.group(2)[6:]}", int(j.group(3)[8:]))] = tuple(map(int, t.groups()))
    fechas = sorted({v["_fecha"] for v in votaciones})
    periodo = f"{fechas[0]} / {fechas[-1]}"
    fuente = "Congreso de los Diputados, Datos abiertos: votaciones del Pleno"

    # --- clasificación de votaciones ---------------------------------------------------------
    nominales, asentimiento, sin_listado, descuadres = [], [], [], []
    for v in votaciones:
        t, rows = v["totales"], v["votaciones"]
        if t["asentimiento"] == "Sí":
            asentimiento.append(v); continue
        if not rows:
            sin_listado.append(v); continue
        c = Counter(r["voto"] for r in rows)
        rec = {"Sí": c["Sí"], "No": c["No"], "Abstención": c["Abstención"], "No vota": c["No vota"]}
        otros = {k: n for k, n in c.items() if k not in VOTOS}
        pub = {"Sí": t["afavor"], "No": t["enContra"], "Abstención": t["abstenciones"], "No vota": t["noVotan"]}
        if rec != pub or otros:
            descuadres.append({"sesion": v["informacion"]["sesion"], "fecha": v["_fecha"], "numero": v["informacion"]["numeroVotacion"],
                               "publicado": pub, "recuento": rec, "valores_no_previstos": otros})
        v["_recuento"] = rec
        nominales.append(v)

    comparadas = discrepancias = 0
    sin_html = []
    for v in nominales:
        k = (v["informacion"]["sesion"], v["_fecha"], v["informacion"]["numeroVotacion"])
        r = v["_recuento"]
        if k not in html_totales:
            sin_html.append(k); continue
        comparadas += 1
        if html_totales[k] != (r["Sí"], r["No"], r["Abstención"]):
            discrepancias += 1
    verif = {"metodo": "recuento propio de las filas nominales del JSON frente a los totales Sí/No/Abstenciones impresos en la página HTML del día",
             "votaciones_comparadas": comparadas, "discrepancias": discrepancias, "sin_bloque_html": [list(k) for k in sin_html]}

    # votaciones repetidas: mismo asunto con números consecutivos en la misma sesión (art. 88 Reglamento:
    # un empate obliga a repetir la votación). Se conservan todas; se listan.
    repetidas = []
    for a, b in zip(nominales, nominales[1:]):
        ia, ib = a["informacion"], b["informacion"]
        if (ia["sesion"] == ib["sesion"] and ib["numeroVotacion"] == ia["numeroVotacion"] + 1 and
                all(ia[k] == ib[k] for k in ("titulo", "textoExpediente", "tituloSubGrupo", "textoSubGrupo"))):
            repetidas.append({"sesion": ia["sesion"], "fecha": a["_fecha"], "numeros": [ia["numeroVotacion"], ib["numeroVotacion"]],
                              "texto": (ia["textoExpediente"] + " " + ia["textoSubGrupo"]).strip()[:160],
                              "primera": a["_recuento"], "segunda": b["_recuento"],
                              "empate_previo": a["_recuento"]["Sí"] == a["_recuento"]["No"]})

    # --- unificación de nombres --------------------------------------------------------------
    # El fichero identifica a cada diputado sólo por «Apellidos, Nombre» y esa cadena puede cambiar
    # con el tiempo (p.ej. «María» / «María del Socorro»). Dos nombres con los mismos apellidos y el
    # mismo primer nombre que NUNCA coinciden en una misma votación se consideran la misma persona;
    # si coinciden alguna vez, son homónimos y se mantienen separados.
    claves: dict[tuple, set[str]] = defaultdict(set)
    for v in nominales:
        for r in v["votaciones"]:
            ap, _, nom = r["diputado"].partition(", ")
            claves[(ap, nom.split(" ")[0])].add(r["diputado"])
    unificados, homonimos = {}, []
    for k, nombres in claves.items():
        if len(nombres) < 2:
            continue
        juntos = any(len(nombres & {r["diputado"] for r in v["votaciones"]}) > 1 for v in nominales)
        if juntos:
            homonimos.append(sorted(nombres)); continue
        canon = next((n for n in nombres if n in fichas), max(nombres, key=len))
        for n in nombres - {canon}:
            unificados[n] = canon
    for v in nominales:
        for r in v["votaciones"]:
            if r["diputado"] in unificados:
                r["diputado"] = unificados[r["diputado"]]

    # --- por diputado ---------------------------------------------------------------------------
    dip: dict[str, dict] = defaultdict(lambda: {"votos": Counter(), "podia": 0, "coinc": 0, "base_coinc": 0,
                                                 "grupos": Counter(), "primera": None, "ultima": None, "asientos": set(), "grupo_ultimo": ""})
    grupo_stats: dict[str, dict] = defaultdict(lambda: {"votaciones": 0, "sin_posicion": 0, "suma_cohesion": 0.0, "n_cohesion": 0})
    posiciones: list[dict[str, str]] = []  # por votación: grupo -> posición
    for v in nominales:
        rows = v["votaciones"]
        emitidos: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            if r["voto"] in ("Sí", "No", "Abstención") and r["grupo"]:
                emitidos[r["grupo"]][r["voto"]] += 1
        pos: dict[str, str] = {}
        for g, c in emitidos.items():
            top = c.most_common(2)
            grupo_stats[g]["votaciones"] += 1
            if len(top) > 1 and top[0][1] == top[1][1]:
                grupo_stats[g]["sin_posicion"] += 1
                continue
            pos[g] = top[0][0]
            grupo_stats[g]["suma_cohesion"] += top[0][1] / sum(c.values())
            grupo_stats[g]["n_cohesion"] += 1
        posiciones.append(pos)
        v["_pos"] = pos
        for r in rows:
            d = dip[r["diputado"]]
            d["podia"] += 1
            d["votos"][r["voto"]] += 1
            d["grupos"][r["grupo"]] += 1
            d["asientos"].add(r["asiento"])
            d["primera"] = min(d["primera"] or v["_fecha"], v["_fecha"])
            if d["ultima"] is None or v["_fecha"] >= d["ultima"]:
                d["ultima"], d["grupo_ultimo"] = v["_fecha"], r["grupo"]
            if r["voto"] in ("Sí", "No", "Abstención") and r["grupo"] in pos:
                d["base_coinc"] += 1
                d["coinc"] += r["voto"] == pos[r["grupo"]]

    n_nom = len(nominales)
    sin_ficha, cruces_alta_baja, alias_ficha = [], [], {}
    diputados_out = []
    for nombre, d in sorted(dip.items()):
        f, alias = buscar_ficha(nombre, fichas)
        if f is None:
            sin_ficha.append(nombre)
        if alias:
            alias_ficha[nombre] = alias
        emit = d["votos"]["Sí"] + d["votos"]["No"] + d["votos"]["Abstención"]
        grupos = [g or "(sin grupo)" for g, _ in d["grupos"].most_common()]  # orden: más votaciones primero
        fila = {
            "nombre": nombre,
            "grupo": d["grupo_ultimo"] or "(sin grupo)",
            "grupos": grupos if len(grupos) > 1 else None,
            "circunscripcion": f["circunscripcion"] if f else None,
            "formacion_electoral": f["formacion_electoral"] if f else None,
            "fecha_alta": f["fecha_alta"] if f else None,
            "fecha_baja": f["fecha_baja"] if f else None,
            "primera_votacion": d["primera"], "ultima_votacion": d["ultima"],
            "votaciones_posibles": d["podia"],
            "si": d["votos"]["Sí"], "no": d["votos"]["No"], "abstencion": d["votos"]["Abstención"], "no_vota": d["votos"]["No vota"],
            "asistencia_pct": round(100 * emit / d["podia"], 1) if d["podia"] else None,
            "coincidencia_base": d["base_coinc"],
            "coincidencia_pct": round(100 * d["coinc"] / d["base_coinc"], 1) if d["base_coinc"] else None,
            "votos_distintos_de_su_grupo": d["base_coinc"] - d["coinc"],
        }
        diputados_out.append(fila)
        if f and f["fecha_baja"] and d["ultima"] > f["fecha_baja"]:
            cruces_alta_baja.append({"nombre": nombre, "fecha_baja": f["fecha_baja"], "ultima_votacion": d["ultima"]})
        if f and d["primera"] < f["fecha_alta"]:
            cruces_alta_baja.append({"nombre": nombre, "fecha_alta": f["fecha_alta"], "primera_votacion": d["primera"]})

    usados = set(fichas) & set(dip) | set(alias_ficha.values())
    en_ficha_no_votos = [{"nombre": n, "fecha_alta": fichas[n]["fecha_alta"], "fecha_baja": fichas[n]["fecha_baja"]}
                         for n in sorted(fichas) if n not in usados]
    # homónimos: el fichero nominal identifica por nombre; se comprueba que ningún nombre esté repetido
    # en las fichas oficiales (activos + de baja) ni asociado a dos circunscripciones
    nombres_ficha = Counter(r["NOMBRE"] for p in sorted((RAW / "diputados").glob("DiputadosActivos__*.json"))[-1:]
                            for r in json.loads(p.read_text(encoding="utf-8-sig")))
    nombres_ficha.update(r["NOMBRE"] for p in sorted((RAW / "diputados").glob("DiputadosDeBaja__*.csv"))[-1:]
                         for r in csv.DictReader(p.open(encoding="utf-8-sig", newline=""), delimiter=";"))

    # --- grupos ------------------------------------------------------------------------------
    miembros_por_grupo = Counter()
    for d in dip.values():
        for g in d["grupos"]:
            if g:
                miembros_por_grupo[g] += 1
    grupos_out = []
    for g, s in sorted(grupo_stats.items(), key=lambda kv: -miembros_por_grupo[kv[0]]):
        grupos_out.append({
            "grupo": g, "nombre": GRUPO_NOMBRE.get(g, g), "diputados_que_han_pertenecido": miembros_por_grupo[g],
            "votaciones_con_votos_emitidos": s["votaciones"], "votaciones_sin_posicion_por_empate": s["sin_posicion"],
            "cohesion_media_pct": round(100 * s["suma_cohesion"] / s["n_cohesion"], 1) if s["n_cohesion"] else None,
        })

    # --- matriz entre grupos -----------------------------------------------------------------
    gl = [g["grupo"] for g in grupos_out]
    matriz = {a: {} for a in gl}
    for a, b in combinations(gl, 2):
        n = coinc = 0
        for pos in posiciones:
            if a in pos and b in pos:
                n += 1
                coinc += pos[a] == pos[b]
        val = {"votaciones_comparables": n, "coincidencia_pct": round(100 * coinc / n, 1) if n else None}
        matriz[a][b] = matriz[b][a] = val
    for a in gl:
        matriz[a][a] = {"votaciones_comparables": None, "coincidencia_pct": 100.0}

    # --- votaciones ajustadas ----------------------------------------------------------------
    def resumen(v: dict) -> dict:
        i, r = v["informacion"], v["_recuento"]
        return {"sesion": i["sesion"], "fecha": v["_fecha"], "numero": i["numeroVotacion"], "titulo": i["titulo"],
                "texto": i["textoExpediente"], "subgrupo": (i["tituloSubGrupo"] + " " + i["textoSubGrupo"]).strip() or None,
                "si": r["Sí"], "no": r["No"], "abstencion": r["Abstención"], "no_vota": r["No vota"],
                "margen": r["Sí"] - r["No"],
                "recuento": "empate" if r["Sí"] == r["No"] else ("más síes que noes" if r["Sí"] > r["No"] else "más noes que síes"),
                "url_dia": f"{OD_VOT}?p_p_id=votaciones&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view&targetLegislatura={LEG}&targetDate={v['_fecha'][8:]}/{v['_fecha'][5:7]}/{v['_fecha'][:4]}"}
    ajustadas = sorted(nominales, key=lambda v: (abs(v["_recuento"]["Sí"] - v["_recuento"]["No"]), -v["_recuento"]["Sí"]))[:25]
    empates = [v for v in nominales if v["_recuento"]["Sí"] == v["_recuento"]["No"]]
    aprobadas_min = sorted((v for v in nominales if v["_recuento"]["Sí"] > v["_recuento"]["No"]),
                           key=lambda v: v["_recuento"]["Sí"] - v["_recuento"]["No"])[:15]
    rechazadas_min = sorted((v for v in nominales if v["_recuento"]["No"] > v["_recuento"]["Sí"]),
                            key=lambda v: v["_recuento"]["No"] - v["_recuento"]["Sí"])[:15]
    margen_hist = Counter(min(abs(v["_recuento"]["Sí"] - v["_recuento"]["No"]) // 10 * 10, 200) for v in nominales)

    # --- escritura ---------------------------------------------------------------------------
    sesiones = sorted({(v["informacion"]["sesion"], v["_fecha"]) for v in votaciones})
    n_sesiones = len({s for s, _ in sesiones})
    censo = Counter(len(v["votaciones"]) for v in nominales)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    base_meta = meta(fuente, periodo, OD_VOT, "votaciones")

    def dump(name: str, obj: dict) -> None:
        obj = {"generated": generated, "legislatura": LEG, **obj}
        (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    dump("resumen.json", {
        "legislatura_inicio": "2023-08-17",
        "periodo": {"desde": fechas[0], "hasta": fechas[-1], "meta": base_meta},
        "votaciones_total": {"value": len(votaciones), "meta": base_meta},
        "votaciones_nominales": {"value": n_nom, "meta": meta(fuente, periodo, OD_VOT, "votaciones", "con listado de voto por diputado; base de todos los agregados")},
        "votaciones_asentimiento": {"value": len(asentimiento), "meta": meta(fuente, periodo, OD_VOT, "votaciones", "sin voto individual; excluidas de los agregados por diputado")},
        "sesiones_plenarias": {"value": n_sesiones, "meta": meta(fuente, periodo, OD_VOT, "sesiones")},
        "dias_con_votaciones": {"value": len(fechas), "meta": meta(fuente, periodo, OD_VOT, "días")},
        "diputados_con_votaciones": {"value": len(dip), "meta": meta(fuente, periodo, OD_VOT, "personas", "todos los que aparecen en algún listado nominal (incluye bajas y sustitutos)")},
        "escanos_por_votacion": {"value": dict(sorted(censo.items())), "meta": meta(fuente, periodo, OD_VOT, "votaciones por tamaño del listado")},
        "votos_emitidos": {"value": sum(v["_recuento"]["Sí"] + v["_recuento"]["No"] + v["_recuento"]["Abstención"] for v in nominales), "meta": meta(fuente, periodo, OD_VOT, "votos")},
        "no_vota": {"value": sum(v["_recuento"]["No vota"] for v in nominales), "meta": meta(fuente, periodo, OD_VOT, "votos", "categoría oficial «No vota»: ausencia o presencia sin voto; el Congreso no las distingue")},
        "histograma_margen": {"value": {f"{k}-{k+9}" if k < 200 else "200+": n for k, n in sorted(margen_hist.items())}, "meta": meta(fuente, periodo, OD_VOT, "votaciones por |sí−no|")},
    })
    dump("diputados.json", {
        "meta": meta(fuente, periodo, OD_VOT, "votos por persona",
                     "Denominador = votaciones en cuyo listado nominal aparece la persona. Circunscripción y fechas de alta/baja: " + OD_DIP),
        "definiciones": {
            "asistencia_pct": "votos emitidos (sí, no, abstención) / votaciones posibles × 100",
            "coincidencia_pct": "votos emitidos iguales a la posición mayoritaria de su grupo en esa votación / votos emitidos en votaciones donde el grupo tenía posición × 100",
            "no_vota": "categoría oficial del Congreso; incluye ausencia y presencia sin emitir voto",
        },
        "diputados": diputados_out,
    })
    dump("grupos.json", {
        "meta": meta(fuente, periodo, OD_VOT, "porcentaje", "cohesión = media, por votación, del porcentaje de votos emitidos del grupo que coinciden con su posición mayoritaria"),
        "grupos": grupos_out,
        "matriz_coincidencia": {"meta": meta(fuente, periodo, OD_VOT, "porcentaje de votaciones en que ambos grupos tuvieron la misma posición mayoritaria"), "grupos": gl, "valores": matriz},
    })
    dump("votaciones_ajustadas.json", {
        "meta": meta(fuente, periodo, OD_VOT, "votos", "margen = síes − noes del recuento nominal; no se infiere si la iniciativa prosperó: algunas requieren mayoría absoluta (176) u otras reglas"),
        "mas_ajustadas": [resumen(v) for v in ajustadas],
        "empates": [resumen(v) for v in empates],
        "mas_sies_por_menor_margen": [resumen(v) for v in aprobadas_min],
        "mas_noes_por_menor_margen": [resumen(v) for v in rechazadas_min],
    })
    dump("calidad.json", {
        "meta": meta(fuente, periodo, OD_VOT, "recuento", "todo lo descartado y por qué"),
        "descarga": {"dias_en_calendario": fetch_log["n_dias"], "ficheros_enlazados": fetch_log["n_links"],
                     "votaciones_con_fichero_listadas_en_html": len(html_totales),
                     "http": fetch_log["http"], "errores": fetch_log["errors"], "dias_sin_enlaces": fetch_log["dias_sin_enlaces"],
                     "tiempo_medio_s": fetch_log["timing_summary"]["mean"], "tiempo_max_s": fetch_log["timing_summary"]["max"]},
        "lectura": calidad["contadores"], "descartes": calidad["descartes"],
        "verificacion_html_vs_recuento": verif,
        "votaciones_por_llamamiento_sin_fichero": llamamiento,
        "votaciones_asentimiento": [{"sesion": v["informacion"]["sesion"], "fecha": v["_fecha"], "numero": v["informacion"]["numeroVotacion"], "titulo": v["informacion"]["titulo"]} for v in asentimiento],
        "votaciones_con_totales_sin_listado_nominal": [{"sesion": v["informacion"]["sesion"], "fecha": v["_fecha"], "numero": v["informacion"]["numeroVotacion"],
                                                       "titulo": v["informacion"]["titulo"], "totales": v["totales"]} for v in sin_listado],
        "descuadres_totales_vs_recuento": descuadres,
        "votaciones_repetidas_consecutivas": repetidas,
        "empates": [{"sesion": v["informacion"]["sesion"], "fecha": v["_fecha"], "numero": v["informacion"]["numeroVotacion"], "si": v["_recuento"]["Sí"], "no": v["_recuento"]["No"]} for v in empates],
        "diputados_sin_ficha_oficial": sin_ficha,
        "diputados_cruzados_por_alias": alias_ficha,
        "nombres_unificados_misma_persona": unificados,
        "homonimos_en_una_misma_votacion": homonimos,
        "diputados_en_ficha_sin_votaciones": en_ficha_no_votos,
        "nombres_repetidos_en_ficha": [n for n, c in nombres_ficha.items() if c > 1],
        "filas_sin_grupo": {n: c for n, c in ((d["nombre"], next((x for x in dip[d["nombre"]]["grupos"].items() if not x[0]), (None, 0))[1]) for d in diputados_out) if c},
        "diputados_con_cambio_de_grupo": [d["nombre"] for d in diputados_out if d["grupos"]],
        "incoherencias_alta_baja_vs_votaciones": cruces_alta_baja,
        "grupos_sin_posicion_por_empate": {g["grupo"]: g["votaciones_sin_posicion_por_empate"] for g in grupos_out},
    })
    (DATA / "sources.json").write_text(json.dumps({
        "generated": generated,
        "sources": [
            {"id": "congreso-votaciones", "name": fuente, "url": OD_VOT,
             "format": "HTML (calendario y enlaces) + JSON/XML por votación + ZIP por sesión", "license": "Datos abiertos del Congreso de los Diputados (reutilización con cita de la fuente)",
             "period": periodo, "accessed": generated[:10], "auth": "ninguna"},
            {"id": "congreso-diputados", "name": "Congreso de los Diputados, Datos abiertos: diputados (activos y de baja)", "url": OD_DIP,
             "format": "JSON/CSV", "license": "Datos abiertos del Congreso de los Diputados", "period": "legislatura XV", "accessed": generated[:10], "auth": "ninguna",
             "fields_used": ["NOMBRE", "CIRCUNSCRIPCION", "FORMACIONELECTORAL", "FECHAALTA", "FECHABAJA", "GRUPOPARLAMENTARIO"], "fields_excluded": ["BIOGRAFIA"]},
        ],
        "scripts": ["scripts/congreso_fetch.py", "scripts/congreso_build.py"],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"votaciones {len(votaciones)} (nominales {n_nom}, asentimiento {len(asentimiento)}, sin listado {len(sin_listado)}), "
          f"sesiones {n_sesiones}, días {len(fechas)}, diputados {len(dip)}, descuadres {len(descuadres)}, sin ficha {len(sin_ficha)}")


if __name__ == "__main__":
    main()
