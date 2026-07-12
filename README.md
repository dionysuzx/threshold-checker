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
