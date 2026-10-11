"""Validate and assemble candidate research tables into the engine input directory."""
import csv
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import number, read_csv, timestamp, write_csv


INPUTS = {
    'sessions.csv': Path('data/candidates/sessions.csv'),
    'prices.csv': Path('data/candidates/prices_complete.csv'),
    'membership.csv': Path('data/candidates/membership_complete.csv'),
    'signals.csv': Path('data/normalized/signals.csv'),
}
CURRENT_SECTORS = Path('data/current_constituents.csv')


def validate_and_assemble(out='data/normalized', start='2021-01-04', end='2025-12-31',
                          hold=60, delay_minutes=60):
    out = Path(out)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date:
        raise ValueError('Start must not follow end')
    if hold < 1 or delay_minutes < 0:
        raise ValueError('Hold must be positive and processing delay nonnegative')
    missing_files = [str(path) for path in INPUTS.values() if not path.exists()]
    if not CURRENT_SECTORS.exists():
        missing_files.append(str(CURRENT_SECTORS))
    if missing_files:
        raise ValueError('Missing candidate input files: ' + ', '.join(missing_files))

    current_sector_rows = read_csv(CURRENT_SECTORS)
    if not current_sector_rows or not {'symbol', 'current_sector'}.issubset(current_sector_rows[0]):
        raise ValueError('Current constituent sector mapping schema is invalid')
    current_sector_by_ticker = {row['symbol']: row['current_sector'] for row in current_sector_rows
                                if row['symbol'] and row['current_sector']}

    sessions = read_csv(INPUTS['sessions.csv'])
    required_session_columns = {'date', 'open_at', 'close_at', 'cash_return', 'sp500_return'}
    if not sessions or not required_session_columns.issubset(sessions[0]):
        raise ValueError('Session candidate schema is invalid')
    session_dates = [row['date'] for row in sessions]
    if session_dates != sorted(set(session_dates)):
        raise ValueError('Session dates must be unique and sorted')
    session_index = {day: index for index, day in enumerate(session_dates)}
    open_times = {}
    for row in sessions:
        opened, closed = timestamp(row['open_at']), timestamp(row['close_at'])
        if closed <= opened:
            raise ValueError('Invalid session open/close: ' + row['date'])
        number(row['cash_return'])
        number(row['sp500_return'])
        open_times[row['date']] = opened
    test_indices = [i for i, day in enumerate(session_dates) if start <= day <= end]
    if not test_indices or test_indices[0] < 60:
        raise ValueError('Backtest period needs at least 60 prior sessions')
    first_test, last_test = test_indices[0], test_indices[-1]

    memberships = read_csv(INPUTS['membership.csv'])
    required_membership_columns = {'issuer', 'start', 'end', 'sector'}
    if not memberships or not required_membership_columns.issubset(memberships[0]):
        raise ValueError('Membership candidate schema is invalid')
    intervals = defaultdict(list)
    for row in memberships:
        if not row['issuer'] or row['start'] >= row['end']:
            raise ValueError('Invalid membership interval: ' + str(row))
        intervals[row['issuer']].append((row['start'], row['end']))
    for issuer, issuer_intervals in intervals.items():
        ordered = sorted(issuer_intervals)
        if any(ordered[index][0] < ordered[index - 1][1] for index in range(1, len(ordered))):
            raise ValueError('Overlapping membership intervals for ' + issuer)

    bars = {}
    issuer_dates = defaultdict(set)
    with INPUTS['prices.csv'].open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            issuer, day = row.get('issuer', ''), row.get('date', '')
            key = (day, issuer)
            if not issuer or day not in session_index:
                raise ValueError('Price row has an unknown issuer or exchange session: ' + str(key))
            if key in bars:
                raise ValueError('Duplicate price row: ' + str(key))
            values = {field: number(row[field]) for field in ('open', 'close', 'volume', 'split', 'dividend')}
            if min(values['open'], values['close'], values['split']) <= 0 or min(values['volume'], values['dividend']) < 0:
                raise ValueError('Invalid price/action values: ' + str(key))
            terminal = row.get('terminal_cash', '')
            if terminal:
                if number(terminal) < 0:
                    raise ValueError('Negative terminal settlement: ' + str(key))
            bars[key] = row
            issuer_dates[issuer].add(day)

    signals = read_csv(INPUTS['signals.csv'])
    signal_keys = set()
    for signal in signals:
        key = (signal.get('accession', ''), signal.get('insider', ''), signal.get('issuer', ''))
        if key in signal_keys:
            raise ValueError('Duplicate signal: ' + str(key))
        signal_keys.add(key)
        if signal.get('review') != 'approved_open_market_common':
            raise ValueError('Unapproved signal present: ' + str(key))
        accepted = timestamp(signal['accepted_at'])
        acceptance_date = accepted.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        if signal['acceptance_date'] != acceptance_date:
            raise ValueError('Acceptance date does not match SEC New York date: ' + str(key))
        if number(signal['purchase_value']) < 100_000:
            raise ValueError('Approved signal is below purchase threshold: ' + str(key))
        target = next((i for i, row in enumerate(sessions)
                       if row['date'] > acceptance_date and
                       open_times[row['date']] >= accepted + timedelta(minutes=delay_minutes)), None)
        if target is None or target < first_test or target > last_test:
            raise ValueError('Approved signal has no entry session in the test window: ' + str(key))
        entry_day = session_dates[target]
        issuer = signal['issuer']
        if not any(member_start <= entry_day < member_end for member_start, member_end in intervals.get(issuer, [])):
            raise ValueError('Approved signal issuer is not an S&P member at entry: ' + str(key))
        if (entry_day, issuer) not in bars:
            raise ValueError('Missing entry price for approved signal: ' + str(key))
        warmup_days = session_dates[target - 60:target]
        if any((day, issuer) not in bars for day in warmup_days):
            raise ValueError('Missing 60-session ADV warmup for approved signal: ' + str(key))

    out.mkdir(parents=True, exist_ok=True)
    hashes = {}
    sector_matches = 0
    sector_unmatched = 0
    for name, source in INPUTS.items():
        destination = out / name
        if name == 'membership.csv':
            membership_rows = read_csv(source)
            for row in membership_rows:
                current_sector = current_sector_by_ticker.get(row.get('ticker', ''))
                if current_sector:
                    row['sector'] = current_sector
                    sector_matches += 1
                else:
                    sector_unmatched += 1
            write_csv(destination, membership_rows)
        elif source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        hashes[name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    manifest = dict(status='candidate_bundle_passed_structural_validation_not_independent_source_validation',
                    start=start, end=end, hold_sessions=hold, delay_minutes=delay_minutes,
                    session_count=len(sessions), price_rows=len(bars), issuer_count=len(intervals),
                    approved_signals=len(signals), source_sha256=hashes,
                    current_sector_source=str(CURRENT_SECTORS),
                    current_sector_source_sha256=hashlib.sha256(CURRENT_SECTORS.read_bytes()).hexdigest(),
                    current_sector_match_membership_rows=sector_matches,
                    current_sector_unmatched_membership_rows=sector_unmatched,
                    inputs={name: str(path) for name, path in INPUTS.items()},
                    output=str(out),
                    limitations=['Structural checks do not independently prove Yahoo prices, source membership accuracy, or cash-return realism.',
                                 'The universe intentionally excludes companies without complete retrieved price histories and is survivorship/data-availability biased.',
                                 'P/E and analyst-rating filters are intentionally absent.',
                                 'Current constituent sector labels are applied retroactively by ticker and may differ from historical classifications; the engine sector cap is disabled.',
                                 "Manually approved signal decisions remain the reviewer's responsibility."])
    (out / 'assembly_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest
