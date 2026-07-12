import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("threshold_checker.py")
EDGE_CASES = Path(__file__).with_name("service_logs_edge_cases.jsonl")


class ThresholdCheckerCliTests(unittest.TestCase):
    def run_checker(self, contents, *arguments):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "requests.jsonl"
            input_path.write_text(contents, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(SCRIPT), str(input_path), *arguments],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_prints_slow_records_in_input_order_regardless_of_status(self):
        records = [
            {"timestamp": "t1", "service": "web", "latency_ms": 751, "status": 200},
            {"timestamp": "t2", "service": "jobs", "latency_ms": 900, "status": 503},
        ]
        result = self.run_checker(
            "".join(json.dumps(record) + "\n" for record in records),
            "--threshold-ms",
            "750",
            "--records",
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stdout,
            "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        )
        self.assertEqual(result.stderr, "")

    def test_latency_equal_to_threshold_is_not_a_violation(self):
        record = {"timestamp": "t1", "service": "web", "latency_ms": 750, "status": 500}

        result = self.run_checker(
            json.dumps(record) + "\n", "--threshold-ms", "750", "--records"
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_reports_malformed_records_and_continues(self):
        slow_record = {
            "timestamp": "t3",
            "service": "web",
            "latency_ms": 800,
            "status": 200,
        }
        contents = "not json\n{\"timestamp\":\"t2\"}\n" + json.dumps(slow_record) + "\n"

        result = self.run_checker(contents, "--threshold-ms", "750", "--records")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stdout,
            json.dumps(slow_record, separators=(",", ":")) + "\n",
        )
        self.assertIn("line 1:", result.stderr)
        self.assertIn("line 2:", result.stderr)

    def test_malformed_records_without_violations_exit_successfully(self):
        result = self.run_checker("not json\n", "--threshold-ms", "750", "--records")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("line 1:", result.stderr)

    def test_invalid_threshold_is_an_invalid_invocation(self):
        result = self.run_checker("", "--threshold-ms", "slow")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_unreadable_input_exits_one(self):
        missing_path = SCRIPT.with_name("does-not-exist.jsonl")

        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(missing_path), "--threshold-ms", "750"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_summary_reports_edge_case_file_as_one_stable_json_document(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(EDGE_CASES),
                "--threshold-ms",
                "750",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "threshold_ms": 750,
                "malformed_record_count": 8,
                "services": [
                    {
                        "service": "api-gateway",
                        "sample_count": 4,
                        "violation_count": 4,
                        "max_latency_ms": 840,
                        "p95_latency_ms": 840,
                        "flagged": False,
                    },
                    {
                        "service": "pricing-worker",
                        "sample_count": 6,
                        "violation_count": 0,
                        "max_latency_ms": 50,
                        "p95_latency_ms": 50,
                        "flagged": False,
                    },
                    {
                        "service": "risk-engine",
                        "sample_count": 6,
                        "violation_count": 1,
                        "max_latency_ms": 760,
                        "p95_latency_ms": 760,
                        "flagged": True,
                    },
                    {
                        "service": "vault-api",
                        "sample_count": 6,
                        "violation_count": 1,
                        "max_latency_ms": 900,
                        "p95_latency_ms": 900,
                        "flagged": True,
                    },
                ],
            },
        )
        self.assertEqual(result.stdout.count("\n"), 1)
        self.assertEqual(result.stderr.count("MALFORMED:"), 8)

    def test_summary_does_not_flag_p95_equal_to_threshold(self):
        contents = "".join(
            json.dumps(
                {
                    "timestamp": f"t{latency}",
                    "service": "web",
                    "latency_ms": latency,
                    "status": 200,
                }
            )
            + "\n"
            for latency in [100, 200, 300, 400, 750]
        )

        result = self.run_checker(contents, "--threshold-ms", "750")

        self.assertEqual(result.returncode, 0)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["services"][0]["p95_latency_ms"], 750)
        self.assertFalse(summary["services"][0]["flagged"])

    def test_summary_treats_empty_service_as_malformed(self):
        record = {"timestamp": "t1", "service": "", "latency_ms": 100, "status": 200}

        result = self.run_checker(json.dumps(record) + "\n", "--threshold-ms", "750")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["malformed_record_count"], 1)
        self.assertIn("service must be a non-empty string", result.stderr)

    def test_arbitrary_timestamp_and_status_values_are_valid(self):
        records = [
            {"timestamp": None, "service": "web", "latency_ms": 100, "status": "timeout"},
            {"timestamp": ["structured"], "service": "web", "latency_ms": 800, "status": {}},
        ]
        contents = "".join(json.dumps(record) + "\n" for record in records)

        summary_result = self.run_checker(contents, "--threshold-ms", "750")
        record_result = self.run_checker(
            contents, "--threshold-ms", "750", "--records"
        )

        summary = json.loads(summary_result.stdout)
        self.assertEqual(summary_result.returncode, 0)
        self.assertEqual(summary["malformed_record_count"], 0)
        self.assertEqual(summary["services"][0]["sample_count"], 2)
        self.assertEqual(record_result.returncode, 2)
        self.assertEqual(json.loads(record_result.stdout), records[1])
        self.assertEqual(record_result.stderr, "")


if __name__ == "__main__":
    unittest.main()
