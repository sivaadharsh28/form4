import unittest

from form4.security_map import normalize_ticker


class SecurityMapTests(unittest.TestCase):
    def test_normalizes_yahoo_and_historical_class_symbols(self):
        self.assertEqual(normalize_ticker('BF.B'), 'BFB')
        self.assertEqual(normalize_ticker('BF-B'), 'BFB')
        self.assertEqual(normalize_ticker('brk.b'), 'BRKB')


if __name__ == '__main__':
    unittest.main()