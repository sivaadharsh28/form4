import argparse
import json
import sys
from pathlib import Path

from .download import download
from .engine import Config, run
from .complete_universe import build_complete_universe, build_effective_membership, filter_sec_candidates, filter_top10_sec_candidates
from .membership import SOURCE_URL, build_ticker_intervals
from .prices import build_historical_ticker_prices, normalize_prices, seed_symbol_mapping
from .sec import filing, index
from .sec_bulk import download_and_screen
from .sec_enrich import enrich_candidates
from .sec_review import promote_reviewed_candidates
from .security_map import build_observed_crosswalk
from .sessions import build_sessions
from .verify import verify
from .download import fetch as fetch_source
from .dataset import validate_and_assemble


def main():
    parser = argparse.ArgumentParser(description='Insider purchase research; real inputs only')
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('download', help='Download real Yahoo prices and retain raw responses')
    fetch.add_argument('--data', default='data')
    fetch.add_argument('--start', default='2009-01-01')
    fetch.add_argument('--end', default='2026-01-01', help='Exclusive end date')
    fetch.add_argument('--symbols', nargs='+')
    price_map = commands.add_parser('price-map', help='Create a current-symbol mapping requiring share-class review')
    price_map.add_argument('--data', default='data')
    price_map.add_argument('--out', default='data/symbol_mapping.csv')
    normalize = commands.add_parser('normalize-prices', help='Create split-normalized candidate bars from cached Yahoo responses')
    normalize.add_argument('--data', default='data')
    normalize.add_argument('--mapping', default='data/symbol_mapping.csv')
    normalize.add_argument('--out', default='data/candidates/prices.csv')
    historical_prices = commands.add_parser('historical-prices', help='Fetch and normalize prices for historical membership tickers')
    historical_prices.add_argument('--data', default='data')
    historical_prices.add_argument('--membership', default='data/candidates/membership_ticker.csv')
    historical_prices.add_argument('--start', default='2015-01-01')
    historical_prices.add_argument('--end', default='2026-01-01', help='Exclusive end date')
    historical_prices.add_argument('--out', default='data/candidates/historical_prices_ticker.csv')
    check = commands.add_parser('verify', help='Verify downloaded source provenance and Yahoo CSV integrity')
    check.add_argument('--data', default='data')
    membership = commands.add_parser('membership-candidate', help='Build a ticker-only historical membership candidate')
    membership.add_argument('--data', default='data')
    membership.add_argument('--source')
    membership.add_argument('--out', default='data/candidates/membership_ticker.csv')
    membership.add_argument('--start', default='2016-01-04')
    membership.add_argument('--end', default='2025-12-23', help='Exclusive end; last snapshot currently 2025-12-22')
    sec_index = commands.add_parser('sec-index', help='Cache an original EDGAR quarterly index')
    sec_index.add_argument('--data', default='data')
    sec_index.add_argument('--year', type=int, required=True)
    sec_index.add_argument('--quarter', type=int, choices=[1, 2, 3, 4], required=True)
    sec_index.add_argument('--email', required=True)
    sec_filing = commands.add_parser('sec-filing', help='Download original submission and extract purchases for review')
    sec_filing.add_argument('--data', default='data')
    sec_filing.add_argument('--path', required=True)
    sec_filing.add_argument('--email', required=True)
    sec_bulk = commands.add_parser('sec-bulk', help='Download official quarterly SEC ownership data and screen review candidates')
    sec_bulk.add_argument('--data', default='data')
    sec_bulk.add_argument('--out', default='data/candidates/sec_purchase_review_candidates.csv')
    sec_bulk.add_argument('--start-year', type=int, default=2016)
    sec_bulk.add_argument('--end-year', type=int, default=2025)
    sec_bulk.add_argument('--min-purchase', type=float, default=100_000)
    sec_bulk.add_argument('--membership', default='data/candidates/membership_ticker.csv')
    sec_bulk.add_argument('--email', required=True)
    sec_enrich = commands.add_parser('sec-enrich', help='Attach original SEC acceptance timestamps to review candidates')
    sec_enrich.add_argument('--data', default='data')
    sec_enrich.add_argument('--candidates', default='data/candidates/sec_top10_review_queue.csv')
    sec_enrich.add_argument('--out', default='data/candidates/sec_original_review.csv')
    sec_enrich.add_argument('--email', required=True)
    promote = commands.add_parser('promote-sec-reviews', help='Validate explicit manual filing decisions and write engine signals.csv')
    promote.add_argument('--review', default='data/candidates/sec_original_review.csv')
    promote.add_argument('--out', default='data/normalized/signals.csv')
    promote.add_argument('--audit-out', default='data/normalized/signals_review_audit.csv')
    crosswalk = commands.add_parser('security-map-candidate', help='Derive observed historical ticker-to-CIK candidates from SEC bulk data')
    crosswalk.add_argument('--data', default='data')
    crosswalk.add_argument('--membership', default='data/candidates/membership_ticker.csv')
    crosswalk.add_argument('--start-year', type=int, default=2016)
    crosswalk.add_argument('--end-year', type=int, default=2025)
    crosswalk.add_argument('--out', default='data/candidates/ticker_cik_observations.csv')
    complete = commands.add_parser('complete-universe', help='Reconcile membership and gate the backtest universe on complete data')
    complete.add_argument('--data', default='data')
    complete.add_argument('--snapshots', default='data/source/sp500_historical_updated.csv')
    complete.add_argument('--changes', default='data/source/sp500_changes_since_2019.csv')
    complete.add_argument('--crosswalk', default='data/candidates/ticker_cik_observations.csv')
    complete.add_argument('--prices', default='data/candidates/historical_prices_ticker.csv')
    complete.add_argument('--sessions', default='data/candidates/sessions.csv')
    complete.add_argument('--start', default='2021-01-04')
    complete.add_argument('--end', default='2026-01-01', help='Exclusive end date')
    complete.add_argument('--warmup-sessions', type=int, default=60)
    complete.add_argument('--effective-membership', default='data/candidates/membership_effective_ticker.csv')
    complete.add_argument('--membership-out', default='data/candidates/membership_complete.csv')
    complete.add_argument('--prices-out', default='data/candidates/prices_complete.csv')
    complete.add_argument('--audit-out', default='data/candidates/complete_universe_audit.csv')
    complete.add_argument('--top10-out', default='data/candidates/top10_liquidity_candidate.csv')
    complete.add_argument('--sec-candidates', default='data/candidates/sec_purchase_review_candidates.csv')
    top10_queue = commands.add_parser('top10-sec-queue', help='Limit original Form 4 review to next-session top-10 liquidity candidates')
    top10_queue.add_argument('--candidates', default='data/candidates/sec_complete_candidates.csv')
    top10_queue.add_argument('--top10', default='data/candidates/top10_liquidity_candidate.csv')
    top10_queue.add_argument('--sessions', default='data/candidates/sessions.csv')
    top10_queue.add_argument('--out', default='data/candidates/sec_top10_review_queue.csv')
    top10_queue.add_argument('--excluded-out', default='data/candidates/sec_top10_review_excluded.csv')
    sessions_cmd = commands.add_parser('session-candidate', help='Build exchange sessions with benchmark and Treasury cash candidates')
    sessions_cmd.add_argument('--data', default='data')
    sessions_cmd.add_argument('--start', default='2009-01-01')
    sessions_cmd.add_argument('--end', default='2026-01-01', help='Exclusive end date')
    sessions_cmd.add_argument('--benchmark', default='data/yahoo/INDEX_SP500TR.csv')
    sessions_cmd.add_argument('--out', default='data/candidates/sessions.csv')
    prepare = commands.add_parser('prepare-backtest', help='Validate and assemble complete-data candidate inputs')
    prepare.add_argument('--out', default='data/normalized')
    prepare.add_argument('--start', default='2021-01-04')
    prepare.add_argument('--end', default='2025-12-31')
    prepare.add_argument('--hold', type=int, default=60)
    prepare.add_argument('--delay-minutes', type=int, default=60)
    backtest = commands.add_parser('backtest')
    backtest.add_argument('--data', default='data/normalized')
    backtest.add_argument('--out', default='output')
    backtest.add_argument('--start', default='2010-01-01')
    backtest.add_argument('--end', default='2018-12-31')
    backtest.add_argument('--hold', type=int, choices=[20, 60, 120], default=60)
    backtest.add_argument('--cost-bps', type=float, default=10)
    backtest.add_argument('--capital', type=float, default=1_000_000)
    backtest.add_argument('--sector-cap', type=float)
    backtest.add_argument('--allow-final-test', action='store_true')
    args = parser.parse_args()
    if args.command == 'download':
        result = download(args.data, args.start, args.end, args.symbols)
    elif args.command == 'price-map':
        result = seed_symbol_mapping(args.data, args.out)
    elif args.command == 'normalize-prices':
        result = normalize_prices(args.data, args.mapping, args.out)
    elif args.command == 'historical-prices':
        result = build_historical_ticker_prices(args.data, args.membership, args.start, args.end, args.out)
    elif args.command == 'verify':
        result = verify(args.data)
    elif args.command == 'membership-candidate':
        source = args.source or str(Path(args.data) / 'source' / 'sp500_historical_updated.csv')
        fetch_source(SOURCE_URL, source)
        result = build_ticker_intervals(source, args.out, args.start, args.end)
    elif args.command == 'sec-index':
        result = {'filings': len(index(args.data, args.year, args.quarter, args.email))}
    elif args.command == 'sec-filing':
        result = filing(args.data, args.path, args.email)
    elif args.command == 'sec-bulk':
        result = download_and_screen(args.data, args.out, args.start_year, args.end_year,
                                     args.min_purchase, args.email, args.membership)
    elif args.command == 'session-candidate':
        result = build_sessions(args.data, args.start, args.end, args.benchmark, args.out)
    elif args.command == 'sec-enrich':
        result = enrich_candidates(args.data, args.candidates, args.out, args.email)
    elif args.command == 'promote-sec-reviews':
        result = promote_reviewed_candidates(args.review, args.out, args.audit_out)
    elif args.command == 'security-map-candidate':
        result = build_observed_crosswalk(args.data, args.membership, args.start_year,
                                          args.end_year, args.out)
    elif args.command == 'complete-universe':
        fetch_source('https://raw.githubusercontent.com/fja05680/sp500/master/sp500_changes_since_2019.csv',
                     args.changes)
        membership_result = build_effective_membership(args.snapshots, args.changes,
                                                       args.effective_membership, args.start, args.end)
        universe_result = build_complete_universe(
            args.effective_membership, args.crosswalk, args.prices, args.sessions,
            args.start, args.end, args.warmup_sessions, args.membership_out,
            args.prices_out, args.audit_out, args.top10_out)
        result = dict(membership=membership_result, complete_universe=universe_result)
        if Path(args.sec_candidates).exists():
            result['sec_candidates'] = filter_sec_candidates(
                args.sec_candidates, args.membership_out,
                'data/candidates/sec_complete_candidates.csv',
                'data/candidates/sec_complete_candidates_excluded.csv')
    elif args.command == 'top10-sec-queue':
        result = filter_top10_sec_candidates(args.candidates, args.top10, args.sessions,
                                             args.out, args.excluded_out)
    elif args.command == 'prepare-backtest':
        result = validate_and_assemble(args.out, args.start, args.end, args.hold,
                                       args.delay_minutes)
    else:
        result = run(args.data, args.out, args.start, args.end,
                Config(hold=args.hold, cost_bps=args.cost_bps, capital=args.capital,
                    sector_cap=args.sector_cap), args.allow_final_test)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
