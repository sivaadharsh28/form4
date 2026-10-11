"""Build ticker-level membership candidates from historical constituent snapshots."""
import csv
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from .engine import read_csv, write_csv

SOURCE_URL = ('https://raw.githubusercontent.com/fja05680/sp500/master/'
              'S%26P%20500%20Historical%20Components%20%26%20Changes%20%28Updated%29.csv')


def _parse_date(value):
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError('Invalid snapshot date: ' + value) from exc
    if parsed.isoformat() != value:
        raise ValueError('Snapshot dates must use YYYY-MM-DD: ' + value)
    return parsed


def build_ticker_intervals(source, out, start='2016-01-04', end='2025-12-23'):
    source, out = Path(source), Path(out)
    start_date, end_date = _parse_date(start), _parse_date(end)
    if start_date >= end_date:
        raise ValueError('Start must precede exclusive end')

    rows = read_csv(source)
    if not rows or not {'date', 'tickers'}.issubset(rows[0]):
        raise ValueError('Source must contain date,tickers columns')
    parsed = []
    previous_date = None
    for row in rows:
        snapshot_date = _parse_date(row['date'])
        if previous_date is not None and snapshot_date <= previous_date:
            raise ValueError('Snapshot dates must be unique and sorted')
        tickers = [ticker.strip() for ticker in row['tickers'].split(',') if ticker.strip()]
        if not tickers or len(tickers) != len(set(tickers)):
            raise ValueError('Snapshot has no tickers or duplicate symbols: ' + row['date'])
        parsed.append((snapshot_date, set(tickers)))
        previous_date = snapshot_date

    prior = [(day, symbols) for day, symbols in parsed if day <= start_date]
    if not prior:
        raise ValueError('No snapshot on or before requested start date')
    prior_date, active = prior[-1]
    if (start_date - prior_date).days > 31:
        raise ValueError('Nearest initial snapshot is more than 31 days before requested start')

    intervals = []
    open_intervals = {ticker: start_date for ticker in active}
    snapshots = [(day, symbols) for day, symbols in parsed if start_date < day < end_date]
    observed_dates = [prior_date]
    for day, symbols in snapshots:
        for ticker in active - symbols:
            intervals.append(dict(ticker=ticker, start=open_intervals.pop(ticker).isoformat(),
                                  end=day.isoformat(), start_observed_at=start,
                                  end_observed_at=day.isoformat(),
                                  boundary_basis='snapshot_inferred'))
        for ticker in symbols - active:
            open_intervals[ticker] = day
        active = symbols
        observed_dates.append(day)

    for ticker, interval_start in open_intervals.items():
        intervals.append(dict(ticker=ticker, start=interval_start.isoformat(), end=end,
                              start_observed_at=(start if interval_start == start_date else interval_start.isoformat()),
                              end_observed_at='', boundary_basis='snapshot_inferred'))

    intervals.sort(key=lambda row: (row['ticker'], row['start']))
    if any(_parse_date(row['start']) >= _parse_date(row['end']) for row in intervals):
        raise ValueError('Generated empty or reversed interval')
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, intervals)

    snapshot_dates = [day for day, _ in parsed if start_date <= day < end_date]
    gaps = [(right - left).days for left, right in zip(snapshot_dates, snapshot_dates[1:])]
    manifest = dict(status='ticker_membership_candidate_not_engine_ready',
                    requested_start=start_date.isoformat(), requested_end_exclusive=end_date.isoformat(),
                    first_source_snapshot=prior_date.isoformat(),
                    last_source_snapshot=snapshot_dates[-1].isoformat() if snapshot_dates else '',
                    source_snapshot_count=len(snapshot_dates),
                    source_max_gap_days=max(gaps, default=0),
                    ticker_count=len({row['ticker'] for row in intervals}), interval_count=len(intervals),
                    source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    source_url=SOURCE_URL,
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    output=str(out),
                    limitations=['Source dates are snapshots; inferred interval boundaries are not independently verified effective dates.',
                                 'Ticker symbols are not stable issuer or security identifiers.',
                                 'Historical sector assignments are not included.',
                                 'Source maintainer reports changes are supplemented from Wikipedia and may be incomplete.',
                                 'Intervals cover only the requested period; prices and settlements for exited or renamed securities remain separate work.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest