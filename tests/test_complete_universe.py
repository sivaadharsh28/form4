import csv
import tempfile
import unittest
from pathlib import Path

from form4.complete_universe import build_complete_universe, build_effective_membership


class CompleteUniverseTests(unittest.TestCase):
    def test_change_log_builds_effective_intervals_and_reconciles_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshots = root / 'snapshots.csv'
            changes = root / 'changes.csv'
            output = root / 'membership.csv'
            snapshots.write_text(
                'date,tickers\n'
                '2020-12-21,"AAA,BBB"\n'
                '2021-01-04,"AAA,BBB"\n'
                '2021-02-01,"BBB,CCC"\n'
                '2021-03-01,"BBB,CCC"\n', encoding='utf-8')
            changes.write_text('date,add,remove\n2021-01-15,CCC,AAA\n', encoding='utf-8')

            manifest = build_effective_membership(snapshots, changes, output,
                                                  '2021-01-04', '2021-03-02')

            with output.open(encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
            actual = {(row['ticker'], row['start'], row['end']) for row in rows}
            self.assertEqual(actual, {
                ('AAA', '2021-01-04', '2021-01-15'),
                ('BBB', '2021-01-04', '2021-03-02'),
                ('CCC', '2021-01-15', '2021-03-02'),
            })
            self.assertEqual(manifest['snapshot_mismatches'], 0)

    def test_gate_excludes_missing_prices_and_ambiguous_cik(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            membership = root / 'membership.csv'
            crosswalk = root / 'crosswalk.csv'
            prices = root / 'prices.csv'
            sessions = root / 'sessions.csv'
            member_rows = [
                dict(ticker=ticker, start='2020-12-28', end='2021-01-07',
                     boundary_basis='dated_change_log')
                for ticker in ('AAA', 'BBB', 'CCC')
            ]
            crosswalk_rows = [
                dict(ticker=ticker, ticker_key=ticker, issuer=cik,
                     first_filing_observed='2020-01-01', last_filing_observed='2021-01-01',
                     filing_observations='2', mapping_status='single_observed_cik_candidate')
                for ticker, cik in [('AAA', '0000000001'), ('BBB', '0000000002'), ('CCC', '0000000003')]
            ]
            crosswalk_rows.append(dict(ticker='CCC', ticker_key='CCC', issuer='0000000004',
                                       first_filing_observed='2020-01-01', last_filing_observed='2021-01-01',
                                       filing_observations='1', mapping_status='ambiguous_ticker_maps_to_multiple_ciks'))
            days = ['2020-12-28', '2020-12-29', '2020-12-30', '2020-12-31',
                    '2021-01-04', '2021-01-05', '2021-01-06']
            session_rows = [dict(date=day, open_at=day + 'T14:30:00+00:00',
                                 close_at=day + 'T21:00:00+00:00', cash_return=0, sp500_return=0)
                            for day in days]
            price_rows = []
            for ticker in ('AAA', 'BBB', 'CCC'):
                for day in days:
                    if ticker == 'BBB' and day == '2021-01-06':
                        continue
                    price_rows.append(dict(date=day, ticker=ticker, open=10, close=10,
                                           volume=1_000_000, split=1, dividend=0, terminal_cash=''))
            for path, rows in ((membership, member_rows), (crosswalk, crosswalk_rows),
                               (sessions, session_rows), (prices, price_rows)):
                with path.open('w', encoding='utf-8', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)

            manifest = build_complete_universe(membership, crosswalk, prices, sessions,
                                               '2021-01-05', '2021-01-07', 2,
                                               root / 'membership_complete.csv',
                                               root / 'prices_complete.csv',
                                               root / 'audit.csv', root / 'top10.csv')

            with (root / 'audit.csv').open(encoding='utf-8', newline='') as handle:
                audit = {row['ticker']: row for row in csv.DictReader(handle)}
            self.assertEqual(manifest['candidate_tickers'], 1)
            self.assertEqual(audit['AAA']['status'], 'complete_candidate')
            self.assertIn('missing_exchange_session_prices', audit['BBB']['reasons'])
            self.assertIn('ambiguous_ticker_to_cik', audit['CCC']['reasons'])


if __name__ == '__main__':
    unittest.main()