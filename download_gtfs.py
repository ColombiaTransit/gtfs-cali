import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE_URL = (
    "https://services9.arcgis.com/8rJ42n9yWry0I4K4/"
    "arcgis/rest/services/GTFS/FeatureServer"
)

STOPS_GEOJSON_URL = (
    "https://services9.arcgis.com/8rJ42n9yWry0I4K4/"
    "arcgis/rest/services/ptosparadas/FeatureServer/0/query"
    "?outFields=*&where=1%3D1&f=geojson"
)

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

SKIP_LAYERS = {
    "linevariantelements"
}

session = requests.Session()


def get_json(url, params=None):
    response = session.get(url, params=params, timeout=120)
    response.raise_for_status()
    return response.json()


def discover_resources():
    """
    Discover both layers and tables from the FeatureServer.
    """
    metadata = get_json(BASE_URL, {"f": "json"})

    resources = []

    for layer in metadata.get("layers", []):
        resources.append({
            "id": layer["id"],
            "name": layer["name"],
            "type": "layer"
        })

    for table in metadata.get("tables", []):
        resources.append({
            "id": table["id"],
            "name": table["name"],
            "type": "table"
        })

    return resources


def fetch_all_records(resource_id):
    """
    Download all rows using pagination.
    Works for both layers and tables.
    """
    resource_url = f"{BASE_URL}/{resource_id}"

    meta = get_json(resource_url, {"f": "json"})
    max_record_count = meta.get("maxRecordCount", 2000)

    offset = 0
    rows = []

    while True:
        result = get_json(
            f"{resource_url}/query",
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": max_record_count
            }
        )

        features = result.get("features", [])

        if not features:
            break

        rows.extend(
            feature.get("attributes", {})
            for feature in features
        )

        print(
            f"Downloaded {len(rows)} rows "
            f"(resource={resource_id})"
        )

        if len(features) < max_record_count:
            break

        offset += max_record_count

    return pd.DataFrame(rows)


def fetch_stops_geojson():
    """
    Download stop enrichment data from GeoJSON.
    """
    response = session.get(
        STOPS_GEOJSON_URL,
        timeout=120
    )
    response.raise_for_status()

    data = response.json()

    rows = []

    for feature in data.get("features", []):
        props = feature.get("properties", {}) or {}

        stop_id = props.get("STOPID")

        rows.append({
            "stop_id": str(stop_id).strip()
            if stop_id is not None
            else None,

            "stop_lat": props.get("LATITUD"),
            "stop_lon": props.get("LONGITUD"),
            "stop_desc": props.get("DIRECCION"),
        })

    return pd.DataFrame(rows)


def normalize_resource_name(name):
    """
    Convert ArcGIS names -> GTFS filenames.
    """
    cleaned = (
        name.lower()
        .replace(" ", "_")
        .replace("-", "_")
        .strip()
    )

    aliases = {
        "agency": "agency",
        "stops": "stops",
        "routes": "routes",
        "runs": "trips",
        "trips": "trips",
        "stop_times": "stop_times",
        "calendar": "calendar",
        "calendar_dates": "calendar_dates",
        "shapes": "shapes",
        "feed_info": "feed_info",
        "transfers": "transfers",
        "fare_attributes": "fare_attributes",
        "fare_rules": "fare_rules",
        "frequencies": "frequencies",
        "pathways": "pathways",
        "levels": "levels",
        "translations": "translations",
        "attributions": "attributions",
    }

    for key, value in aliases.items():
        if key in cleaned:
            return value

    return cleaned


def convert_dates(df):
    """
    Convert ArcGIS timestamps to GTFS YYYYMMDD.
    """
    for col in df.columns:
        lower = col.lower()

        if "date" not in lower:
            continue

        try:
            series = pd.to_datetime(
                df[col],
                errors="coerce"
            )

            if series.notna().any():
                df[col] = (
                    series.dt.strftime("%Y%m%d")
                )
        except Exception:
            pass

    return df


def apply_gtfs_column_mapping(df, filename):
    """
    Rename ArcGIS columns to GTFS-compliant names.
    """
    mappings = {
        "trips": {
            "gtripid": "trip_id",
            "calendarid": "service_id",
            "gwheelchairaccessible": "wheelchair_accessible",
            "gbikesallowed": "bikes_allowed",
        }
    }

    rename_map = mappings.get(filename, {})

    if rename_map:
        df = df.rename(columns=rename_map)

    return df


def build_stops_txt(base_df):
    """
    Build GTFS-compliant stops.txt

    Base source:
        FeatureServer stops layer

    Enrichment:
        GeoJSON stops feed
    """
    df = base_df.copy()

    df.columns = [c.lower() for c in df.columns]

    rename_map = {
        "gstopid": "stop_id",
        "gstoptype": "location_type",
        "gstopparen": "parent_station",
        "gwheelchairboarding": "wheelchair_boarding",
    }

    df = df.rename(columns=rename_map)

    # stop_name = GStopID
    if "stop_id" in df.columns:
        df["stop_name"] = (
            df["stop_id"]
            .astype(str)
            .str.strip()
        )

    # normalize join key
    df["stop_id"] = (
        df["stop_id"]
        .astype(str)
        .str.strip()
    )

    # enrich with GeoJSON
    geo_df = fetch_stops_geojson()

    df = df.merge(
        geo_df,
        on="stop_id",
        how="left"
    )

    # GTFS stops.txt order
    columns = [
        "stop_id",
        "stop_name",
        "stop_lat",
        "stop_lon",
        "stop_desc",
        "location_type",
        "parent_station",
        "wheelchair_boarding",
    ]

    existing_cols = [
        c for c in columns
        if c in df.columns
    ]

    return df[existing_cols]


def validate_gtfs_output(files_created):
    """
    Ensure required GTFS files exist.
    """
    required = [
        "stops",
        "routes",
        "trips",
    ]

    created_names = [
        p.stem for p in files_created
    ]

    missing = [
        f for f in required
        if f not in created_names
    ]

    if missing:
        raise RuntimeError(
            "Missing required GTFS files: "
            + ", ".join(missing)
        )


def export_gtfs(resources):
    txt_files = []

    for resource in resources:
        name = resource["name"]

        if name.lower() in SKIP_LAYERS:
            print(f"Skipping {name}")
            continue

        print(
            f"Processing "
            f"{resource['type']} "
            f"{name}"
        )

        normalized_name = (
            normalize_resource_name(name)
        )

        df = fetch_all_records(
            resource["id"]
        )

        if df.empty:
            print(
                f"Skipping empty {name}"
            )
            continue

        df.columns = [
            c.lower()
            for c in df.columns
        ]

        # Special handling for stops
        if normalized_name == "stops":
            print(
                "Building stops.txt "
                "from base layer + GeoJSON enrichment"
            )

            df = build_stops_txt(df)

        else:
            df = convert_dates(df)

            df = (
                apply_gtfs_column_mapping(
                    df,
                    normalized_name
                )
            )

        filename = (
            normalized_name
            + ".txt"
        )

        out_file = (
            OUTPUT_DIR / filename
        )

        df.to_csv(
            out_file,
            index=False
        )

        txt_files.append(
            out_file
        )

        print(
            f"Saved {out_file}"
        )

    validate_gtfs_output(
        txt_files
    )

    zip_path = (
        OUTPUT_DIR
        / "gtfs.zip"
    )

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as zf:
        for txt_file in txt_files:
            zf.write(
                txt_file,
                arcname=txt_file.name
            )

    print(
        f"Created {zip_path}"
    )


def main():
    resources = discover_resources()

    print(
        "Found resources:"
    )

    for r in resources:
        print(
            f"- {r['type']}: "
            f"{r['name']} "
            f"(id={r['id']})"
        )

    export_gtfs(resources)


if __name__ == "__main__":
    main()
