import json
import unittest
from unittest.mock import Mock, patch

import monitor
from product_utils import STOCK_IN, STOCK_MARKETPLACE, STOCK_OUT


class FakeResponse:
    def __init__(self, text="", status_code=200, payload=None):
        self.text = text
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class CheckerFixtureTests(unittest.TestCase):
    def test_amazon_fixture_detects_trusted_in_stock(self):
        html = """
        <html><body>
          <div id="availability">In Stock</div>
          <span class="a-size-small">Ships from and sold by Amazon.ca</span>
          <span class="a-price-whole">79</span>
          <input id="add-to-cart-button" />
        </body></html>
        """
        with patch("monitor.requests.get", return_value=FakeResponse(html)):
            result = monitor.check_amazon("https://www.amazon.ca/dp/B0DYR8D7YC", {"name": "PE ETB - Amazon.ca"})
        self.assertEqual(result.status, STOCK_IN)
        self.assertEqual(result.seller, "Amazon.ca")

    def test_walmart_fixture_rejects_marketplace_seller(self):
        next_data = {
            "props": {
                "pageProps": {
                    "initialData": {
                        "data": {
                            "product": {
                                "sellerName": "Random Cards Shop",
                                "availabilityStatus": "IN_STOCK",
                                "priceInfo": {"currentPrice": {"price": 59.99}},
                            }
                        }
                    }
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>'
        with patch("monitor.cffi_get_with_fallback", return_value=FakeResponse(html)):
            result = monitor.check_walmart("https://www.walmart.ca/en/ip/example/12345678", {"name": "Example - Walmart.ca", "url": "https://www.walmart.ca/en/ip/example/12345678", "priority": "high"})
        self.assertEqual(result.status, STOCK_MARKETPLACE)

    def test_bestbuy_fixture_detects_in_stock(self):
        session = Mock()
        session.get.return_value = FakeResponse(payload={
            "seller": {"name": "Best Buy Canada"},
            "salePrice": 44.99,
            "isPurchasable": True,
            "availability": {
                "buttonState": "AddToCart",
                "isAvailableOnline": True,
            },
        })
        with patch("monitor.get_session", return_value=session):
            result = monitor.check_bestbuy("https://www.bestbuy.ca/en-ca/product/example/12345678")
        self.assertEqual(result.status, STOCK_IN)
        self.assertEqual(result.price, 44.99)

    def test_costco_fixture_detects_out_of_stock(self):
        html = """
        <script type="application/ld+json">
        {"offers": {"availability": "https://schema.org/OutOfStock", "price": "199.99"}}
        </script>
        """
        with patch("monitor.cffi_get_with_fallback", return_value=FakeResponse(html)):
            result = monitor.check_costco("https://www.costco.ca/.product.1234567.html")
        self.assertEqual(result.status, STOCK_OUT)
        self.assertEqual(result.price, "199.99")

    def test_ebgames_fixture_detects_in_stock(self):
        html = "<html><body><span>$29.99</span><button>Add to Cart</button></body></html>"
        with patch("monitor.cffi_get_with_fallback", return_value=FakeResponse(html)):
            result = monitor.check_ebgames("https://www.ebgames.ca/Trading%20Cards/Games/123456/example")
        self.assertEqual(result.status, STOCK_IN)
        self.assertEqual(result.price, "29.99")


if __name__ == "__main__":
    unittest.main()
