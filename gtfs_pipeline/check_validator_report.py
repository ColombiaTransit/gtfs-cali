"""
Parses the JSON report produced by MobilityData's gtfs-validator-cli and
exits non-zero if any ERROR-severity notices were found. Also prints a
compact summary (counts per notice code) so CI logs are readable without
opening the full HTML report.

Usage:
    python check_validator_report.py path/to/report.json
"""
import json
import sys
from collections import Counter


def main():
    if len(sys.argv) != 2:
        print("Usage: python check_validator_report.py <report.json>")
        sys.exit(2)

    report_path = sys.argv[1]
    try:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: report file not found at {report_path}. "
              f"Did the validator run successfully?")
        sys.exit(2)

    notices = report.get("notices", [])
    by_severity = Counter()
    by_code = Counter()
    error_codes = []

    for n in notices:
        severity = n.get("severity", "UNKNOWN")
        code = n.get("code", "unknown_code")
        count = n.get("totalNotices", len(n.get("sampleNotices", [])) or 1)
        by_severity[severity] += count
        by_code[(severity, code)] += count
        if severity == "ERROR":
            error_codes.append((code, count))

    print("=== MobilityData gtfs-validator summary ===")
    for severity in ("ERROR", "WARNING", "INFO"):
        if by_severity.get(severity):
            print(f"  {severity}: {by_severity[severity]}")

    if by_code:
        print("\nBy code:")
        for (severity, code), count in sorted(by_code.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  [{severity:<7}] {code}: {count}")

    if error_codes:
        print(f"\nRESULT: FAIL - {sum(c for _, c in error_codes)} error notice(s) across "
              f"{len(error_codes)} code(s). See {report_path} / the HTML report for details.")
        sys.exit(1)

    print("\nRESULT: PASS - no ERROR-severity notices.")
    sys.exit(0)


if __name__ == "__main__":
    main()
