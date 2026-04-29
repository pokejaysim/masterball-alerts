import unittest

import monitor


class AlertFormatterTests(unittest.TestCase):
    def setUp(self):
        monitor._detected_prices.clear()
        monitor._detected_sellers.clear()

    def test_alert_message_is_compact_and_escaped(self):
        product = {
            "name": "Pokemon <ETB> & Booster - Amazon.ca",
            "url": "https://www.amazon.ca/dp/B0DYR8D7YC",
        }
        monitor.set_detected_price(product["name"], "79.99")
        monitor.set_detected_seller(product["name"], "Amazon & Co")

        message = monitor.build_alert_message(product)

        self.assertIn("Amazon CA Restock", message)
        self.assertIn("Pokemon &lt;ETB&gt; &amp; Booster", message)
        self.assertIn("$79.99 CAD", message)
        self.assertIn("Amazon &amp; Co", message)
        self.assertIn("Add to Cart", message)
        self.assertNotIn("Market Value", message)


if __name__ == "__main__":
    unittest.main()
