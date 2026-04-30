import unittest
from unittest.mock import patch

from discover import maybe_auto_approve_candidate, should_auto_approve
from product_utils import StockResult


class DiscoverTests(unittest.TestCase):
    def test_auto_approve_uses_confidence_and_retailer_guardrails(self):
        candidate = {"retailer": "walmart", "confidence": 0.82}
        self.assertTrue(should_auto_approve(candidate, 0.82, {"walmart"}))
        self.assertFalse(should_auto_approve(candidate, 0.9, {"walmart"}))
        self.assertFalse(should_auto_approve(candidate, 0.82, {"bestbuy"}))

    def test_auto_approve_never_approves_pokemon_center(self):
        candidate = {"retailer": "pokemoncenter", "confidence": 1.0}
        self.assertFalse(should_auto_approve(candidate, 0.82, {"pokemoncenter"}))

    def test_walmart_auto_approve_requires_validation(self):
        candidate = {
            "retailer": "walmart",
            "confidence": 0.82,
            "name": "Pokemon TCG Booster Bundle - Walmart CA",
            "url": "https://www.walmart.ca/en/ip/example/12345678",
        }
        with patch("discover.validate_walmart_candidate", return_value=(False, "walmart validation blocked", StockResult.blocked("captcha"))):
            status, reason = maybe_auto_approve_candidate(candidate, {"walmart": {"enabled": True}}, 0.82, {"walmart"})
        self.assertIsNone(status)
        self.assertEqual(reason, "walmart validation blocked")

    def test_walmart_auto_approve_passes_after_validation(self):
        candidate = {
            "retailer": "walmart",
            "confidence": 0.82,
            "name": "Pokemon TCG Booster Bundle - Walmart CA",
            "url": "https://www.walmart.ca/en/ip/example/12345678",
        }
        with patch("discover.validate_walmart_candidate", return_value=(True, "walmart validation passed", StockResult.out_of_stock())):
            status, reason = maybe_auto_approve_candidate(candidate, {"walmart": {"enabled": True}}, 0.82, {"walmart"})
        self.assertEqual(status, "approved")
        self.assertEqual(reason, "Auto-approved by Walmart validation")


if __name__ == "__main__":
    unittest.main()
