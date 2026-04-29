import unittest

from product_utils import (
    STOCK_BLOCKED,
    StockResult,
    candidate_id,
    default_priority,
    is_pokemon_tcg_sealed_candidate,
    normalize_url,
    stock_transition,
)


class ProductUtilsTests(unittest.TestCase):
    def test_normalize_url_strips_tracking_params(self):
        url = "https://www.bestbuy.ca/en-ca/product/example/12345678?icmp=foo&utm_source=x#reviews"
        self.assertEqual(
            normalize_url(url),
            "https://www.bestbuy.ca/en-ca/product/example/12345678",
        )

    def test_candidate_id_is_stable_for_tracking_changes(self):
        a = candidate_id("https://www.amazon.ca/dp/B0DYR8D7YC?tag=abc")
        b = candidate_id("https://www.amazon.ca/dp/B0DYR8D7YC")
        self.assertEqual(a, b)

    def test_default_priority_marks_high_value_product_types(self):
        self.assertEqual(default_priority("Pokemon TCG Elite Trainer Box"), "high")
        self.assertEqual(default_priority("Pokemon TCG Mini Tin"), "normal")

    def test_tcg_filter_rejects_sports_cards(self):
        self.assertFalse(is_pokemon_tcg_sealed_candidate("Topps Basketball Mega Box", "https://www.walmart.ca"))
        self.assertFalse(is_pokemon_tcg_sealed_candidate("Pokemon TCG B0DHRDM481", "https://www.amazon.ca/dp/B0DHRDM481"))
        self.assertFalse(is_pokemon_tcg_sealed_candidate("Acrylic Display Case for Pokemon TCG ETB", "https://www.amazon.ca"))
        self.assertTrue(is_pokemon_tcg_sealed_candidate("Pokemon TCG Booster Bundle", "https://www.walmart.ca"))

    def test_blocked_result_preserves_previous_status(self):
        result = StockResult(STOCK_BLOCKED, reason="captcha")
        self.assertEqual(stock_transition(True, result), (True, "no_change"))
        self.assertEqual(stock_transition(False, result), (False, "no_change"))


if __name__ == "__main__":
    unittest.main()
