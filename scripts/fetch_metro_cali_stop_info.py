#!/usr/bin/env python3

import csv
import io
import json
import sys
import time
import zipfile

from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
)


BASE_URL = (
    "https://metrocali.gov.co"
    "/newmioapp/mioapp/views/rutas/api/luminosV2.php"
)

REFERER = (
    "https://metrocali.gov.co/"
    "newmioapp/mioapp/views/rutas/index.html"
)

OUTPUT_DIR = Path("tmp/metro_cali_stop_info")

# Small delay between requests.
# This is intentionally conservative.
REQUEST_DELAY_SECONDS = 0.25

REQUEST_TIMEOUT_SECONDS = 30

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36"
)


def read_stop_ids_from_zip(zip_path):
    """
    Read all unique stop_id values from stops.txt inside data/gtfs.zip.
    """

    with zipfile.ZipFile(zip_path, "r") as archive:

        stops_name = next(
            (
                name
                for name in archive.namelist()
                if Path(name).name.lower() == "stops.txt"
            ),
            None,
        )

        if stops_name is None:
            raise RuntimeError(
                f"stops.txt was not found inside {zip_path}"
            )

        print(f"Reading stops from: {stops_name}")

        with archive.open(stops_name) as raw:

            text = io.TextIOWrapper(
                raw,
                encoding="utf-8-sig",
                newline="",
            )

            reader = csv.DictReader(text)

            if not reader.fieldnames:
                raise RuntimeError(
                    "stops.txt has no header"
                )

            if "stop_id" not in reader.fieldnames:
                raise RuntimeError(
                    "stops.txt does not contain a stop_id column. "
                    f"Found: {reader.fieldnames}"
                )

            stop_ids = []

            for row in reader:

                stop_id = str(
                    row.get("stop_id", "")
                ).strip()

                if stop_id:
                    stop_ids.append(stop_id)

    # Remove duplicates while preserving original order.
    return list(dict.fromkeys(stop_ids))


def create_session():
    """
    Create an HTTP opener with cookie support.

    First visit the Metro Cali route page so a fresh PHP session can
    be established if required.
    """

    cookie_jar = CookieJar()

    opener = build_opener(
        HTTPCookieProcessor(cookie_jar)
    )

    request = Request(
        REFERER,
        headers={
            "Accept": "*/*",
            "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    print("Establishing Metro Cali session...")

    with opener.open(
        request,
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:
        response.read()

    print(
        f"Session established. "
        f"Cookies received: {len(list(cookie_jar))}"
    )

    return opener


def fetch_stop(opener, stop_id):
    """
    Fetch the live information for one Metro Cali stop.
    """

    query = urlencode(
        {
            "numeroParada": stop_id,
        }
    )

    url = f"{BASE_URL}?{query}"

    request = Request(
        url,
        headers={
            "Accept": "*/*",
            "Accept-Language": "es-CO,es;q=0.9,en;q=0.8",
            "Referer": REFERER,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )

    try:

        with opener.open(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:

            body = response.read().decode(
                "utf-8",
                errors="replace",
            )

            return {
                "http_status": response.status,
                "content_type": response.headers.get(
                    "Content-Type",
                    "",
                ),
                "body": body,
                "error": None,
            }

    except HTTPError as exc:

        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        return {
            "http_status": exc.code,
            "content_type": exc.headers.get(
                "Content-Type",
                "",
            ),
            "body": body,
            "error": f"HTTP {exc.code}",
        }

    except URLError as exc:

        return {
            "http_status": None,
            "content_type": None,
            "body": "",
            "error": str(exc.reason),
        }

    except Exception as exc:

        return {
            "http_status": None,
            "content_type": None,
            "body": "",
            "error": repr(exc),
        }


def try_parse_json(body):
    """
    Parse the response as JSON when possible.

    Keep the original response if it is not valid JSON.
    """

    if not body:
        return None

    try:
        return json.loads(body)

    except json.JSONDecodeError:
        return None


def main():

    if len(sys.argv) != 2:

        print(
            f"Usage: {sys.argv[0]} data/gtfs.zip",
            file=sys.stderr,
        )

        return 2

    zip_path = Path(sys.argv[1])

    if not zip_path.exists():

        print(
            f"ERROR: GTFS ZIP does not exist: {zip_path}",
            file=sys.stderr,
        )

        return 1

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    stop_ids = read_stop_ids_from_zip(zip_path)

    total = len(stop_ids)

    print()
    print(f"GTFS ZIP:     {zip_path}")
    print(f"Unique stops: {total}")
    print()

    opener = create_session()

    started_at = datetime.now(timezone.utc)

    observations_file = (
        OUTPUT_DIR / "observations.jsonl"
    )

    successful = 0
    failed = 0

    with observations_file.open(
        "w",
        encoding="utf-8",
    ) as output:

        for index, stop_id in enumerate(
            stop_ids,
            start=1,
        ):

            observed_at = datetime.now(
                timezone.utc
            ).isoformat()

            print(
                f"[{index}/{total}] "
                f"Fetching numeroParada={stop_id}",
                flush=True,
            )

            result = fetch_stop(
                opener,
                stop_id,
            )

            parsed_data = try_parse_json(
                result["body"]
            )

            record = {
                "observed_at": observed_at,
                "stop_id": stop_id,
                "numeroParada": stop_id,
                "url": (
                    f"{BASE_URL}"
                    f"?numeroParada={stop_id}"
                ),
                "http_status": result["http_status"],
                "content_type": result["content_type"],
                "error": result["error"],
                "data": parsed_data,
                "raw_response": (
                    None
                    if parsed_data is not None
                    else result["body"]
                ),
            }

            output.write(
                json.dumps(
                    record,
                    ensure_ascii=False,
                )
                + "\n"
            )

            output.flush()

            if (
                result["http_status"] is not None
                and 200 <= result["http_status"] < 300
            ):
                successful += 1
            else:
                failed += 1

                print(
                    f"  ERROR: "
                    f"{result['error'] or result['http_status']}",
                    flush=True,
                )

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    finished_at = datetime.now(timezone.utc)

    summary = {
        "gtfs_zip": str(zip_path),
        "total_stops": total,
        "successful": successful,
        "failed": failed,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "request_delay_seconds": REQUEST_DELAY_SECONDS,
        "output_file": str(observations_file),
    }

    summary_file = (
        OUTPUT_DIR / "summary.json"
    )

    with summary_file.open(
        "w",
        encoding="utf-8",
    ) as output:

        json.dump(
            summary,
            output,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("=" * 50)
    print("Metro Cali stop information fetch complete")
    print("=" * 50)
    print(f"Total stops: {total}")
    print(f"Successful:  {successful}")
    print(f"Failed:      {failed}")
    print(f"Output:      {observations_file}")
    print("=" * 50)

    # Do not fail the complete workflow merely because individual
    # stops failed. We want to inspect all collected responses.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
