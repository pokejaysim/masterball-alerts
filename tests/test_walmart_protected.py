import unittest

from product_utils import STOCK_BLOCKED, STOCK_MARKETPLACE, STOCK_OUT, StockResult
from walmart_protected import (
    has_walmart_product_id,
    validate_walmart_candidate,
    walmart_settings_from_config,
)


class WalmartProtectedTests(unittest.TestCase):
    def test_settings_defaults_to_proxy_required(self):
        settings = walmart_settings_from_config({})
        self.assertTrue(settings["enabled"])
        self.assertTrue(settings["require_proxy"])
        self.assertEqual(settings["lightweight_interval_seconds"], 600)

    def test_product_id_required_for_validation(self):
        candidate = {
            "retailer": "walmart",
            "name": "Pokemon TCG Booster Bundle - Walmart CA",
            "url": "https://www.walmart.ca/en/ip/example",
        }
        valid, reason, result = validate_walmart_candidate(candidate, config={"walmart": {"enabled": True}})
        self.assertFalse(valid)
        self.assertIn("missing product id", reason)
        self.assertEqual(result.status, "unknown")

    def test_validation_accepts_out_of_stock_product_page(self):
        candidate = {
            "retailer": "walmart",
            "name": "Pokemon TCG Booster Bundle - Walmart CA",
            "url": "https://www.walmart.ca/en/ip/example/12345678",
        }
        valid, reason, result = validate_walmart_candidate(
            candidate,
            config={"walmart": {"enabled": True}},
            checker=lambda _url: StockResult.out_of_stock(seller="Walmart.ca"),
        )
        self.assertTrue(valid)
        self.assertIn("passed", reason)
        self.assertEqual(result.status, STOCK_OUT)

    def test_validation_keeps_blocked_pending(self):
        candidate = {
            "retailer": "walmart",
            "name": "Pokemon TCG Booster Bundle - Walmart CA",
            "url": "https://www.walmart.ca/en/ip/example/12345678",
        }
        valid, reason, result = validate_walmart_candidate(
            candidate,
            config={"walmart": {"enabled": True}},
            checker=lambda _url: StockResult.blocked("captcha"),
        )
        self.assertFalse(valid)
        self.assertIn("blocked", reason)
        self.assertEqual(result.status, STOCK_BLOCKED)

    def test_validation_rejects_marketplace(self):
        candidate = {
            "retailer": "walmart",
            "name": "Pokemon TCG Booster Bundle - Walmart CA",
            "url": "https://www.walmart.ca/en/ip/example/12345678",
        }
        valid, reason, result = validate_walmart_candidate(
            candidate,
            config={"walmart": {"enabled": True}},
            checker=lambda _url: StockResult.marketplace(seller="Random Cards Shop"),
        )
        self.assertFalse(valid)
        self.assertIn("marketplace", reason)
        self.assertEqual(result.status, STOCK_MARKETPLACE)

    def test_walmart_product_id_detection(self):
        self.assertTrue(has_walmart_product_id("https://www.walmart.ca/en/ip/example/12345678"))
        self.assertTrue(has_walmart_product_id("https://www.walmart.ca/en/ip/example/4BBHBZPCR45Z"))
        self.assertFalse(has_walmart_product_id("https://www.walmart.ca/en/ip/example"))


if __name__ == "__main__":
    unittest.main()

