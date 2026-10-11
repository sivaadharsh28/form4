"""Reconcile point-in-time membership and gate tickers on complete price/security data."""
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

from .engine import number, read_csv, write_csv
from .security_map import normalize_ticker


def _symbols(value):
    return {item.strip() for item in (value or '').split(',') if item.strip()}


def build_effective_membership(snapshots_path, changes_path, out,
                               start='2021-01-04', end='2026-01-01'):
    snapshots_path, changes_path, out = Path(snapshots_path), Path(changes_path), Path(out)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date >= end_date:
        raise ValueError('Start must precede exclusive end')

    snapshots = []
    with snapshots_path.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            snapshot_date = date.fromisoformat(row['date'])
            if snapshot_date < end_date:
                snapshots.append((snapshot_date, _symbols(row['tickers'])))
    snapshots.sort(key=lambda row: row[0])
    seeds = [(day, members) for day, members in snapshots if day <= start_date]
    if not seeds:
        raise ValueError('No membership snapshot on or before requested start')
    seed_date, seed_members = seeds[-1]

    changes = defaultdict(lambda: {'add': set(), 'remove': set()})
    with changes_path.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            change_date = date.fromisoformat(row['date'])
            if seed_date < change_date < end_date:
                changes[change_date]['add'].update(_symbols(row.get('add', '')))
                changes[change_date]['remove'].update(_symbols(row.get('remove', '')))

    def apply_event(active, day):
        event = changes.get(day, {'add': set(), 'remove': set()})
        additions, removals = event['add'], event['remove']
        if additions & removals:
            raise ValueError('Same ticker both added and removed on ' + day.isoformat())
        if removals - active:
            raise ValueError('Change log removes inactive ticker(s) on ' + day.isoformat() + ': ' +
                             ', '.join(sorted(removals - active)))
        if additions & active:
            raise ValueError('Change log adds already-active ticker(s) on ' + day.isoformat() + ': ' +
                             ', '.join(sorted(additions & active)))
        return (active - removals) | additions

    active = set(seed_members)
    current = seed_date
    mismatches = []
    for snapshot_date, expected in snapshots:
        if snapshot_date < seed_date:
            continue
        for event_date in sorted(day for day in changes if current < day <= snapshot_date):
            active = apply_event(active, event_date)
        current = snapshot_date
        if active != expected:
            mismatches.append(dict(date=snapshot_date.isoformat(), reconstructed=len(active),
                                   snapshot=len(expected), missing=sorted(expected - active),
                                   unexpected=sorted(active - expected)))
    if mismatches:
        raise ValueError('Change-log replay fails snapshot checks: ' + json.dumps(mismatches[:5]))

    active = set(seed_members)
    current = seed_date
    for event_date in sorted(day for day in changes if current < day <= start_date):
        active = apply_event(active, event_date)
    open_starts = {ticker: start_date for ticker in active}
    intervals = []
    event_count = 0
    for event_date in sorted(day for day in changes if start_date < day < end_date):
        event = changes[event_date]
        active = apply_event(active, event_date)
        for ticker in event['remove']:
            intervals.append(dict(ticker=ticker, start=open_starts.pop(ticker).isoformat(),
                                  end=event_date.isoformat(), boundary_basis='dated_change_log'))
        for ticker in event['add']:
            open_starts[ticker] = event_date
        event_count += 1
    for ticker, interval_start in open_starts.items():
        intervals.append(dict(ticker=ticker, start=interval_start.isoformat(), end=end,
                              boundary_basis='dated_change_log'))
    intervals.sort(key=lambda row: (row['ticker'], row['start']))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, intervals)
    manifest = dict(status='dated_change_log_replayed_and_snapshot_reconciled',
                    start=start_date.isoformat(), end_exclusive=end_date.isoformat(),
                    seed_snapshot=seed_date.isoformat(), seed_members=len(seed_members),
                    snapshots_checked=sum(day >= seed_date for day, _ in snapshots),
                    change_dates_applied=event_count, snapshot_mismatches=0,
                    interval_count=len(intervals), unique_tickers=len({row['ticker'] for row in intervals}),
                    snapshot_sha256=hashlib.sha256(snapshots_path.read_bytes()).hexdigest(),
                    changes_sha256=hashlib.sha256(changes_path.read_bytes()).hexdigest(),
                    output=str(out), generated_at=datetime.now(timezone.utc).isoformat(),
                    limitations=['The public source is community-maintained and not the licensed S&P index record.',
                                 'Snapshot replay proves internal consistency, not independent source accuracy.',
                                 'Membership remains ticker-keyed until the CIK crosswalk passes the strict gate.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def build_complete_universe(membership_path, crosswalk_path, prices_path, sessions_path,
                            start='2021-01-04', end='2026-01-01', warmup_sessions=60,
                            membership_out='data/candidates/membership_complete.csv',
                            prices_out='data/candidates/prices_complete.csv',
                            audit_out='data/candidates/complete_universe_audit.csv',
                            top10_out='data/candidates/top10_liquidity_candidate.csv'):
    membership_path, crosswalk_path = Path(membership_path), Path(crosswalk_path)
    prices_path, sessions_path = Path(prices_path), Path(sessions_path)
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    membership_rows = read_csv(membership_path)
    sessions = read_csv(sessions_path)
    session_dates = [row['date'] for row in sessions]
    if session_dates != sorted(set(session_dates)):
        raise ValueError('Sessions must be unique and sorted before completeness audit')
    test_start_index = next((index for index, day in enumerate(session_dates) if day >= start), None)
    end_index = next((index for index, day in enumerate(session_dates) if day >= end), len(session_dates))
    if test_start_index is None or test_start_index < warmup_sessions:
        raise ValueError('Need test sessions and warmup sessions')
    warmup_start = session_dates[test_start_index - warmup_sessions]
    required_test_dates = session_dates[test_start_index:end_index]
    final_test_date = required_test_dates[-1]

    ticker_ciks = defaultdict(set)
    for row in read_csv(crosswalk_path):
        ticker_ciks[row['ticker_key']].add(row['issuer'].zfill(10))
    relevant_intervals = defaultdict(list)
    for row in membership_rows:
        if row['start'] < end and row['end'] > start:
            relevant_intervals[row['ticker']].append((row['start'], row['end']))

    rows_by_ticker = defaultdict(dict)
    source_errors = defaultdict(set)
    with prices_path.open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            ticker = row.get('ticker', '')
            day = row.get('date', '')
            if not ticker or not day:
                continue
            if day in rows_by_ticker[ticker]:
                source_errors[ticker].add('duplicate_price_date')
                continue
            try:
                values = {name: number(row[name]) for name in ('open', 'close', 'volume', 'split', 'dividend')}
                if min(values['open'], values['close'], values['split']) <= 0 or min(values['volume'], values['dividend']) < 0:
                    raise ValueError('invalid_price_or_action')
            except (KeyError, ValueError):
                source_errors[ticker].add('invalid_price_row')
                continue
            rows_by_ticker[ticker][day] = row

    issuer_tickers = defaultdict(set)
    mapped_issuer = {}
    for ticker in relevant_intervals:
        cik_set = ticker_ciks.get(normalize_ticker(ticker), set())
        if len(cik_set) == 1:
            issuer = next(iter(cik_set))
            mapped_issuer[ticker] = issuer
            issuer_tickers[issuer].add(ticker)

    cross_ticker_issuers = {issuer for issuer, tickers in issuer_tickers.items() if len(tickers) > 1}
    candidate_tickers = []
    audit = []
    for ticker, intervals in sorted(relevant_intervals.items()):
        reasons = set(source_errors.get(ticker, set()))
        cik_set = ticker_ciks.get(normalize_ticker(ticker), set())
        issuer = mapped_issuer.get(ticker, '')
        if not cik_set:
            reasons.add('no_observed_cik_mapping')
        elif len(cik_set) > 1:
            reasons.add('ambiguous_ticker_to_cik')
        elif issuer in cross_ticker_issuers:
            reasons.add('issuer_has_multiple_tickers_or_share_classes')

        bars = rows_by_ticker.get(ticker, {})
        available_dates = sorted(bars)
        if not available_dates:
            reasons.add('no_price_history')
        else:
            first_price = available_dates[0]
            if first_price not in session_dates:
                reasons.add('first_price_not_exchange_session')
            else:
                first_index = session_dates.index(first_price)
                check_start_index = max(first_index, test_start_index - warmup_sessions)
                required_price_dates = session_dates[check_start_index:end_index]
                missing_dates = [day for day in required_price_dates if day not in bars]
                if missing_dates:
                    reasons.add('missing_exchange_session_prices')
                if available_dates[-1] < final_test_date:
                    reasons.add('price_history_ends_before_test_end')

        if reasons:
            audit.append(dict(ticker=ticker, issuer=issuer, status='excluded',
                              reasons=';'.join(sorted(reasons)),
                              price_first=available_dates[0] if available_dates else '',
                              price_last=available_dates[-1] if available_dates else '',
                              expected_test_sessions=len(required_test_dates),
                              missing_test_sessions=sum(day not in bars for day in required_test_dates)))
        else:
            candidate_tickers.append(ticker)
            audit.append(dict(ticker=ticker, issuer=issuer, status='complete_candidate', reasons='',
                              price_first=available_dates[0], price_last=available_dates[-1],
                              expected_test_sessions=len(required_test_dates), missing_test_sessions=0))

    accepted = set(candidate_tickers)
    membership_output = []
    for ticker in candidate_tickers:
        for interval_start, interval_end in relevant_intervals[ticker]:
            membership_output.append(dict(issuer=mapped_issuer[ticker], ticker=ticker,
                                          start=max(interval_start, start), end=min(interval_end, end),
                                          sector='unclassified', membership_basis='dated_change_log_reconciled'))
    price_output = []
    for ticker in candidate_tickers:
        issuer = mapped_issuer[ticker]
        for day, row in rows_by_ticker[ticker].items():
            if warmup_start <= day < end:
                price_output.append(dict(date=day, issuer=issuer, open=row['open'], close=row['close'],
                                         volume=row['volume'], split=row['split'], dividend=row['dividend'],
                                         terminal_cash=row.get('terminal_cash', '')))
    price_output.sort(key=lambda row: (row['date'], row['issuer']))
    top10_rows = []
    for session_index in range(test_start_index, end_index):
        day = session_dates[session_index]
        history_dates = session_dates[session_index - warmup_sessions:session_index]
        ranking = []
        for ticker in candidate_tickers:
            active = any(interval_start <= day < interval_end for interval_start, interval_end in relevant_intervals[ticker])
            if not active:
                continue
            bars = rows_by_ticker[ticker]
            if any(history_day not in bars for history_day in history_dates):
                continue
            adv = sum(number(bars[history_day]['close']) * number(bars[history_day]['volume'])
                      for history_day in history_dates) / warmup_sessions
            ranking.append((ticker, mapped_issuer[ticker], adv))
        ranking.sort(key=lambda item: (-item[2], item[0]))
        for rank, (ticker, issuer, adv) in enumerate(ranking[:10], start=1):
            top10_rows.append(dict(date=day, ticker=ticker, issuer=issuer, adv60=adv, rank=rank,
                                   selection='top10_liquidity_complete_data_candidate'))

    membership_out, prices_out, audit_out, top10_out = map(Path,
        (membership_out, prices_out, audit_out, top10_out))
    for path in (membership_out, prices_out, audit_out, top10_out):
        path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(membership_out, membership_output)
    write_csv(prices_out, price_output)
    write_csv(audit_out, audit)
    write_csv(top10_out, top10_rows)
    counts = defaultdict(int)
    for row in audit:
        if row['status'] == 'excluded':
            for reason in row['reasons'].split(';'):
                counts[reason] += 1
    manifest = dict(status='strict_complete_data_candidate_not_backtest_ready',
                    start=start_date.isoformat(), end_exclusive=end_date.isoformat(),
                    warmup_sessions=warmup_sessions, warmup_start=warmup_start,
                    final_test_session=final_test_date, exact_membership_intervals=len(membership_rows),
                    candidate_tickers=len(candidate_tickers), excluded_tickers=len(relevant_intervals)-len(candidate_tickers),
                    exclusion_reason_counts=dict(counts), top10_rows=len(top10_rows),
                    source_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in (membership_path, crosswalk_path, prices_path, sessions_path)},
                    outputs=dict(membership=str(membership_out), prices=str(prices_out),
                                 audit=str(audit_out), top10=str(top10_out)),
                    limitations=['The cohort intentionally excludes issuers with incomplete price histories, including delisted/renamed names; reported strategy results will have explicit survivorship/data-availability selection bias.',
                                 'Ticker-to-CIK is based on observed SEC filings and excludes ambiguous or multi-ticker issuers.',
                                 'Membership change events are reconciled to the same source snapshots, not independently audited against S&P official records.',
                                 'Top-10 selection is liquidity-only; the strategy has no P/E filter.',
                                 'No backtest is produced by this command.'])
    manifest_path = audit_out.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest


def filter_sec_candidates(candidates_path, membership_path, out='data/candidates/sec_complete_candidates.csv',
                          excluded_out='data/candidates/sec_complete_candidates_excluded.csv'):
    candidates_path, membership_path = Path(candidates_path), Path(membership_path)
    candidates = read_csv(candidates_path)
    memberships = read_csv(membership_path)
    intervals = defaultdict(list)
    for row in memberships:
        intervals[normalize_ticker(row['ticker'])].append((row['issuer'], row['start'], row['end']))

    accepted, rejected = [], []
    for candidate in candidates:
        ticker = normalize_ticker(candidate.get('ticker', ''))
        filing_date = datetime.strptime(candidate['filing_date'], '%d-%b-%Y').date().isoformat()
        matching = [(issuer, start, end) for issuer, start, end in intervals.get(ticker, [])
                    if start <= filing_date < end]
        reason = ''
        if not matching:
            reason = 'not_in_complete_membership_on_filing_date'
        elif len(matching) > 1:
            reason = 'overlapping_complete_membership_intervals'
        elif matching[0][0] != candidate['issuer'].zfill(10):
            reason = 'candidate_issuer_cik_mismatch'
        if reason:
            rejected.append(dict(**candidate, complete_universe_decision=reason))
        else:
            accepted.append(dict(**candidate, complete_universe_decision='complete_data_candidate_unapproved'))

    out, excluded_out = Path(out), Path(excluded_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    excluded_out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, accepted)
    write_csv(excluded_out, rejected)
    manifest = dict(status='complete_data_sec_candidates_unapproved',
                    source_candidates=len(candidates), included=len(accepted), excluded=len(rejected),
                    candidate_sha256=hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                    membership_sha256=hashlib.sha256(membership_path.read_bytes()).hexdigest(),
                    output=str(out), excluded_output=str(excluded_out),
                    limitations=['Rows remain unapproved and require original filing review.',
                                 'This filter cannot validate open-market status, footnotes, or amendments.',
                                 'Completeness is conditional on the declared ticker/price/CIK screen and is survivorship-biased by construction.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def filter_top10_sec_candidates(candidates_path, top10_path, sessions_path,
                                out='data/candidates/sec_top10_review_queue.csv',
                                excluded_out='data/candidates/sec_top10_review_excluded.csv'):
    candidates_path, top10_path, sessions_path = map(Path, (candidates_path, top10_path, sessions_path))
    candidates = read_csv(candidates_path)
    sessions = read_csv(sessions_path)
    next_session = {sessions[index]['date']: sessions[index + 1]['date']
                    for index in range(len(sessions) - 1)}
    top10_rows = read_csv(top10_path)
    top10_by_date_ticker = {(row['date'], row['ticker']): row for row in top10_rows}

    included, excluded = [], []
    for candidate in candidates:
        filing_date = datetime.strptime(candidate['filing_date'], '%d-%b-%Y').date().isoformat()
        entry_date = next_session.get(filing_date)
        rank_row = top10_by_date_ticker.get((entry_date, candidate['ticker'])) if entry_date else None
        output = dict(candidate, assumed_entry_date=entry_date or '',
                      top10_rank=rank_row['rank'] if rank_row else '',
                      adv60=rank_row['adv60'] if rank_row else '')
        if rank_row:
            output['review_queue_decision'] = 'review_original_filing'
            included.append(output)
        else:
            output['review_queue_decision'] = 'not_top10_on_next_session'
            excluded.append(output)

    out, excluded_out = Path(out), Path(excluded_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    excluded_out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, included)
    write_csv(excluded_out, excluded)
    manifest = dict(status='top10_next_session_sec_review_queue_unapproved',
                    source_candidate_rows=len(candidates), included_owner_rows=len(included),
                    included_accessions=len({row['accession'] for row in included}),
                    included_tickers=len({row['ticker'] for row in included}),
                    excluded_owner_rows=len(excluded),
                    candidate_sha256=hashlib.sha256(candidates_path.read_bytes()).hexdigest(),
                    top10_sha256=hashlib.sha256(top10_path.read_bytes()).hexdigest(),
                    sessions_sha256=hashlib.sha256(sessions_path.read_bytes()).hexdigest(),
                    output=str(out), excluded_output=str(excluded_out),
                    limitations=['The bulk filing date is used to find the next exchange session; SEC acceptance time will be checked in original filing before approving any signal.',
                                 'Top-10 membership is based on prior 60-session average dollar volume only; no P/E or rating filter is applied.',
                                 'Included filings remain unapproved until original XML, footnotes, transaction status, owner attribution, and amendments are reviewed.',
                                 'The complete-data universe has survivorship/data-availability selection bias.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest