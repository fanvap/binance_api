import unittest

from app.backend.positions import PositionSnapshot


class PositionSnapshotTests(unittest.TestCase):
    def test_long_unrealized_pnl_is_positive_when_mark_rises(self):
        position = PositionSnapshot(
            "BTCUSDT", "LONG", 100.0, 110.0, 2.0, leverage=2
        )
        self.assertAlmostEqual(position.unrealized_pnl, 20.0)
        self.assertEqual(position.notional_usdt, 200.0)
        self.assertEqual(position.margin_usdt, 100.0)

    def test_short_unrealized_pnl_is_positive_when_mark_falls(self):
        position = PositionSnapshot("BTCUSDT", "SHORT", 100.0, 90.0, 2.0)
        self.assertAlmostEqual(position.unrealized_pnl, 20.0)

    def test_invalid_side_is_rejected(self):
        with self.assertRaises(ValueError):
            PositionSnapshot("BTCUSDT", "BUY", 100.0, 110.0, 1.0)


if __name__ == "__main__":
    unittest.main()
