# Latency threshold checker

`threshold_checker.py` reads JSON Lines request logs, reports malformed records,
and detects latency problems without using HTTP status to decide whether a request
is slow. It uses only the Python standard library.

## Run it

Summary mode is the default:

```bash
python3 threshold_checker.py service_logs.jsonl --threshold-ms 750
```

The original per-record mode prints compact violation JSON in input order:

```bash
python3 threshold_checker.py service_logs.jsonl --threshold-ms 750 --records
```

Use `-` to stream JSONL from stdin. A threshold configuration supplies a default
and optional service overrides:

```bash
producer | python3 threshold_checker.py - --thresholds thresholds.json
```

```json
{
  "default_threshold_ms": 750,
  "services": {
    "risk-engine": 700,
    "vault-api": 800
  }
}
```

Exit status is `0` when nothing is flagged, `2` when violations or flagged
services exist, and `1` for invalid invocation, configuration, or input access.
Malformed-record diagnostics go to stderr and do not stop later records.

## Summary rules

For each service, the summary contains its applied threshold, valid sample count,
violation count, maximum latency, p95 latency, and flag. Services are sorted by
name. A service is flagged only when it has at least five valid samples and its
p95 is strictly greater than its threshold.

A valid record contains `timestamp`, `service`, `latency_ms`, and `status`.
`service` must be a non-empty string. `latency_ms` must be a finite, non-negative
JSON number and cannot be a boolean. Timestamp and status may contain any valid
JSON value.

## Streaming and p95 bounds

Exact nearest-rank p95 requires retaining values to find their sorted rank, which
is incompatible with bounded memory for an arbitrary one-pass stream. Counts,
violation counts, and maximums remain exact. P95 is exact through 1,024 samples
per service, then uses a deterministic reservoir of 1,024 values. Its approximate
percentile-rank error is ±4.3 percentage points at 95% confidence.

Memory is capped at 1,024 latency values for each of at most 10,000 distinct
services. Exceeding the service limit exits with an error instead of allowing
memory to grow without bound.

## Code map

- `latency_analysis.py`: domain records, validation, threshold lookup, and bounded
  summary calculation.
- `threshold_checker.py`: CLI parsing, file/stdin access, diagnostics, JSON output,
  and exit codes.
- `test_threshold_checker.py`: executable behavioral specification through the
  real CLI, plus the bounded-reservoir invariant.

## Verify

```bash
python3 -m unittest -v
python3 -m compileall -q latency_analysis.py threshold_checker.py test_threshold_checker.py
```
