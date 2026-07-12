# Latency threshold checker

An operations engineer left this diagnostic script behind. Its intended behavior will be given by the interviewer.

Summarize valid samples by service with:

```bash
python3 threshold_checker.py service_logs.jsonl --threshold-ms 750
```

Print each violating record in the original per-record mode with:

```bash
python3 threshold_checker.py service_logs.jsonl --threshold-ms 750 --records
```

Read a stream from stdin and apply the default and per-service thresholds in a
configuration file with:

```bash
producer | python3 threshold_checker.py - --thresholds thresholds.json
```

Summary mode keeps exact counts, violation counts, and maximums. P95 is exact for
up to 1,024 samples per service, then uses a deterministic reservoir sample of
1,024 values. Its reported percentile rank has an approximate ±4.3 percentage
point error at 95% confidence. Memory is capped at 1,024 latency values for each
of at most 10,000 distinct services; exceeding that service limit exits with an
error instead of growing memory without bound.
