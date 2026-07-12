#!/usr/bin/env python3
"""Print slow service log records from a JSONL file."""

import argparse
import json


def print_slow_records(path, threshold_ms):
    violations = 0
    with open(path) as log_file:
        for line in log_file:
            record = json.loads(line)
            latency_ms = int(record["latency_ms"])
            status = int(record["status"])
            if latency_ms >= threshold_ms and status >= 500:
                print(json.dumps(record))
                violations += 1
    return violations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--threshold-ms", default=750)
    args = parser.parse_args()
    print_slow_records(args.path, args.threshold_ms)


if __name__ == "__main__":
    main()

