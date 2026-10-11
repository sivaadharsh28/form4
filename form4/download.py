"""Real Yahoo downloads. Keep raw responses; never fill missing observations."""
import hashlib
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from .engine import write_csv


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.rows, self.row, self.cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.rows = []
        elif tag == 'tr' and self.rows is not None:
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = ''

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == 'table' and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None


def fetch(url, path, user_agent='Mozilla/5.0 Form4Research/0.1'):
    path = Path(path)
    if path.exists():
        return path.read_bytes()
    error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': user_agent})
            with urllib.request.urlopen(req, timeout=40) as response:
                body = response.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            path.with_suffix(path.suffix + '.source.json').write_text(json.dumps({
                'url': url, 'retrieved_at': datetime.now(timezone.utc).isoformat(),
                'sha256': hashlib.sha256(body).hexdigest()}, indent=2), encoding='utf-8')
            return body
        except Exception as exc:
            error = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f'{url}: {error}')


def universe(root):
    url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
    body = fetch(url, root / 'raw' / 'constituents.html')
    parser = Tables()
    parser.feed(body.decode('utf-8'))
    table = next(t for t in parser.tables if t and 'Symbol' in t[0] and 'CIK' in t[0])
    header = table[0]
    rows = []
    for r in table[1:]:
        if len(r) != len(header):
            continue
        entry = dict(zip(header, r))
        rows.append(dict(symbol=entry['Symbol'], yahoo_symbol=entry['Symbol'].replace('.', '-'),
                         issuer=entry['CIK'].zfill(10), name=entry['Security'],
                         current_sector=entry['GICS Sector'], added=entry['Date added']))
    if len(rows) < 450:
        raise ValueError('Unexpected constituents table size')
    write_csv(root / 'current_constituents.csv', rows)
    return rows


def yahoo(symbol, root, start, end):
    first = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    last = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    query = urllib.parse.urlencode(dict(period1=first, period2=last, interval='1d', events='div,splits'))
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + urllib.parse.quote(symbol) + '?' + query
    name = symbol.replace('^', 'INDEX_')
    path = root / 'raw' / f'{name}_{start}_{end}.json'
    body = json.loads(fetch(url, path))
    if body['chart'].get('error') or not body['chart'].get('result'):
        raise ValueError(str(body))
    result = body['chart']['result'][0]
    q = result['indicators']['quote'][0]
    adj = result['indicators'].get('adjclose', [{}])[0].get('adjclose', [])
    rows = []
    for i, ts in enumerate(result.get('timestamp', [])):
        rows.append(dict(date=datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
                         symbol=symbol, open=q['open'][i], high=q['high'][i], low=q['low'][i],
                         close=q['close'][i], volume=q['volume'][i],
                         adjusted_close=adj[i] if i < len(adj) else None))
    if not rows:
        raise ValueError('No prices: ' + symbol)
    (root / 'yahoo').mkdir(exist_ok=True)
    write_csv(root / 'yahoo' / (name + '.csv'), rows)
    return dict(symbol=symbol, rows=len(rows), first=rows[0]['date'], last=rows[-1]['date'],
                missing_bars=sum(r['close'] is None for r in rows), status='downloaded')


def download(root, start='2009-01-01', end='2026-01-01', symbols=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if symbols is None:
        constituents = universe(root)
        symbols = [r['yahoo_symbol'] for r in constituents]
    symbols = sorted(set(symbols) | {'SPY', '^SP500TR', '^IRX'})
    statuses = []

    def one(symbol):
        try:
            result = yahoo(symbol, root, start, end)
        except Exception as exc:
            result = dict(symbol=symbol, rows=0, first='', last='', missing_bars=0, status=str(exc))
        time.sleep(.5)
        return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(one, symbols):
            statuses.append(result)
            print(f"[{len(statuses)}/{len(symbols)}] {result['symbol']}: {result['rows']} rows", flush=True)
    write_csv(root / 'download_status.csv', statuses)
    report = dict(price_symbols_downloaded=sum(r['rows'] > 0 for r in statuses),
                  requested=len(symbols), start=start, end_exclusive=end,
                  status='BLOCKED_FOR_CREDIBLE_BACKTEST',
                  missing=['Validated historical S&P 500 membership and sector intervals, including removed companies',
                           'Point-in-time trailing EPS with original publication timestamps and consistent split basis',
                           'Original Form 4 filings with acceptance times and reviewed open-market purchase classification',
                           'Historical symbol/security mapping and delisting/acquisition settlement outcomes',
                           'Validated session calendar and historical cash total-return series'],
                  notes=['Current constituent list is a download seed only, not historical membership.',
                         'Yahoo OHLC and volume use a split-adjusted basis; do not apply raw split events twice.',
                         'SPY is an ETF proxy; SP500TR is separately requested as the total-return index.',
                         'IRX is a yield quote, not a daily cash return.',
                         'No performance was computed and no synthetic data was created.'])
    (root / 'readiness.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
