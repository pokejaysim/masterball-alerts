import importlib
import os
import tempfile
import unittest


class DatabaseDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["MASTERBALL_DB_PATH"] = os.path.join(self.tmp.name, "test.db")
        import database

        database.close_conn()
        self.database = importlib.reload(database)
        self.database.init_db()

    def tearDown(self):
        self.database.close_conn()
        self.tmp.cleanup()
        os.environ.pop("MASTERBALL_DB_PATH", None)

    def test_candidate_dedupe_and_approval_flow(self):
        candidate = {
            "retailer": "bestbuy",
            "name": "Pokemon TCG Booster Bundle - Best Buy CA",
            "url": "https://www.bestbuy.ca/en-ca/product/example/12345678?icmp=tracking",
            "product_id": "12345678",
            "source": "test",
            "confidence": 0.9,
            "priority": "high",
        }

        stored, inserted = self.database.add_or_update_candidate(candidate)
        self.assertTrue(inserted)
        self.assertEqual(stored["status"], "pending")

        again, inserted_again = self.database.add_or_update_candidate({
            **candidate,
            "url": "https://www.bestbuy.ca/en-ca/product/example/12345678",
        })
        self.assertFalse(inserted_again)
        self.assertEqual(again["id"], stored["id"])

        approved = self.database.set_candidate_status(stored["id"], "approved")
        self.assertEqual(approved["status"], "approved")

        products = self.database.get_approved_products()
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["priority"], "high")
        self.assertEqual(products[0]["candidate_id"], stored["id"])


if __name__ == "__main__":
    unittest.main()
