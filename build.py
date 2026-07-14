"""
build.py
---------
Reconstruye los archivos de datos que usa el mapa (carpeta data/) a partir
de los archivos GeoJSON exportados desde QGIS que se colocan en source/.

Este script lo corre automáticamente GitHub Actions cada vez que se sube
un archivo nuevo a source/. No hace falta ejecutarlo a mano ni entender
el código para usarlo: solo hay que exportar desde QGIS con los nombres
de archivo correctos (ver LEEME.md) y subirlos a source/.

Archivos esperados en source/:
  - barrios.geojson
  - inmuebles_fiscales.geojson
  - inmuebles_privados.geojson
  - zonificacion.geojson
"""
import json, re, os
from pyproj import Transformer

SOURCE = "source"
OUT = "data"
NSHARDS = 100

# Campos que NUNCA se publican porque identifican a personas
# (adjudicatario/ocupante/etc). Ver LEEME.md para el porqué.
CAMPOS_SENSIBLES = {
    "apellido_nombre", "posesion", "fecha_expte",
    "categoria", "categoria ", "propietario_tierras_fiscales", "codigo_pos",
}


def get_transformer(geojson):
    """Detecta el CRS del archivo y arma un transformador a WGS84 (lon/lat).
    Si el archivo ya está en WGS84 / CRS84, no transforma nada."""
    crs = geojson.get("crs", {}).get("properties", {}).get("name", "")
    m = re.search(r"EPSG::?(\d+)", crs)
    if not m:
        return None  # ya está en WGS84 / CRS84, o no declara CRS
    epsg = int(m.group(1))
    if epsg == 4326:
        return None
    return Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)


def reproject_coords(coords, transformer):
    if not coords:
        return coords
    if isinstance(coords[0], (int, float)):
        x, y = coords[0], coords[1]
        if transformer:
            x, y = transformer.transform(x, y)
        return [round(x, 6), round(y, 6)]
    return [reproject_coords(c, transformer) for c in coords]


def parse_zonificacion_desc(html):
    """Extrae los pares clave-valor de la tabla HTML que QGIS/KML embebe
    en el campo 'description' de la capa de zonificación."""
    if not html:
        return {}
    cells = re.findall(r"<td[^>]*>(.*?)</td>", html, re.S)
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
    pairs = cells[1:]
    result = {}
    for i in range(0, len(pairs) - 1, 2):
        key, val = pairs[i], pairs[i + 1]
        if key:
            result[key] = val
    return result


def build_barrios():
    path = os.path.join(SOURCE, "barrios.geojson")
    data = json.load(open(path, encoding="utf-8"))
    transformer = get_transformer(data)
    out = {"type": "FeatureCollection", "features": []}
    for f in data["features"]:
        p = f["properties"]
        geom = f["geometry"]
        if not geom or not geom.get("coordinates"):
            continue
        out["features"].append({
            "type": "Feature",
            "properties": {
                "barrio": p.get("barrio"),
                "circ": p.get("circ"),
                "sec": p.get("sec"),
            },
            "geometry": {
                "type": geom["type"],
                "coordinates": reproject_coords(geom["coordinates"], transformer),
            },
        })
    json.dump(out, open(os.path.join(OUT, "barrios_final.geojson"), "w", encoding="utf-8"),
               ensure_ascii=False)
    print(f"barrios: {len(out['features'])} features")


def build_zonificacion():
    path = os.path.join(SOURCE, "zonificacion.geojson")
    data = json.load(open(path, encoding="utf-8"))
    transformer = get_transformer(data)
    out = {"type": "FeatureCollection", "features": []}
    parsed = []
    for f in data["features"]:
        p = f["properties"]
        geom = f["geometry"]
        if not geom or not geom.get("coordinates"):
            continue
        d = parse_zonificacion_desc(p.get("description"))
        props = {
            "nombre": d.get("Nombre") or p.get("Name") or p.get("nombre"),
            "codigo": d.get("Codigo") or p.get("codigo"),
            "reglamento": d.get("REGLAMENT") or p.get("reglamento"),
            "fuente_url": d.get("Fuente") or p.get("fuente_url"),
            "uso_predominante": d.get("USO_AD-PRE") or p.get("uso_predominante"),
            "uso_complementario": d.get("USO_COMPLE") or p.get("uso_complementario"),
            "sup_lote_min": d.get("SUB_SUP_MI") or p.get("sup_lote_min"),
            "sup_lote_max": d.get("SUB_SUP_MA") or p.get("sup_lote_max"),
            "fos": d.get("IN-URB_FOS") or p.get("fos"),
            "fot": d.get("IN-URB_FOT") or p.get("fot"),
            "altura_max": d.get("IN-URB_HM") or p.get("altura_max"),
            "retiro": d.get("IN-URB_RET") or p.get("retiro"),
            "densidad_hab": d.get("DENS_HAB") or p.get("densidad_hab"),
            "superficie_ha": d.get("HA") or p.get("superficie_ha"),
        }
        parsed.append((props, geom))

    # Completar links faltantes: si otra zona con exactamente la misma
    # normativa ("reglamento") sí tiene un link http al Digesto cargado,
    # se usa ese mismo link. Esto no inventa fuentes: solo replica un
    # link que ya existe en el propio archivo para la misma ordenanza.
    from collections import Counter
    url_by_reglamento = {}
    for props, _ in parsed:
        reg = props.get("reglamento")
        url = props.get("fuente_url") or ""
        if reg and url.startswith("http"):
            url_by_reglamento.setdefault(reg, Counter())[url] += 1
    reglamento_best_url = {
        reg: counter.most_common(1)[0][0] for reg, counter in url_by_reglamento.items()
    }

    for props, geom in parsed:
        if not (props.get("fuente_url") or "").startswith("http"):
            reg = props.get("reglamento")
            if reg in reglamento_best_url:
                props["fuente_url"] = reglamento_best_url[reg]
        out["features"].append({
            "type": "Feature",
            "properties": props,
            "geometry": {
                "type": geom["type"],
                "coordinates": reproject_coords(geom["coordinates"], transformer),
            },
        })

    json.dump(out, open(os.path.join(OUT, "zonificacion_final.geojson"), "w", encoding="utf-8"),
               ensure_ascii=False)
    print(f"zonificacion: {len(out['features'])} features")


def build_nomenclatura(p):
    """Devuelve los 4 componentes catastrales relevantes por separado
    (se descartan departamento y ejido, que no se muestran en la web)."""
    return {
        "circunscripcion": p.get("circun"),
        "sector": p.get("sector"),
        "manzana": p.get("numero_div"),
        "parcela_cat": p.get("numero_par"),
    }


def build_parcelas():
    shards = {i: {"type": "FeatureCollection", "features": []} for i in range(NSHARDS)}

    for fname, dominio in [
        ("inmuebles_fiscales.geojson", "Fiscal"),
        ("inmuebles_privados.geojson", "Privado"),
    ]:
        path = os.path.join(SOURCE, fname)
        data = json.load(open(path, encoding="utf-8"))
        transformer = get_transformer(data)
        n = 0
        for f in data["features"]:
            p = {k: v for k, v in f["properties"].items() if k not in CAMPOS_SENSIBLES}
            geom = f["geometry"]
            if not geom or not geom.get("coordinates"):
                continue
            partida = p.get("partida")
            if partida is None:
                continue
            direccion = None
            if p.get("calles"):
                direccion = f"{p.get('calles')} {p.get('numero') or ''}".strip()
            props = {
                "partida": partida,
                "dominio": dominio,
                **build_nomenclatura(p),
                "barrio": p.get("barrios"),
                "direccion": direccion,
            }
            new_geom = {
                "type": geom["type"],
                "coordinates": reproject_coords(geom["coordinates"], transformer),
            }
            shard_id = int(partida) % NSHARDS
            shards[shard_id]["features"].append({
                "type": "Feature", "properties": props, "geometry": new_geom
            })
            n += 1
        print(f"{fname}: {n} features")

    for i in range(NSHARDS):
        out_path = os.path.join(OUT, f"parcelas_{i:02d}.geojson")
        json.dump(shards[i], open(out_path, "w", encoding="utf-8"),
                   ensure_ascii=False, separators=(",", ":"))
    total = sum(len(s["features"]) for s in shards.values())
    print(f"parcelas: {total} features en {NSHARDS} archivos")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    build_barrios()
    build_zonificacion()
    build_parcelas()
    print("Listo.")
