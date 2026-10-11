import unittest
from datetime import datetime, timezone

from form4.prices import normalized_rows


def epoch(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())


class NormalizedRowsTests(unittest.TestCase):
    def test_reverses_future_splits_for_prices_volume_and_dividends(self):
        chart = {
            'timestamp': [epoch(day) for day in ('2009-01-02', '2014-06-09',
                                                  '2020-08-31', '2021-01-04')],
            'indicators': {'quote': [{'open': [10 / 28, 10 / 4, 10, 10],
                                      'close': [11 / 28, 11 / 4, 11, 11],
                                      'volume': [2800, 400, 100, 100]}]},
            'events': {
                'splits': {
                    str(epoch('2014-06-09')): {'numerator': 7, 'denominator': 1},
                    str(epoch('2020-08-31')): {'numerator': 4, 'denominator': 1},
                },
                'dividends': {
                    str(epoch('2009-01-02')): {'amount': 2.65 / 28},
                },
            },
        }

        rows, missing = normalized_rows(chart, 'issuer-1')

        self.assertEqual(missing, 0)
        self.assertAlmostEqual(rows[0]['open'], 10)
        self.assertAlmostEqual(rows[0]['close'], 11)
        self.assertAlmostEqual(rows[0]['volume'], 100)
        self.assertAlmostEqual(rows[0]['dividend'], 2.65)
        self.assertEqual(rows[1]['split'], 7)
        self.assertEqual(rows[2]['split'], 4)
        self.assertEqual(rows[3]['split'], 1)

    def test_rejects_action_without_matching_session(self):
        chart = {
            'timestamp': [epoch('2020-08-28')],
            'indicators': {'quote': [{'open': [10], 'close': [10], 'volume': [100]}]},
            'events': {'splits': {
                str(epoch('2020-08-31')): {'numerator': 4, 'denominator': 1},
            }},
        }

        with self.assertRaisesRegex(ValueError, 'no matching price observation'):
            normalized_rows(chart, 'issuer-1')

    def test_counts_missing_quote_rows_without_filling_them(self):
        chart = {
            'timestamp': [epoch('2020-08-28'), epoch('2020-08-31')],
            'indicators': {'quote': [{'open': [10, None], 'close': [10, 10],
                                      'volume': [100, 100]}]},
        }

        rows, missing = normalized_rows(chart, 'issuer-1')

        self.assertEqual(missing, 1)
        self.assertEqual([row['date'] for row in rows], ['2020-08-28'])


if __name__ == '__main__':
    unittest.main()