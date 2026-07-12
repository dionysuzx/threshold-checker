#!/usr/bin/env python3
"""Report service requests whose latency exceeds a threshold."""

import argparse
from dataclasses import asdict, dataclass
import json
import math
import random
import sys
from typing import Union


REQUIRED_FIELDS = ("timestamp", "service", "latency_ms", "status")
MINIMUM_SAMPLES_TO_FLAG = 5
P95_SAMPLE_SIZE = 1024
P95_RANK_ERROR = 0.043
P95_CONFIDENCE = 0.95
MAX_SERVICES = 10_000


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
    threshold_ms: Union[int, float]
    sample_count: int
    violation_count: int
    max_latency_ms: Union[int, float]
    p95_latency_ms: Union[int, float]
    flagged: bool



@dataclass(frozen=True)
class Thresholds:
    default_ms: Union[int, float]
    services: dict

    def for_service(self, service):
        return self.services.get(service, self.default_ms)


class RunningServiceSummary:
    """Exact counters plus a bounded, deterministic reservoir for p95."""

    def __init__(self, service, threshold_ms):
        self.service = service
        self.threshold_ms = threshold_ms
        self.sample_count = 0
        self.violation_count = 0
        self.max_latency_ms = 0
        self._p95_sample = []
        self._random = random.Random(service)

    def add(self, latency_ms):
        self.sample_count += 1
        self.violation_count += latency_ms > self.threshold_ms
        self.max_latency_ms = max(self.max_latency_ms, latency_ms)

        if len(self._p95_sample) < P95_SAMPLE_SIZE:
            self._p95_sample.append(latency_ms)
            return

        position = self._random.randrange(self.sample_count)
        if position < P95_SAMPLE_SIZE:
            self._p95_sample[position] = latency_ms

    def finish(self):
        ordered = sorted(self._p95_sample)
        p95_index = math.ceil(0.95 * len(ordered)) - 1
        p95_latency_ms = ordered[p95_index]
        return ServiceSummary(
            service=self.service,
            threshold_ms=self.threshold_ms,
            sample_count=self.sample_count,
            violation_count=self.violation_count,
            max_latency_ms=self.max_latency_ms,
            p95_latency_ms=p95_latency_ms,
            flagged=(
                self.sample_count >= MINIMUM_SAMPLES_TO_FLAG
                and p95_latency_ms > self.threshold_ms
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


def valid_number(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


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
        else:
            if record.service not in running_summaries:
                if len(running_summaries) >= MAX_SERVICES:
                    print(f"{path}: too many distinct services (maximum {MAX_SERVICES})", file=sys.stderr)
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
            parsed_records = parse_lines(log_file)
            if record_mode:
                return print_violations(path, parsed_records, thresholds)
            return print_summary(path, parsed_records, thresholds)
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
