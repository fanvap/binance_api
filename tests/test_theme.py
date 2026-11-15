import unittest

from app.frontend.theme import chart_stylesheet, dashboard_stylesheet, login_stylesheet


class ThemeStylesheetTests(unittest.TestCase):
    def test_day_and_night_have_distinct_dashboard_palettes(self):
        dark = dashboard_stylesheet("dark")
        light = dashboard_stylesheet("light")

        self.assertIn("#0b0e11", dark)
        self.assertIn("#f3f5f7", light)
        self.assertIn("#3b82f6", dark)
        self.assertIn("QTableWidget#strategyStatisticsTable::item:selected", dark)
        self.assertIn("selection-color: #ffffff", dark)
        self.assertIn("selection-color: #1e2329", light)
        self.assertNotIn("#f0b90b", dark)
        self.assertNotEqual(dark, light)

    def test_chart_and_login_stylesheets_support_both_modes(self):
        for mode in ("dark", "light"):
            self.assertTrue(chart_stylesheet(mode))
            self.assertTrue(login_stylesheet(mode))


if __name__ == "__main__":
    unittest.main()
