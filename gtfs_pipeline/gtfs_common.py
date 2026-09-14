"""
Shared constants and helpers for the Metro Cali GTFS pipeline.
"""
import os
import re
import unicodedata

ITEM_ID = "163af9c688444778b74150cd84f64a8b"
ARCGIS_ITEM_URL = f"https://www.arcgis.com/sharing/rest/content/items/{ITEM_ID}?f=json"

# Confirmed live endpoint (from https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/GTFS/FeatureServer/layers).
# download.py resolves the item dynamically first and falls back to this if that fails,
# since Metro Cali could repoint the item to a new service in a future "vigencia".
FEATURE_SERVICE_URL = "https://services9.arcgis.com/8rJ42n9yWry0I4K4/arcgis/rest/services/GTFS/FeatureServer"

# This service is NOT one-layer-per-GTFS-file. It's a relational vehicle-schedule
# model (pattern + timing-offset + calendar), so layer IDs are addressed explicitly
# rather than discovered by name-matching.
LAYER_IDS = {
    "Stops": 0,                 # point geometry
    "LineVariantElements": 1,   # polyline geometry (route-pattern segments)
    "Calendars": 2,
    "CalendarExceptions": 3,
    "Lines": 4,
    "LineVariants": 5,
    "Runs": 6,
    "ScheduleElements": 7,
    "Schedules": 8,
}

RAW_DIR = "build/gtfs_raw"
CLEAN_DIR = "build/gtfs_clean"
REPORT_DIR = "build/report"
FINAL_ZIP = "build/gtfs.zip"

# Rough bounding box for Santiago de Cali, Colombia (sanity check only,
# generous margin to allow for suburban routes / terminals).
CALI_BBOX = {"lat_min": 3.05, "lat_max": 3.70, "lon_min": -76.75, "lon_max": -76.35}

# --- Manual overrides -------------------------------------------------------
# The source schema has no Agency table at all, and Stops/Lines carry no
# human-readable name fields. These fill the gaps. Edit if Metro Cali confirms
# different values (contact: sistemas@metrocali.gov.co).
KNOWN_AGENCY = {
    "agency_id": "MIO",
    "agency_name": "Metro Cali S.A. - Sistema Integrado de Transporte Publico MIO",
    "agency_url": "https://www.metrocali.gov.co",
    "agency_timezone": "America/Bogota",
    "agency_lang": "es",
}

# download.py auto-detects these from the live data (see diagnostics printed at
# runtime). Leave as "auto" until you've confirmed the detection is correct,
# then you can hard-pin here to skip re-detection on every run.
#   TIME_UNIT_OVERRIDE: "seconds" | "minutes" | "hours" | None (= auto-detect)
#   SCHEDULE_ALIGNMENT_OVERRIDE: "per_stop" | "per_segment" | None (= auto-detect)
TIME_UNIT_OVERRIDE = None
SCHEDULE_ALIGNMENT_OVERRIDE = None
# -----------------------------------------------------------------------------

# Canonical GTFS file -> canonical column order (per gtfs.org/schedule/reference).
# Only the "core" columns are listed; any extra columns found in the source
# are appended after these, preserved rather than dropped.
GTFS_SCHEMA = {
    "agency.txt": [
        "agency_id", "agency_name", "agency_url", "agency_timezone",
        "agency_lang", "agency_phone", "agency_fare_url", "agency_email",
    ],
    "stops.txt": [
        "stop_id", "stop_code", "stop_name", "stop_desc", "stop_lat", "stop_lon",
        "zone_id", "stop_url", "location_type", "parent_station", "stop_timezone",
        "wheelchair_boarding", "level_id", "platform_code",
    ],
    "routes.txt": [
        "route_id", "agency_id", "route_short_name", "route_long_name",
        "route_desc", "route_type", "route_url", "route_color",
        "route_text_color", "route_sort_order",
    ],
    "trips.txt": [
        "route_id", "service_id", "trip_id", "trip_headsign", "trip_short_name",
        "direction_id", "block_id", "shape_id", "wheelchair_accessible",
        "bikes_allowed",
    ],
    "stop_times.txt": [
        "trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence",
        "stop_headsign", "pickup_type", "drop_off_type", "shape_dist_traveled",
        "timepoint",
    ],
    "calendar.txt": [
        "service_id", "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday", "start_date", "end_date",
    ],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
    "fare_attributes.txt": [
        "fare_id", "price", "currency_type", "payment_method", "transfers",
        "agency_id", "transfer_duration",
    ],
    "fare_rules.txt": [
        "fare_id", "route_id", "origin_id", "destination_id", "contains_id",
    ],
    "shapes.txt": [
        "shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence",
        "shape_dist_traveled",
    ],
    "frequencies.txt": [
        "trip_id", "start_time", "end_time", "headway_secs", "exact_times",
    ],
    "transfers.txt": [
        "from_stop_id", "to_stop_id", "transfer_type", "min_transfer_time",
    ],
    "feed_info.txt": [
        "feed_publisher_name", "feed_publisher_url", "feed_lang",
        "default_lang", "feed_start_date", "feed_end_date", "feed_version",
        "feed_contact_email", "feed_contact_url",
    ],
    "attributions.txt": [
        "attribution_id", "agency_id", "route_id", "trip_id", "organization_name",
        "is_producer", "is_operator", "is_authority", "attribution_url",
        "attribution_email", "attribution_phone",
    ],
}

REQUIRED_FILES = ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"]
# GTFS requires at least one of these two to define service days
CALENDAR_FILES = ["calendar.txt", "calendar_dates.txt"]


def normalize_name(name: str) -> str:
    """lowercase, strip accents/diacritics, keep only a-z0-9."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


def ensure_dirs():
    for d in (RAW_DIR, CLEAN_DIR, REPORT_DIR):
        os.makedirs(d, exist_ok=True)

