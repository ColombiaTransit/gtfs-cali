import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE_URL = (
    "https://services9.arcgis.com/8rJ42n9yWry0I4K4/"
    "arcgis/rest/services/GTFS/FeatureServer"
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
    Discover both layers and tables.
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


def fetch_all_records(resource_id, include_geometry=False):
    """
    Download all rows using pagination.
    Works for both layers and tables.

    If include_geometry=True, x/y geometry is included.
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
                "returnGeometry": str(include_geometry).lower(),
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": max_record_count
            }
        )

        features = result.get("features", [])

        if not features:
            break

        for feature in features:
            row = feature.get("attributes", {}).copy()

            # Add geometry for stops
            if include_geometry:
                geom = feature.get("geometry", {}) or {}

                x = geom.get("x")
                y = geom.get("y")

                if x is not None:
                    row["stop_lon"] = x

                if y is not None:
                    row["stop_lat"] = y

            rows.append(row)

        print(
            f"Downloaded {len(rows)} rows "
            f"(resource={resource_id})"
        )

        if len(features) < max_record_count:
            break

        offset += max_record_count

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

        normalized_name = normalize_resource_name(name)

        # Stops layer needs x/y geometry
        include_geometry = normalized_name == "stops"
        
        df = fetch_all_records(
            resource["id"],
            include_geometry=include_geometry
        )

        if df.empty:
            print(f"Skipping empty {name}")
            continue

        df.columns = [c.lower() for c in df.columns]

        df = convert_dates(df)

        filename = normalized_name + ".txt"

        out_file = OUTPUT_DIR / filename

        df.to_csv(out_file, index=False)

        txt_files.append(out_file)

        print(f"Saved {out_file}")

    zip_path = OUTPUT_DIR / "gtfs.zip"

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

    print(f"Created {zip_path}")


def main():
    resources = discover_resources()

    print("Found resources:")

    for r in resources:
        print(
            f"- {r['type']}: "
            f"{r['name']} "
            f"(id={r['id']})"
        )

    export_gtfs(resources)


if __name__ == "__main__":
    main()
