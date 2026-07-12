#!/usr/bin/env python3
"""Report service requests whose latency exceeds a threshold."""

import argparse
from dataclasses import asdict
import json
import sys

from latency_analysis import (
    MAX_SERVICES,
    P95_CONFIDENCE,
    P95_RANK_ERROR,
    P95_SAMPLE_SIZE,
    MalformedRecord,
    RunningServiceSummary,
    Thresholds,
    parse_lines,
    valid_number,
)


class InvocationParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def load_thresholds(path, fallback_ms):
    if path is None:
        if not valid_number(fallback_ms):
            raise ValueError("threshold_ms must be a non-negative finite number")
        return Thresholds(fallback_ms, {})

    with open(path, encoding="utf-8") as threshold_file:
        value = json.load(threshold_file)

    if not isinstance(value, dict) or not valid_number(value.get("default_threshold_ms")):
        raise ValueError("default_threshold_ms must be a non-negative finite number")
    services = value.get("services")
    if not isinstance(services, dict):
        raise ValueError("services must be a JSON object")
    for service, threshold_ms in services.items():
        if not service or not isinstance(service, str) or not valid_number(threshold_ms):
            raise ValueError("service thresholds require non-empty names and valid numbers")

    return Thresholds(value["default_threshold_ms"], services)


def report_malformed(path, record):
    print(
        f"MALFORMED: {path}: line {record.line_number}: {record.reason}",
        file=sys.stderr,
    )


def print_violations(path, records, thresholds):
    violation_count = 0
    for record in records:
        if isinstance(record, MalformedRecord):
            report_malformed(path, record)
        elif record.exceeds(thresholds.for_service(record.service)):
            print(json.dumps(record.original, separators=(",", ":")))
            violation_count += 1

    return 2 if violation_count else 0


def print_summary(path, records, thresholds):
    running_summaries = {}
    malformed_record_count = 0

    for record in records:
        if isinstance(record, MalformedRecord):
            report_malformed(path, record)
            malformed_record_count += 1
            continue

        if record.service not in running_summaries:
            if len(running_summaries) >= MAX_SERVICES:
                print(
                    f"{path}: too many distinct services (maximum {MAX_SERVICES})",
                    file=sys.stderr,
                )
                return 1
            running_summaries[record.service] = RunningServiceSummary(
                record.service, thresholds.for_service(record.service)
            )
        running_summaries[record.service].add(record.latency_ms)

    services = [
        running_summaries[service].finish() for service in sorted(running_summaries)
    ]
    document = {
        "default_threshold_ms": thresholds.default_ms,
        "malformed_record_count": malformed_record_count,
        "p95": {
            "method": "reservoir-sample",
            "sample_size": P95_SAMPLE_SIZE,
            "rank_error": P95_RANK_ERROR,
            "confidence": P95_CONFIDENCE,
        },
        "services": [asdict(service) for service in services],
    }
    print(json.dumps(document, separators=(",", ":")))

    return 2 if any(service.flagged for service in services) else 0


def check_file(path, thresholds, record_mode=False):
    try:
        log_file = sys.stdin if path == "-" else open(path, encoding="utf-8")
        try:
            records = parse_lines(log_file)
            if record_mode:
                return print_violations(path, records, thresholds)
            return print_summary(path, records, thresholds)
        finally:
            if path != "-":
                log_file.close()
    except (OSError, UnicodeError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 1


def main():
    parser = InvocationParser(description=__doc__)
    parser.add_argument("path")
    thresholds_group = parser.add_mutually_exclusive_group()
    thresholds_group.add_argument("--threshold-ms", type=int, default=None)
    thresholds_group.add_argument("--thresholds", metavar="PATH")
    parser.add_argument(
        "--records",
        action="store_true",
        help="print each violating record instead of a service summary",
    )
    arguments = parser.parse_args()

    try:
        fallback_ms = 750 if arguments.threshold_ms is None else arguments.threshold_ms
        thresholds = load_thresholds(arguments.thresholds, fallback_ms)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"invalid thresholds: {error}", file=sys.stderr)
        return 1

    return check_file(arguments.path, thresholds, arguments.records)


if __name__ == "__main__":
    sys.exit(main())
