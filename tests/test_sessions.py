import unittest

from form4.sessions import FRED_URL


class SessionSourceTests(unittest.TestCase):
    def test_cash_source_is_daily_treasury_series(self):
        self.assertEqual(FRED_URL, 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO')


if __name__ == '__main__':
    unittest.main()