# Informe de datos — «¿Cómo vota tu diputado?» (cci-12)

Generado el 2026-09-30 con `scripts/congreso_fetch.py` + `scripts/congreso_build.py`. Todos los números de este informe salen de `data/*.json` y `raw/fetch_log.json`; ninguno está escrito a mano.

## 1. Punto de entrada real (medido)

| Qué | Dónde | Formato |
|---|---|---|
| Calendario de días con votaciones | `https://www.congreso.es/es/opendata/votaciones` (HTML, portal Liferay) | `var diasVotaciones = [YYYYMMDD, …]` embebido en la página; `<select>` de legislatura (X–XV) |
| Listado de un día | misma URL + `?p_p_id=votaciones&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view&targetLegislatura=XV&targetDate=DD/MM/YYYY` | HTML con un bloque `result_vot` por votación: totales Sí/No/Abstenciones y enlaces PDF/XML/JSON/PNG; un ZIP por sesión y día |
| Una votación | `/webpublica/opendata/votaciones/Leg15/SesionNNN/YYYYMMDD/VotacionNNN/VOT_<timestamp>.json` | `{informacion, totales, votaciones:[{asiento, diputado, grupo, voto}]}`; el XML es idéntico (ISO-8859-1) |
| Diputados | `https://www.congreso.es/es/opendata/diputados` | `DiputadosActivos__*.json` (350) y `DiputadosDeBaja__*.csv` (62) con `FECHAALTA`/`FECHABAJA`, circunscripción, grupo y **biografía (no se usa)** |

- **No hay API ni paginación**: son ficheros estáticos. No hace falta clave.
- **Sin límite de tasa detectado**: 2 303 peticiones con 4 hilos, todas `200`, tiempo medio 0,235 s, máximo 8,6 s, 0 errores (`raw/fetch_log.json`). Descarga completa en 140 s; releer desde caché, 13 s.
- **Trampa medida**: el WAF devuelve `403` si el `User-Agent` contiene una URL (`como-vota/1.0 (…; github.com/…)`); con `como-vota/1.0` responde `200`.
- Volumen: 92 MB en `raw/` (2 154 JSON de ~33 KB + 145 HTML + ficheros de diputados). No se usaron los ZIP (llevan PDF y PNG, ~250 KB por votación).

## 2. Cobertura

- **Legislatura XV**, constituida el 17/08/2023 (`minCalendarDate` de la página = 1692223200000 ms = 2023-08-17). Primera votación con fichero: **19/09/2023**; última: **29/09/2026**.
- Calendario: **145 días** con votaciones → **141 días con ficheros** (los 4 restantes sólo tienen votaciones públicas por llamamiento, ver §5).
- **143 sesiones plenarias** (ninguna sesión reparte votaciones en dos días; dos días acumulan dos sesiones: 14/03/2024 y 26/03/2026).
- **2 154 votaciones con fichero** = 2 154 bloques con enlace en el HTML (cotejado). De ellas **2 153 con listado nominal** y 1 con totales pero sin listado (sesión 89, 22/01/2025, nº 5: dictamen sobre un suplicatorio; `noVotan` 5, sin filas).
- **0 votaciones por asentimiento** en los ficheros (`asentimiento` siempre «No»).
- **753 518 filas nominales**: 745 646 votos emitidos (Sí 345 848 · No 329 554 · Abstención 70 244) y 7 872 «No vota».
- Tamaño del listado: 350 escaños en 2 121 votaciones y **349 en 32** (escaño vacante entre una baja y su sustitución: 09/04/2024, 24 y 26/09/2024, 17/06/2025).
- **410 diputados** con al menos una votación. La ficha oficial suma 412 (350 activos + 62 de baja); los 2 restantes causaron baja antes de la primera votación (Batet Lamaña, baja 06/09/2023; Marín González, baja 14/09/2023).

## 3. Verificación

1. **Recuento propio vs. página del Congreso**: para las 2 153 votaciones nominales se recontaron las filas y se compararon con los totales Sí/No/Abstenciones impresos en la página HTML del día (fuente distinta del bloque `totales` del JSON): **2 153 comparadas, 0 discrepancias** (`calidad.json → verificacion_html_vs_recuento`). También 0 descuadres entre filas y `totales` del propio JSON.
2. **Votaciones concretas** (nuestro recuento = lo publicado):
   - Ley Orgánica de amnistía, votación del dictamen, 30/01/2024, sesión 18 nº 9: **177 sí, 172 no, 0 abst., 1 no vota**.
   - Nuevo dictamen de la misma ley, 14/03/2024, sesión 30 nº 1: **178 sí, 172 no**.
   - Investidura de Pedro Sánchez, 16/11/2023: **179 sí, 171 no** — sólo totales en la web (pública por llamamiento, ver §5). Reforma del art. 49 CE, 18/01/2024: **312 sí, 32 no**, mismo caso.
   - Sesión 15, 10/01/2024: tres asuntos consecutivos empatados a **171–171** (nº 4 Real Decreto-ley 8/2023, nº 5 objetivos de estabilidad, nº 6 Plan de Reequilibrio), cada uno resuelto después con votación pública por llamamiento que sólo tiene totales en la web (172–171–7, 179–171 y 179–171). Los números 8 y 9 de esa sesión no tienen fichero.

## 4. Cómo se tratan altas, bajas, ausencias y voto telemático

- **Denominador por persona = votaciones en cuyo listado aparece**. El listado de cada votación es el censo de escaños de ese día, así que quien entra o sale a mitad de legislatura queda medido sólo sobre lo que podía votar (ej.: Ábalos Meco 1 637 posibles; Cuesta Rodríguez 2 153). No se interpola con fechas.
- **«No vota»** es la categoría oficial del Congreso e incluye tanto ausencia como presencia sin emitir voto. El fichero no las distingue: se publica como «no vota», nunca como «ausencia». `asistencia_pct` = votos emitidos / posibles.
- **Voto telemático**: ni el JSON ni el XML tienen ningún campo que lo marque (campos exactos: `asiento, diputado, grupo, voto`); se integra en Sí/No/Abstención. No se puede separar y no se intenta.
- **Grupo**: se toma el que figura en cada votación, así que los cambios de grupo (9 personas: Belarra, Micó, Ortega Smith, Santana, Sánchez Serna, Velarde, Verstrynge, Ábalos; y Conesa/Santana con 2 filas sin grupo en la sesión 2) se miden contra el grupo de ese momento. `grupo` en `diputados.json` = grupo de su última votación; `grupos` lista todos.
- **Posición mayoritaria del grupo** = opción más votada entre los votos emitidos del grupo en esa votación; con empate no hay posición (36 casos, todos del Grupo Mixto). **Coincidencia** = votos emitidos iguales a esa posición / votos emitidos con posición definida. Los «no vota» no cuentan ni a favor ni en contra.
- **Grupo Mixto**: cohesión media 80,1 % frente a 99,6–100 % del resto; es un cajón de formaciones distintas, y su «posición mayoritaria» no significa lo mismo. Conviene tratarlo aparte en la web.

## 5. Lo que no está y por qué

- **23 votaciones sin fichero nominal** (`calidad.json → votaciones_por_llamamiento_sin_fichero`), detectadas recorriendo todos los bloques HTML sin enlace JSON/XML:
  - **13 públicas por llamamiento** con totales en la web pero voto individual sólo en el Diario de Sesiones (PDF): las dos investiduras de Feijóo (172–178 y 172–177), la de Sánchez (179–171), la reforma del art. 49 CE (312–32), cuatro votaciones de la ley de amnistía (12/12/2023 178–172; 10/01/2024 171–178; 30/01/2024 171–179; 30/05/2024 177–172), el desenlace del RDL 8/2023 y dos acuerdos de estabilidad (10/01/2024, 179–171).
  - **10 sin totales** (elecciones y votaciones secretas: vocales del CGPJ, consejo y presidencia de RTVE, elección de diputados para la Mesa).
  - El ZIP de esos días sólo contiene un PNG. Alternativa si se quisiera el voto individual: transcribir el PDF del Diario de Sesiones (enlaces guardados). No se ha hecho: quedan fuera de todos los agregados por diputado y se muestran como huecos declarados.
- **1 votación con totales y sin filas** (suplicatorio, 22/01/2025): fuera de los agregados por persona.
- No se descarta nada más: 0 ficheros ilegibles, 0 claves duplicadas.

## 6. Trampas encontradas

- **Empates y repeticiones** (art. 88 del Reglamento): 19 empates; 15 pares de votaciones consecutivas con texto idéntico, todos precedidos de empate (p. ej. sesión 70, 23/10/2024: cuatro empates 172–172 seguidos, nº 6 a 9). Se conservan todas como votaciones distintas, así que una moción empatada pesa varias veces en el recuento total; están listadas en `calidad.json` para que la web pueda señalarlas.
- **Huecos de numeración**: 8 números de votación sin fichero en 6 sesiones (p. ej. 15/8-9, 80/1-2); coinciden con las votaciones por llamamiento o secretas de esos días. No son descargas fallidas (0 errores HTTP; los enlaces no existen en el HTML).
- **Totalidad vs. enmiendas**: una misma iniciativa genera muchas votaciones (enmiendas, «resto de las enmiendas», votación por puntos, dictamen). `titulo`/`textoExpediente`/`textoSubGrupo` permiten distinguirlas pero no hay identificador de expediente en el JSON (el `Núm. expte.` sólo está en el HTML).
- **Resultado no inferible**: el fichero no dice si la iniciativa prosperó. Las leyes orgánicas requieren 176 síes y hay reglas especiales; `votaciones_ajustadas.json` publica sólo el margen síes−noes y el recuento.
- **Correcciones posteriores del sentido del voto**: no hay ningún campo de corrección ni versión en JSON/XML; cada votación tiene un único fichero con `timestamp` en el nombre. No se puede saber si un fichero fue regenerado. Si el Congreso corrige, la única señal sería un cambio del enlace (bastaría reejecutar con `--force`).
- **Grupos con menos votaciones** (Junts 2 132, PNV 2 127 de 2 153): son votaciones en que ningún miembro emitió voto; no cuentan en su cohesión ni en la matriz.
- `asistencia_pct` más baja: Sánchez Pérez-Castejón 48,1 %, Ábalos Meco 66,6 %, Díaz Pérez 68,0 %, Núñez Feijóo 72,4 %. Es un dato de «no vota» oficial, sin causa; la web debe decirlo así.

## 7. Ficheros

- `data/resumen.json` — totales, periodo, histograma de márgenes.
- `data/diputados.json` — 410 filas: nombre, grupo(s), circunscripción, formación, alta/baja, posibles, sí/no/abstención/no vota, `asistencia_pct`, `coincidencia_pct`, base y nº de votos distintos del grupo.
- `data/grupos.json` — cohesión media por grupo y `matriz_coincidencia` 9×9 (con nº de votaciones comparables por par).
- `data/votaciones_ajustadas.json` — 25 más ajustadas, 19 empates, 15 con más síes por menor margen, 15 con más noes por menor margen; cada una con URL del día.
- `data/calidad.json` — todo lo anterior en cifras: descarga, verificación, descartes, llamamientos, repeticiones, alias, cambios de grupo.
- `data/sources.json` — fuentes, formato, campos usados y excluidos (`BIOGRAFIA`).
