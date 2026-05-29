import zipfile
from pathlib import Path
import pandas as pd
import requests
import re
from datetime import datetime

# -----------------------------
# SOURCES
# -----------------------------
BASE_URL = (
    "https://services9.arcgis.com/8rJ42n9yWry0I4K4/"
    "arcgis/rest/services/GTFS/FeatureServer"
)

STOPS_GEOJSON_URL = (
    "https://services9.arcgis.com/8rJ42n9yWry0I4K4/"
    "arcgis/rest/services/ptosparadas/FeatureServer/0/query"
    "?outFields=*&where=1%3D1&f=geojson"
)

RUTAS_GEOJSON_URL = (
    "https://services9.arcgis.com/8rJ42n9yWry0I4K4/"
    "arcgis/rest/services/rutas/FeatureServer/0/query"
    "?outFields=*&where=1%3D1&f=geojson"
)

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

SKIP_LAYERS = {"linevariantelements"}

session = requests.Session()

# -----------------------------
# GENERIC HELPERS
# -----------------------------
def get_json(url, params=None):
    r = session.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def discover_resources():
    meta = get_json(BASE_URL, {"f": "json"})
    resources = []

    for l in meta.get("layers", []):
        resources.append({"id": l["id"], "name": l["name"], "type": "layer"})

    for t in meta.get("tables", []):
        resources.append({"id": t["id"], "name": t["name"], "type": "table"})

    return resources


def fetch_all_records(resource_id):
    url = f"{BASE_URL}/{resource_id}"
    meta = get_json(url, {"f": "json"})
    max_rc = meta.get("maxRecordCount", 2000)

    offset = 0
    rows = []

    while True:
        res = get_json(
            f"{url}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": max_rc,
            },
        )

        feats = res.get("features", [])
        if not feats:
            break

        for f in feats:
            rows.append(f.get("attributes", {}))

        if len(feats) < max_rc:
            break

        offset += max_rc

    return pd.DataFrame(rows)


# -----------------------------
# STOPS (BASE + GEOJSON)
# -----------------------------
def fetch_stops_geojson():
    data = get_json(STOPS_GEOJSON_URL)

    rows = []
    for f in data.get("features", []):
        p = f.get("properties", {}) or {}

        rows.append({
            "stop_id": str(p.get("STOPID")).strip() if p.get("STOPID") else None,
            "stop_lat": p.get("LATITUD"),
            "stop_lon": p.get("LONGITUD"),
            "stop_desc": p.get("DIRECCION"),
        })

    return pd.DataFrame(rows)


def build_stops(base_df):
    df = base_df.copy()
    df.columns = [c.lower() for c in df.columns]

    df = df.rename(columns={
        "gstopid": "stop_id",
        "gstoptype": "location_type",
        "gstopparen": "parent_station",
        "gwheelchairboarding": "wheelchair_boarding",
    })

    df["stop_id"] = df["stop_id"].astype(str).str.strip()
    df["stop_name"] = df["stop_id"]

    geo = fetch_stops_geojson()

    df = df.merge(geo, on="stop_id", how="left")

    return df[[
        "stop_id",
        "stop_name",
        "stop_lat",
        "stop_lon",
        "stop_desc",
        "location_type",
        "parent_station",
        "wheelchair_boarding",
    ]]


# -----------------------------
# ROUTES + CALENDAR + SHAPES
# -----------------------------
def parse_operating(value):
    if not value:
        return False

    text = str(value).strip().upper()
    if "NO OPERA" in text:
        return False

    return True


def fetch_routes_geojson():
    return get_json(RUTAS_GEOJSON_URL)


def build_routes_calendar_shapes():
    data = fetch_routes_geojson()

    routes = []
    calendar = []
    shapes = []

    start_date = datetime.utcnow().strftime("%Y0101")
    end_date = datetime.utcnow().strftime("%Y1231")

    for f in data.get("features", []):
        p = f.get("properties", {}) or {}
        geom = f.get("geometry", {}) or {}

        ruta = str(p.get("RUTA")).strip()

        # ---------------- ROUTES ----------------
        routes.append({
            "route_id": ruta,
            "route_short_name": ruta,
            "route_desc": p.get("NOMBRE"),
            "route_type": p.get("ID_SERVICI"),
        })

        # ---------------- CALENDAR ----------------
        calendar.append({
            "service_id": ruta,
            "monday": int(parse_operating(p.get("HABIL"))),
            "tuesday": int(parse_operating(p.get("HABIL"))),
            "wednesday": int(parse_operating(p.get("HABIL"))),
            "thursday": int(parse_operating(p.get("HABIL"))),
            "friday": int(parse_operating(p.get("HABIL"))),
            "saturday": int(parse_operating(p.get("SABADO"))),
            "sunday": int(parse_operating(p.get("DOM_FEST"))),
            "start_date": start_date,
            "end_date": end_date,
        })

        # ---------------- SHAPES ----------------
        coords = geom.get("coordinates", [])

        seq = 1
        for c in coords:
            lon = c[0]
            lat = c[1]

            shapes.append({
                "shape_id": ruta,
                "shape_pt_lat": lat,
                "shape_pt_lon": lon,
                "shape_pt_sequence": seq,
            })

            seq += 1

    return (
        pd.DataFrame(routes).drop_duplicates(),
        pd.DataFrame(calendar).drop_duplicates(),
        pd.DataFrame(shapes),
    )


# -----------------------------
# GTFS EXPORT
# -----------------------------
def export_gtfs(resources):
    files = []

    for r in resources:
        name = r["name"]

        if name.lower() in SKIP_LAYERS:
            continue

        print("Processing", name)

        norm = name.lower().replace(" ", "_")

        df = fetch_all_records(r["id"])

        if df.empty:
            continue

        df.columns = [c.lower() for c in df.columns]

        if norm == "stops":
            df = build_stops(df)

        elif norm == "runs":
            df = df.rename(columns={
                "gtripid": "trip_id",
                "calendarid": "service_id",
                "gwheelchairaccessible": "wheelchair_accessible",
                "gbikesallowed": "bikes_allowed",
            })

        out = OUTPUT_DIR / f"{norm}.txt"
        df.to_csv(out, index=False)
        files.append(out)

    # routes/calendar/shapes
    routes, calendar, shapes = build_routes_calendar_shapes()

    routes_file = OUTPUT_DIR / "routes.txt"
    calendar_file = OUTPUT_DIR / "calendar.txt"
    shapes_file = OUTPUT_DIR / "shapes.txt"

    routes.to_csv(routes_file, index=False)
    calendar.to_csv(calendar_file, index=False)
    shapes.to_csv(shapes_file, index=False)

    files += [routes_file, calendar_file, shapes_file]

    # ZIP
    zip_path = OUTPUT_DIR / "gtfs.zip"

    with zipfile.ZipFile(zip_path, "w") as z:
        for f in files:
            z.write(f, arcname=f.name)

    print("GTFS created:", zip_path)


def main():
    resources = discover_resources()

    for r in resources:
        print(r)

    export_gtfs(resources)


if __name__ == "__main__":
    main()
