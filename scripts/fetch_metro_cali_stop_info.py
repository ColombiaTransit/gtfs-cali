#!/usr/bin/env python3

import csv
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar


BASE_URL = (
    "https://metrocali.gov.co"
    "/newmioapp/mioapp/views/rutas/api/luminosV2.php"
)

REFERER = (
    "https://metrocali.gov.co/"
    "newmioapp/mioapp/views/rutas/index.html"
)

OUTPUT_DIR = Path("tmp/metro_cali_stop_info")

REQUEST_DELAY_SECONDS = 0.25

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
)


def read_stop_ids_from_zip(zip_path: Path) -> list[str]:
    """Read unique stop_id values from stops.txt inside gtfs.zip."""

    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()

        stops_name = next(
            (
                name
                for name in names
                if Path(name).name.lower() == "stops.txt"
            ),
            None,
        )

        if stops_name is None:
            raise RuntimeError(
                "stops.txt was not found inside "
                f"{zip_path}"
            )

        print(f"Reading: {stops_name}")

        with z.open(stops_name) as raw:
            data = io.TextIOWrapper(
                raw,
                encoding="utf-8-sig",
                newline="",
            )

            reader = csv.DictReader(data)

            if not reader.fieldnames:
                raise RuntimeError(
                    "stops.txt has no header"
                )

            if "stop_id" not in reader.fieldnames:
                raise RuntimeError(
                    "stops.txt does not contain stop_id. "
                    f"Columns: {reader.fieldnames}"
                )

            stop_ids = []

            for row in reader:
                stop_id = str(
                    row.get("stop_id", "")
                ).strip()

                if stop_id:
                    stop_ids.append(stop_id)

    # Remove duplicate stop IDs while preserving order.
    return list(dict.fromkeys(stop_ids))


def create_session():
    """
    Establish a fresh PHP session with Metro Cali.

    We deliberately do not use the PHPSESSID from the user's curl
    example because that is a browser/session-specific cookie.
    """

    cookie_jar = CookieJar()

    opener = build_opener(
        HTTPCookieProcessor(cookie_jar)
    )

    headers = {
        "Accept": "*/*",
        "Accept-Language": (
            "nl,en;q=0.9,en-GB;q=0.8,"
            "en-US;q=0.7,es;q=0.6,nl-NL;q=0.5"
        ),
        "Referer": REFERER,
        "User-Agent": USER_AGENT,
    }

    request = Request(
        REFERER,
        headers=headers,
        method="GET",
    )

    print("Establishing Metro Cali session...")

    with opener.open(
        request,
        timeout=30,
    ) as response:
        response.read()

    cookies = list(cookie_jar)

    print(
        f"Session established "
        f"({len(cookies)} cookie(s))."
    )

    return opener


def fetch_stop(opener, stop_id: str) -> dict:
    """Fetch one stop using numeroParada=stop_id."""

    url = (
        f"{BASE_URL}"
        f"?numeroParada={stop_id}"
    )

    headers = {
        "Accept": "*/*",
        "Accept-Language": (
            "nl,en;q=0.9,en-GB;q=0.8,"
            "en-US;q=0.7,es;q=0.6,nl-NL;q=0.5"
        ),
        "Priority": "u=1, i",
        "Referer": REFERER,
        "Sec-CH-UA": (
            '"Chromium";v="152", '
            '"Not?A_Brand";v="24", '
            '"Microsoft Edge";v="152"'
        ),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": USER_AGENT,
    }

    request = Request(
        url,
        headers=headers,
        method="GET",
    )

    try:
        with opener.open(
            request,
            timeout=30,
        ) as response:

            body = response.read()

            return {
                "stop_id": stop_id,
                "numeroParada": stop_id,
                "url": url,
                "http_status": response.status,
                "content_type": response.headers.get(
                    "Content-Type",
                    "",
                ),
                "body": body.decode(
                    "utf-8",
                    errors="replace",
                ),
                "error": None,
            }

    except HTTPError as exc:
        body = exc.read().decode(
            "utf-8",
            errors="replace",
        )

        return {
            "stop_id": stop_id,
            "numeroParada": stop_id,
            "url": url,
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
            "stop_id": stop_id,
            "numeroParada": stop_id,
            "url": url,
            "http_status": None,
            "content_type": None,
            "body": "",
            "error": str(exc.reason),
        }

    except Exception as exc:
        return {
            "stop_id": stop_id,
            "numeroParada": stop_id,
            "url": url,
            "http_status": None,
            "content_type": None,
            "body": "",
            "error": repr(exc),
        }


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
            f"ERROR: file does not exist: {zip_path}",
            file=sys.stderr,
        )
        return 1

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw_dir = OUTPUT_DIR / "raw"
    raw_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    stop_ids = read_stop_ids_from_zip(zip_path)

    print()
    print(f"GTFS ZIP: {zip_path}")
    print(f"Unique stops: {len(stop_ids)}")
    print()

    opener = create_session()

    results_file = (
        OUTPUT_DIR / "stop_info.jsonl"
    )

    total = len(stop_ids)
    successful = 0
    failed = 0

    started = datetime.now(timezone.utc)

    with results_file.open(
        "w",
        encoding="utf-8",
    ) as output:

        for index, stop_id in enumerate(
            stop_ids,
            start=1,
        ):
            print(
                f"[{index}/{total}] "
                f"numeroParada={stop_id}",
                flush=True,
            )

            result = fetch_stop(
                opener,
                stop_id,
            )

            # Keep the complete raw response.
            #
            # Stop IDs in the MIO feed are normally numeric, but use
            # an index as the filename as well so a strange stop_id
            # can never create a path outside raw/.
            raw_file = (
                raw_dir
                / f"{index:06d}_{stop_id}.txt"
            )

            raw_file.write_text(
                result["body"],
                encoding="utf-8",
            )

            # Try to decode the response as JSON.
            parsed = None

            if result["body"]:
                try:
                    parsed = json.loads(
                        result["body"]
                    )
                except json.JSONDecodeError:
                    pass

            record = {
                "stop_id": stop_id,
                "numeroParada": stop_id,
                "url": result["url"],
                "http_status": result["http_status"],
                "content_type": result["content_type"],
                "error": result["error"],
                "data": parsed,
                "raw_response": (
                    None
                    if parsed is not None
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
                    "  ERROR: "
                    f"{result['error'] or result['http_status']}",
                    flush=True,
                )

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

    finished = datetime.now(timezone.utc)

    summary = {
        "gtfs_zip": str(zip_path),
        "total_stops": total,
        "successful": successful,
        "failed": failed,
        "started_utc": started.isoformat(),
        "finished_utc": finished.isoformat(),
        "request_delay_seconds": REQUEST_DELAY_SECONDS,
        "output": str(results_file),
    }

    with (
        OUTPUT_DIR / "summary.json"
    ).open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            summary,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("========================================")
    print("Metro Cali stop information fetch")
    print("========================================")
    print(f"Total:      {total}")
    print(f"Successful: {successful}")
    print(f"Failed:     {failed}")
    print(f"Output:     {results_file}")
    print("========================================")

    # Don't fail the entire GitHub Action because one or more
    # individual stops returned an error. The artifact is still
    # useful for investigating those responses.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
