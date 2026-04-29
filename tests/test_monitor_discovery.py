import unittest

from monitor import discovery_settings_from_config


class MonitorDiscoveryTests(unittest.TestCase):
    def test_discovery_settings_defaults_to_disabled_if_missing(self):
        settings = discovery_settings_from_config({})
        self.assertFalse(settings["auto_run"])
        self.assertEqual(settings["interval_seconds"], 10800)
        self.assertEqual(settings["startup_delay_seconds"], 120)

    def test_discovery_settings_reads_config_and_enforces_minimum_interval(self):
        settings = discovery_settings_from_config({
            "discovery": {
                "auto_run": True,
                "auto_run_interval_minutes": 10,
                "auto_run_startup_delay_seconds": 5,
                "auto_approve": True,
                "auto_approve_min_confidence": 0.9,
                "auto_approve_retailers": ["walmart"],
            }
        })
        self.assertTrue(settings["auto_run"])
        self.assertEqual(settings["interval_seconds"], 3600)
        self.assertEqual(settings["startup_delay_seconds"], 5)
        self.assertTrue(settings["auto_approve"])
        self.assertEqual(settings["auto_min_confidence"], 0.9)
        self.assertEqual(settings["auto_retailers"], ["walmart"])


if __name__ == "__main__":
    unittest.main()
