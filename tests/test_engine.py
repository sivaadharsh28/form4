import tempfile
import unittest
from pathlib import Path

from form4.engine import Config, run


class EngineStrategyConfigTests(unittest.TestCase):
    def test_pe_filter_is_absent_and_earnings_are_not_required(self):
        self.assertFalse(hasattr(Config(), 'min_pe'))
        self.assertFalse(hasattr(Config(), 'max_pe'))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'missing validated historical inputs') as error:
                run(directory, Path(directory) / 'out', '2021-01-04', '2022-12-30')

        message = str(error.exception)
        self.assertIn('sessions.csv', message)
        self.assertIn('prices.csv', message)
        self.assertIn('membership.csv', message)
        self.assertIn('signals.csv', message)
        self.assertNotIn('earnings.csv', message)


if __name__ == '__main__':
    unittest.main()
