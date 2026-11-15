import unittest

from app.backend.trade_statistics import StrategyTradeStatistics


class StrategyTradeStatisticsTests(unittest.TestCase):
    def test_records_wins_losses_breakeven_and_unknown_closures(self):
        statistics = StrategyTradeStatistics()

        statistics.record_open("Simulation", "short_p")
        statistics.record_open("Simulation", "short_p")
        statistics.record_close("Simulation", "short_p", 2.5)
        statistics.record_close("Simulation", "short_p", -1.25)
        statistics.record_close("Simulation", "short_p", 0.0)
        statistics.record_close("Simulation", "short_p", None)
        statistics.record_open("Testnet", "long_p")
        statistics.record_close("Testnet", "long_p", 3.0)

        simulation, testnet = statistics.snapshot()
        self.assertEqual(simulation.opened, 2)
        self.assertEqual(simulation.closed, 4)
        self.assertEqual((simulation.wins, simulation.losses), (1, 1))
        self.assertEqual((simulation.breakeven, simulation.unknown_result), (1, 1))
        self.assertEqual(simulation.win_rate, 50.0)
        self.assertEqual(simulation.winning_pnl, 2.5)
        self.assertEqual(simulation.losing_pnl, 1.25)
        self.assertEqual(simulation.net_pnl, 1.25)
        self.assertEqual(testnet.mode, "Testnet")
        self.assertEqual(testnet.strategy, "long_p")
        self.assertEqual(testnet.win_rate, 100.0)

    def test_separates_results_by_candle_interval(self):
        statistics = StrategyTradeStatistics()

        statistics.record_open("Simulation", "short_p", "1m")
        statistics.record_close("Simulation", "short_p", 2.0, "1m")
        statistics.record_open("Simulation", "short_p", "5m")
        statistics.record_close("Simulation", "short_p", -1.0, "5m")

        rows = {row.timeframe: row for row in statistics.snapshot()}
        self.assertEqual(rows["1m"].wins, 1)
        self.assertEqual(rows["1m"].net_pnl, 2.0)
        self.assertEqual(rows["5m"].losses, 1)
        self.assertEqual(rows["5m"].net_pnl, -1.0)


if __name__ == "__main__":
    unittest.main()
