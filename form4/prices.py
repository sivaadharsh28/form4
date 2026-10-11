"""Convert split-adjusted Yahoo observations into a reviewable price candidate."""
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .download import yahoo
from .engine import number, read_csv, write_csv


def _event_date(timestamp):
    return datetime.fromtimestamp(int(timestamp), timezone.utc).date().isoformat()


def normalized_rows(chart, issuer):
    timestamps = chart.get('timestamp') or []
    quote = chart['indicators']['quote'][0]
    event_data = chart.get('events') or {}
    splits = defaultdict(lambda: 1.0)
    dividends = defaultdict(float)

    for timestamp, event in (event_data.get('splits') or {}).items():
        numerator = number(event['numerator'])
        denominator = number(event['denominator'])
        if numerator <= 0 or denominator <= 0:
            raise ValueError('Invalid split ratio')
        splits[_event_date(timestamp)] *= numerator / denominator

    for timestamp, event in (event_data.get('dividends') or {}).items():
        amount = number(event['amount'])
        if amount < 0:
            raise ValueError('Negative dividend')
        dividends[_event_date(timestamp)] += amount

    dates = [_event_date(value) for value in timestamps]
    if dates != sorted(set(dates)):
        raise ValueError('Yahoo observations must be unique and chronological')
    date_set = set(dates)
    if (set(splits) | set(dividends)) - date_set:
        raise ValueError('Corporate action date has no matching price observation')

    future_split_factor = {}
    factor = 1.0
    for day in reversed(dates):
        future_split_factor[day] = factor
        factor *= splits.get(day, 1.0)

    rows = []
    missing = 0
    for index, day in enumerate(dates):
        open_price = quote['open'][index]
        close_price = quote['close'][index]
        volume = quote['volume'][index]
        if open_price is None or close_price is None or volume is None:
            missing += 1
            continue
        reverse_factor = future_split_factor[day]
        values = [number(open_price) * reverse_factor,
                  number(close_price) * reverse_factor,
                  number(volume) / reverse_factor,
                  dividends.get(day, 0.0) * reverse_factor]
        if not all(math.isfinite(value) for value in values):
            raise ValueError('Non-finite normalized observation: ' + day)
        if min(values[0], values[1]) <= 0 or min(values[2], values[3]) < 0:
            raise ValueError('Invalid normalized observation: ' + day)
        rows.append(dict(date=day, issuer=issuer, open=values[0], close=values[1],
                         volume=values[2], split=splits[day],
                         dividend=values[3], terminal_cash=''))
    return rows, missing


def seed_symbol_mapping(data='data', out='data/symbol_mapping.csv'):
    constituents = read_csv(Path(data) / 'current_constituents.csv')
    issuer_counts = Counter(row['issuer'] for row in constituents)
    rows = [dict(symbol=row.get('yahoo_symbol') or row['symbol'], issuer=row['issuer'],
                 selected='true' if issuer_counts[row['issuer']] == 1 else '')
            for row in constituents]
    write_csv(out, rows)
    return dict(rows=len(rows), ambiguous_issuers=sum(count > 1 for count in issuer_counts.values()),
                output=str(out), status='mapping_requires_review')


def _selected_mapping(path):
    source_rows = read_csv(path)
    mapping = {}
    issuer_counts = Counter()
    ambiguous = []
    for row in source_rows:
        symbol = row.get('symbol') or row.get('yahoo_symbol')
        issuer = row.get('issuer')
        selected = (row.get('selected') or '').strip().lower()
        if not symbol or not issuer or selected not in ('true', 'false', '1', '0', ''):
            raise ValueError('Mapping requires symbol, issuer, and selected columns')
        if symbol in mapping:
            raise ValueError('Duplicate mapping symbol: ' + symbol)
        mapping[symbol] = (issuer, selected in ('true', '1'))
        issuer_counts[issuer] += 1

    chosen = {}
    for symbol, (issuer, selected) in mapping.items():
        if selected:
            if issuer in chosen:
                raise ValueError('Select only one share class for issuer: ' + issuer)
            chosen[issuer] = symbol
    for issuer in issuer_counts:
        if issuer not in chosen:
            ambiguous.append(issuer)
    unresolved = sorted(issuer for issuer in issuer_counts if issuer not in chosen)
    return {symbol: issuer for issuer, symbol in chosen.items()}, unresolved


def normalize_prices(data='data', mapping='data/symbol_mapping.csv', out='data/candidates/prices.csv'):
    data, mapping, out = Path(data), Path(mapping), Path(out)
    selected, unresolved_issuers = _selected_mapping(mapping)
    all_rows, missing_quote_rows, source_hashes, missing_symbols = [], 0, {}, []

    for symbol, issuer in sorted(selected.items()):
        raw_name = symbol.replace('^', 'INDEX_')
        matches = [path for path in (data / 'raw').glob(raw_name + '_*.json')
                   if not path.name.endswith('.source.json')]
        if not matches:
            missing_symbols.append(symbol)
            continue
        if len(matches) != 1:
            raise ValueError('Expected one cached response for ' + symbol)
        source = matches[0]
        metadata_path = source.with_suffix(source.suffix + '.source.json')
        if not metadata_path.exists():
            raise ValueError('Missing provenance sidecar: ' + str(metadata_path))
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != metadata.get('sha256'):
            raise ValueError('Raw response checksum mismatch: ' + str(source))
        payload = json.loads(source.read_text(encoding='utf-8'))
        result = payload.get('chart', {}).get('result')
        if payload.get('chart', {}).get('error') or not result:
            missing_symbols.append(symbol)
            continue
        rows, missing = normalized_rows(result[0], issuer)
        all_rows.extend(rows)
        missing_quote_rows += missing
        source_hashes[source.name] = digest

    if not all_rows:
        raise ValueError('No price observations normalized; check mapping and raw downloads')
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, all_rows)
    manifest = dict(status='candidate_only_not_backtest_ready', symbols_normalized=len(source_hashes),
                    rows=len(all_rows), missing_quote_rows=missing_quote_rows,
                    symbols_without_usable_source=missing_symbols,
                    unresolved_share_class_issuers=unresolved_issuers,
                    mapping_sha256=hashlib.sha256(mapping.read_bytes()).hexdigest(),
                    source_sha256=source_hashes,
                    limitations=['Mapping is seeded from current constituents and is survivorship-biased.',
                                 'One static symbol-to-issuer mapping cannot represent historical ticker changes.',
                                 'Historical membership, sessions, cash returns, and reviewed signals remain separate inputs.',
                                 'Missing quotes are omitted; held-security coverage must be validated before a backtest.'])
    manifest_path = out.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest


def build_historical_ticker_prices(data='data', membership='data/candidates/membership_ticker.csv',
                                   start='2015-01-01', end='2026-01-01',
                                   out='data/candidates/historical_prices_ticker.csv'):
    data, membership, out = Path(data), Path(membership), Path(out)
    if not membership.exists():
        raise ValueError('Missing historical ticker membership candidate: ' + str(membership))
    tickers = sorted({row['ticker'] for row in read_csv(membership)})
    yahoo_symbols = {ticker: ticker.replace('.', '-') for ticker in tickers}

    def has_raw(symbol):
        raw_name = symbol.replace('^', 'INDEX_')
        return (data / 'raw' / f'{raw_name}_{start}_{end}.json').exists()

    missing_symbols = [symbol for symbol in sorted(set(yahoo_symbols.values())) if not has_raw(symbol)]
    missing_downloads = []

    def download_one(symbol):
        try:
            result = yahoo(symbol, data, start, end)
        except Exception as exc:
            result = dict(symbol=symbol, rows=0, first='', last='', missing_bars=0, status=str(exc))
        time.sleep(.5)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        for status in pool.map(download_one, missing_symbols):
            if not status['rows']:
                missing_downloads.append(status)
            print(f"Historical price {status['symbol']}: {status['rows']} observations", flush=True)

    rows, source_hashes, coverage = [], {}, []
    for ticker, symbol in yahoo_symbols.items():
        source = data / 'raw' / f'{symbol.replace("^", "INDEX_")}_{start}_{end}.json'
        if not source.exists():
            coverage.append(dict(ticker=ticker, yahoo_symbol=symbol, rows=0, first='', last='', status='missing_source'))
            continue
        sidecar = source.with_suffix(source.suffix + '.source.json')
        if not sidecar.exists():
            raise ValueError('Missing Yahoo source sidecar: ' + str(sidecar))
        metadata = json.loads(sidecar.read_text(encoding='utf-8'))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if digest != metadata.get('sha256'):
            raise ValueError('Raw Yahoo response checksum mismatch: ' + str(source))
        payload = json.loads(source.read_text(encoding='utf-8'))
        result = payload.get('chart', {}).get('result')
        if payload.get('chart', {}).get('error') or not result:
            coverage.append(dict(ticker=ticker, yahoo_symbol=symbol, rows=0, first='', last='', status='provider_error'))
            continue
        normalized, missing = normalized_rows(result[0], ticker)
        for row in normalized:
            row['ticker'] = row.pop('issuer')
        rows.extend(normalized)
        source_hashes[source.name] = digest
        coverage.append(dict(ticker=ticker, yahoo_symbol=symbol, rows=len(normalized),
                             first=normalized[0]['date'] if normalized else '',
                             last=normalized[-1]['date'] if normalized else '',
                             missing_rows=missing, status='downloaded'))

    if not rows:
        raise ValueError('No historical ticker price rows normalized')
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, rows)
    coverage_path = out.with_name('historical_price_coverage.csv')
    write_csv(coverage_path, coverage)
    manifest = dict(status='historical_ticker_prices_candidate_not_security_mapped',
                    period_start=start, period_end_exclusive=end,
                    membership_ticker_count=len(tickers), yahoo_symbols_requested=len(set(yahoo_symbols.values())),
                    symbols_with_price_source=len(source_hashes), observations=len(rows),
                    missing_price_downloads=missing_downloads, source_sha256=source_hashes,
                    membership_sha256=hashlib.sha256(membership.read_bytes()).hexdigest(),
                    output=str(out), coverage_output=str(coverage_path),
                    limitations=['Yahoo coverage and corporate actions require independent reconciliation, especially for delisted and renamed securities.',
                                 'Rows are keyed by ticker, not stable issuer/security ID; ticker reuse and share classes remain unresolved.',
                                 'No merger/delisting terminal settlements are inferred.',
                                 'This file is not engine prices.csv until a historical security crosswalk is reviewed.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest