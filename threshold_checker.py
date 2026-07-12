#!/usr/bin/env python3
"""Report service requests whose latency exceeds a threshold."""

import argparse
from dataclasses import asdict, dataclass
import json
import math
import sys
from typing import Union


REQUIRED_FIELDS = ("timestamp", "service", "latency_ms", "status")
MINIMUM_SAMPLES_TO_FLAG = 5


@dataclass(frozen=True)
class LogRecord:
    timestamp: object
    service: str
    latency_ms: Union[int, float]
    status: object
    original: dict

    def exceeds(self, threshold_ms):
        return self.latency_ms > threshold_ms


@dataclass(frozen=True)
class MalformedRecord:
    line_number: int
    reason: str


@dataclass(frozen=True)
class ServiceSummary:
    service: str
    sample_count: int
    violation_count: int
    max_latency_ms: Union[int, float]
    p95_latency_ms: Union[int, float]
    flagged: bool

    @classmethod
    def from_latencies(cls, service, latencies, threshold_ms):
        ordered = sorted(latencies)
        p95_index = math.ceil(0.95 * len(ordered)) - 1
        p95_latency_ms = ordered[p95_index]

        return cls(
            service=service,
            sample_count=len(ordered),
            violation_count=sum(latency > threshold_ms for latency in ordered),
            max_latency_ms=ordered[-1],
            p95_latency_ms=p95_latency_ms,
            flagged=(
                len(ordered) >= MINIMUM_SAMPLES_TO_FLAG
                and p95_latency_ms > threshold_ms
            ),
        )


class InvocationParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def malformed(line_number, reason):
    return MalformedRecord(line_number, reason)


def parse_record(line, line_number):
    try:
        value = json.loads(line)
    except json.JSONDecodeError as error:
        return malformed(line_number, f"invalid JSON: {error.msg}")

    if not isinstance(value, dict):
        return malformed(line_number, "record must be a JSON object")

    missing_fields = [field for field in REQUIRED_FIELDS if field not in value]
    if missing_fields:
        return malformed(line_number, f"missing field: {missing_fields[0]}")

    if not isinstance(value["service"], str) or not value["service"]:
        return malformed(line_number, "service must be a non-empty string")

    latency_ms = value["latency_ms"]
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, (int, float))
        or not math.isfinite(latency_ms)
        or latency_ms < 0
    ):
        return malformed(line_number, "latency_ms must be a non-negative finite number")

    return LogRecord(
        timestamp=value["timestamp"],
        service=value["service"],
        latency_ms=latency_ms,
        status=value["status"],
        original=value,
    )


def parse_lines(lines):
    for line_number, line in enumerate(lines, start=1):
        yield parse_record(line, line_number)


def report_malformed(path, record):
    print(
        f"MALFORMED: {path}: line {record.line_number}: {record.reason}",
        file=sys.stderr,
    )


def print_violations(path, records, threshold_ms):
    violation_count = 0
    for record in records:
        if isinstance(record, MalformedRecord):
            report_malformed(path, record)
        elif record.exceeds(threshold_ms):
            print(json.dumps(record.original, separators=(",", ":")))
            violation_count += 1

    return 2 if violation_count else 0


def print_summary(path, records, threshold_ms):
    latencies_by_service = {}
    malformed_record_count = 0

    for record in records:
        if isinstance(record, MalformedRecord):
            report_malformed(path, record)
            malformed_record_count += 1
        else:
            latencies_by_service.setdefault(record.service, []).append(record.latency_ms)

    services = [
        ServiceSummary.from_latencies(service, latencies_by_service[service], threshold_ms)
        for service in sorted(latencies_by_service)
    ]
    document = {
        "threshold_ms": threshold_ms,
        "malformed_record_count": malformed_record_count,
        "services": [asdict(service) for service in services],
    }
    print(json.dumps(document, separators=(",", ":")))

    return 2 if any(service.flagged for service in services) else 0


def check_file(path, threshold_ms, record_mode=False):
    try:
        with open(path, encoding="utf-8") as log_file:
            parsed_records = parse_lines(log_file)
            if record_mode:
                return print_violations(path, parsed_records, threshold_ms)
            return print_summary(path, parsed_records, threshold_ms)
    except (OSError, UnicodeError) as error:
        print(f"{path}: {error}", file=sys.stderr)
        return 1


def main():
    parser = InvocationParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--threshold-ms", type=int, default=750)
    parser.add_argument(
        "--records",
        action="store_true",
        help="print each violating record instead of a service summary",
    )
    arguments = parser.parse_args()
    return check_file(arguments.path, arguments.threshold_ms, arguments.records)


if __name__ == "__main__":
    sys.exit(main())
