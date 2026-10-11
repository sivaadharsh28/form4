import csv
import tempfile
import unittest
from pathlib import Path

from form4.membership import build_ticker_intervals


class MembershipCandidateTests(unittest.TestCase):
    def test_carries_snapshot_membership_and_closes_intervals_exclusively(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'snapshots.csv'
            output = Path(directory) / 'candidate.csv'
            source.write_text(
                'date,tickers\n'
                '2015-12-31,"AAA,BBB"\n'
                '2016-01-04,"AAA,BBB"\n'
                '2016-04-01,"AAA,CCC"\n', encoding='utf-8')

            manifest = build_ticker_intervals(source, output, '2016-01-04', '2016-07-01')

            with output.open(encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            actual = {(row['ticker'], row['start'], row['end']) for row in rows}
            self.assertEqual(actual, {
                ('AAA', '2016-01-04', '2016-07-01'),
                ('BBB', '2016-01-04', '2016-04-01'),
                ('CCC', '2016-04-01', '2016-07-01'),
            })
            self.assertEqual(manifest['status'], 'ticker_membership_candidate_not_engine_ready')

    def test_rejects_unsorted_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'snapshots.csv'
            output = Path(directory) / 'candidate.csv'
            source.write_text('date,tickers\n2016-01-04,AAA\n2016-01-03,AAA\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'unique and sorted'):
                build_ticker_intervals(source, output, '2016-01-04', '2016-02-01')

    def test_rejects_duplicate_ticker_in_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'snapshots.csv'
            output = Path(directory) / 'candidate.csv'
            source.write_text('date,tickers\n2016-01-04,"AAA,AAA"\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'duplicate symbols'):
                build_ticker_intervals(source, output, '2016-01-04', '2016-02-01')


if __name__ == '__main__':
    unittest.main()