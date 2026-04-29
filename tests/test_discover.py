import unittest

from discover import should_auto_approve


class DiscoverTests(unittest.TestCase):
    def test_auto_approve_uses_confidence_and_retailer_guardrails(self):
        candidate = {"retailer": "walmart", "confidence": 0.82}
        self.assertTrue(should_auto_approve(candidate, 0.82, {"walmart"}))
        self.assertFalse(should_auto_approve(candidate, 0.9, {"walmart"}))
        self.assertFalse(should_auto_approve(candidate, 0.82, {"bestbuy"}))

    def test_auto_approve_never_approves_pokemon_center(self):
        candidate = {"retailer": "pokemoncenter", "confidence": 1.0}
        self.assertFalse(should_auto_approve(candidate, 0.82, {"pokemoncenter"}))


if __name__ == "__main__":
    unittest.main()
