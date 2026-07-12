"""Domain values and transformations for latency records and summaries."""

from dataclasses import dataclass
import json
import math
import random
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


def malformed(line_number, reason):
    return MalformedRecord(line_number, reason)


def reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def parse_finite_json_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"JSON number is outside the finite range: {value}")
    return number


def parse_record(line, line_number):
    try:
        value = json.loads(
            line,
            parse_constant=reject_json_constant,
            parse_float=parse_finite_json_float,
        )
    except json.JSONDecodeError as error:
        return malformed(line_number, f"invalid JSON: {error.msg}")
    except ValueError as error:
        return malformed(line_number, str(error))

    if not isinstance(value, dict):
        return malformed(line_number, "record must be a JSON object")

    missing_fields = [field for field in REQUIRED_FIELDS if field not in value]
    if missing_fields:
        return malformed(line_number, f"missing field: {missing_fields[0]}")

    if not isinstance(value["service"], str) or not value["service"]:
        return malformed(line_number, "service must be a non-empty string")

    latency_ms = value["latency_ms"]
    if not valid_number(latency_ms):
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
