import unittest
from collections import Counter

from monitor import (
    is_retailer_paused,
    retailer_pause_remaining,
    safe_mode_settings_from_config,
    update_retailer_safe_mode_state,
)


class RetailerSafeModeTests(unittest.TestCase):
    def test_default_settings_are_enabled(self):
        settings = safe_mode_settings_from_config({})
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["backoff_seconds"], 45 * 60)
        self.assertEqual(settings["blocked_threshold"], 3)
        self.assertIn("walmart", settings["retailers"])

    def test_blocks_pause_retailer(self):
        settings = safe_mode_settings_from_config({
            "safe_mode": {
                "backoff_minutes": 30,
                "blocked_threshold_per_cycle": 3,
                "unknown_threshold_per_cycle": 4,
                "min_checks_per_cycle": 3,
            }
        })
        state, new_pauses = update_retailer_safe_mode_state(
            {"amazon": Counter({"total": 3, "blocked": 3})},
            settings,
            now=1000,
        )

        self.assertEqual([retailer for retailer, _pause in new_pauses], ["amazon"])
        self.assertTrue(is_retailer_paused("amazon", state, now=1000))
        self.assertEqual(retailer_pause_remaining("amazon", state, now=1000), 30 * 60)
        self.assertIn("3 blocked", state["amazon"]["reason"])

    def test_unknowns_pause_retailer(self):
        settings = safe_mode_settings_from_config({
            "safe_mode": {
                "blocked_threshold_per_cycle": 3,
                "unknown_threshold_per_cycle": 2,
                "min_checks_per_cycle": 2,
            }
        })
        state, new_pauses = update_retailer_safe_mode_state(
            {"bestbuy": Counter({"total": 2, "unknown": 2})},
            settings,
            now=1000,
        )

        self.assertEqual(len(new_pauses), 1)
        self.assertTrue(is_retailer_paused("bestbuy", state, now=1001))

    def test_does_not_pause_below_minimum_checks(self):
        settings = safe_mode_settings_from_config({
            "safe_mode": {
                "blocked_threshold_per_cycle": 1,
                "min_checks_per_cycle": 3,
            }
        })
        state, new_pauses = update_retailer_safe_mode_state(
            {"costco": Counter({"total": 1, "blocked": 1})},
            settings,
            now=1000,
        )

        self.assertEqual(state, {})
        self.assertEqual(new_pauses, [])

    def test_active_pause_is_not_extended_every_cycle(self):
        settings = safe_mode_settings_from_config({
            "safe_mode": {
                "backoff_minutes": 45,
                "blocked_threshold_per_cycle": 3,
                "min_checks_per_cycle": 3,
            }
        })
        state, new_pauses = update_retailer_safe_mode_state(
            {"ebgames": Counter({"total": 3, "blocked": 3})},
            settings,
            now=1000,
        )
        paused_until = state["ebgames"]["paused_until"]

        state, new_pauses = update_retailer_safe_mode_state(
            {"ebgames": Counter({"total": 3, "blocked": 3})},
            settings,
            state=state,
            now=1100,
        )

        self.assertEqual(new_pauses, [])
        self.assertEqual(state["ebgames"]["paused_until"], paused_until)

    def test_expired_pause_no_longer_counts(self):
        state = {
            "walmart": {
                "paused_until": 1000,
                "reason": "3 blocked responses",
                "blocked": 3,
                "unknown": 0,
                "total": 3,
                "set_at": 900,
            }
        }

        self.assertFalse(is_retailer_paused("walmart", state, now=1001))


if __name__ == "__main__":
    unittest.main()
