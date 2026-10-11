"""Build exchange-session and benchmark/cash-return candidates from public sources."""
import csv
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars as xcals

from .download import fetch
from .engine import number, read_csv, write_csv


FRED_URL = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS3MO'


def build_sessions(data='data', start='2009-01-01', end='2026-01-01',
                   benchmark='data/yahoo/INDEX_SP500TR.csv', out='data/candidates/sessions.csv'):
    data, benchmark, out = Path(data), Path(benchmark), Path(out)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date >= end_date:
        raise ValueError('Start must precede exclusive end')
    if not benchmark.exists():
        raise ValueError('Missing real S&P 500 total-return price series: ' + str(benchmark))

    cash_path = data / 'source' / 'DGS3MO.csv'
    cash_bytes = fetch(FRED_URL, cash_path, 'Form4Research/0.1 public research data')
    with cash_path.open(encoding='utf-8-sig', newline='') as handle:
        cash_rows = list(csv.DictReader(handle))
    yields = {}
    for row in cash_rows:
        raw = row.get('DGS3MO', '')
        if raw and raw != '.':
            value = number(raw)
            if value < 0:
                raise ValueError('Unexpected negative 3-month Treasury yield: ' + row['observation_date'])
            yields[date.fromisoformat(row['observation_date'])] = value
    if not yields:
        raise ValueError('FRED cash-yield source contained no usable observations')

    benchmark_rows = read_csv(benchmark)
    levels = {}
    for row in benchmark_rows:
        day = date.fromisoformat(row['date'])
        if start_date <= day < end_date and row.get('close'):
            value = number(row['close'])
            if value <= 0:
                raise ValueError('Invalid benchmark index level: ' + row['date'])
            levels[day] = value

    calendar = xcals.get_calendar('XNYS', start=start, end=end)
    schedule = calendar.schedule
    sessions = []
    previous_day = None
    previous_benchmark = None
    cash_dates = sorted(yields)
    yield_index = 0
    previous_yield = None
    for session_label, times in schedule.iterrows():
        day = session_label.date()
        if day < start_date or day >= end_date:
            continue
        if day not in levels:
            raise ValueError('Missing total-return benchmark level on exchange session: ' + day.isoformat())
        while yield_index < len(cash_dates) and cash_dates[yield_index] <= day:
            previous_yield = yields[cash_dates[yield_index]]
            yield_index += 1
        if previous_yield is None:
            raise ValueError('No prior FRED Treasury yield for session: ' + day.isoformat())
        elapsed_days = (day - previous_day).days if previous_day else 0
        cash_return = previous_yield / 100 * elapsed_days / 365 if previous_day else 0.0
        sp500_return = levels[day] / previous_benchmark - 1 if previous_benchmark else 0.0
        sessions.append(dict(date=day.isoformat(), open_at=times['open'].isoformat(),
                             close_at=times['close'].isoformat(), cash_return=cash_return,
                             sp500_return=sp500_return))
        previous_day = day
        previous_benchmark = levels[day]

    if len(sessions) < 60:
        raise ValueError('Generated session history is too short')
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, sessions)
    manifest = dict(status='candidate_sessions_cash_and_benchmark_not_independently_validated',
                    start=start_date.isoformat(), end_exclusive=end_date.isoformat(),
                    exchange_calendar='XNYS via exchange_calendars', session_count=len(sessions),
                    benchmark_path=str(benchmark),
                    benchmark_sha256=hashlib.sha256(benchmark.read_bytes()).hexdigest(),
                    cash_source=FRED_URL, cash_source_sha256=hashlib.sha256(cash_bytes).hexdigest(),
                    cash_method='Previous available DGS3MO investment-basis yield times calendar days/365; approximate bill-roll cash accrual.',
                    generated_at=datetime.now(timezone.utc).isoformat(), output=str(out),
                    limitations=['Exchange schedule is generated from exchange_calendars and requires independent spot checks.',
                                 'Yahoo ^SP500TR close levels are used for the benchmark and need vendor-level reconciliation.',
                                 'Daily 3-month Treasury yields are a cash proxy, not a realized bill total-return index.',
                                 'Cash return on the first included session is set to zero because no prior NAV session is included.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest