import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from status_health import (
    classify_snapshot,
    launchagent_status,
    parse_log_timestamp,
    summarize_retailer_health,
    walmart_health_summary,
)


class StatusHealthTests(unittest.TestCase):
    def test_parse_log_timestamp(self):
        parsed = parse_log_timestamp("[2026-04-29 16:20:35] Cycle done")
        self.assertEqual(parsed, datetime(2026, 4, 29, 16, 20, 35))
        self.assertIsNone(parse_log_timestamp("Cycle done"))

    def test_launchagent_status_uses_top_level_state(self):
        output = """
gui/501/com.masterball.alerts = {
    state = running
    pid = 12345
    resource coalition = {
        state = active
    }
}
"""

        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        status = launchagent_status(runner=runner)
        self.assertTrue(status["running"])
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["pid"], "12345")

    def test_classify_down_when_service_not_running(self):
        status, message = classify_snapshot(
            {"running": False},
            last_log_age_seconds=10,
            stale_seconds=300,
            retailers=[],
        )
        self.assertEqual(status, "down")
        self.assertIn("not running", message)

    def test_classify_down_when_log_is_stale(self):
        status, message = classify_snapshot(
            {"running": True},
            last_log_age_seconds=601,
            stale_seconds=300,
            retailers=[],
        )
        self.assertEqual(status, "down")
        self.assertIn("No monitor log activity", message)

    def test_classify_degraded_for_retailer_blocks(self):
        status, message = classify_snapshot(
            {"running": True},
            last_log_age_seconds=10,
            stale_seconds=300,
            retailers=[{"name": "Amazon", "status": "degraded"}],
        )
        self.assertEqual(status, "degraded")
        self.assertIn("Amazon", message)

    def test_retailer_summary_counts_blocks_and_successes(self):
        retailers = summarize_retailer_health([
            "[2026-04-29 16:20:06] Hot check preserved state for PE Booster Bundle - Amazon.ca: Amazon CAPTCHA",
            "[2026-04-29 16:20:07] Hot check preserved state for PE ETB - Amazon.ca: Amazon CAPTCHA",
            "[2026-04-29 16:20:08] Hot check preserved state for PE Mini Tin - Amazon.ca: Amazon CAPTCHA",
            "[2026-04-29 16:20:18] Sold by: amazon.ca (trusted)",
        ])
        amazon = next(row for row in retailers if row["key"] == "amazon")
        self.assertEqual(amazon["status"], "degraded")
        self.assertEqual(amazon["blocked"], 3)
        self.assertEqual(amazon["success"], 1)

    def test_walmart_health_reports_proxy_and_queue(self):
        db = {"walmart_discovery": {"approved": 4, "pending": 9, "pending_validation": 3}}
        config = {
            "walmart": {"enabled": True},
            "products": [
                {"name": "Pokemon TCG Booster Bundle - Walmart.ca", "url": "https://www.walmart.ca/en/ip/example/12345678", "enabled": True}
            ],
        }
        lines = ["[2026-04-29 16:20:06] Walmart blocked: Walmart proxy not configured"]
        with patch("status_health.walmart_proxy_ready", return_value=False):
            summary = walmart_health_summary(config, db, lines)
        self.assertEqual(summary["active_product_count"], 5)
        self.assertEqual(summary["pending_validation_count"], 3)
        self.assertEqual(summary["lane_state"], "blocked")


if __name__ == "__main__":
    unittest.main()
