import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamps must include UTC offset: ' + value)
    return result


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite number')
    return result


@dataclass(frozen=True)
class Config:
    capital: float = 1_000_000
    hold: int = 60
    cost_bps: float = 10
    delay_minutes: int = 60
    min_purchase: float = 100_000
    min_adv: float = 50_000_000
    min_price: float = 10
    weight: float = .05
    sector_cap: float | None = None
    max_positions: int = 20
    capacity: float = .01


def read_csv(path):
    path = Path(path)
    try:
        with path.open(encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    except UnicodeDecodeError:
        with path.open(encoding='cp1252', newline='') as f:
            return list(csv.DictReader(f))


def write_csv(path, rows):
    if not rows:
        Path(path).write_text('', encoding='utf-8')
        return
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(data, out, start, end, config=Config(), allow_final=False):
    if start > end:
        raise ValueError('Start must precede end')
    if end >= '2023-01-01' and not allow_final:
        raise ValueError('Final-test dates require --allow-final-test after freezing rules')
    if (config.hold < 1 or config.capital <= 0 or config.cost_bps < 0 or
            (config.sector_cap is not None and not 0 < config.sector_cap <= 1)):
        raise ValueError('Invalid configuration')
    data, out = Path(data), Path(out)
    required = ['sessions.csv', 'prices.csv', 'membership.csv', 'signals.csv']
    missing = [name for name in required if not (data / name).exists()]
    if missing:
        raise ValueError('Backtest blocked: missing validated historical inputs: ' + ', '.join(missing))
    sessions = read_csv(data / 'sessions.csv')
    dates = [r['date'] for r in sessions]
    if dates != sorted(set(dates)):
        raise ValueError('Sessions must be unique and sorted')
    for s in sessions:
        s['open_time'] = timestamp(s['open_at'])
        s['close_time'] = timestamp(s['close_at'])
        if s['close_time'] <= s['open_time']:
            raise ValueError('Invalid session times')
        s['cash_return'] = number(s['cash_return'])
        s['sp500_return'] = number(s['sp500_return'])
    indices = [i for i, d in enumerate(dates) if start <= d <= end]
    if not indices or indices[0] < 60:
        raise ValueError('Need requested sessions and at least 60 warmup sessions')
    bars = {}
    for b in read_csv(data / 'prices.csv'):
        key = (b['date'], b['issuer'])
        if key in bars or b['date'] not in dates:
            raise ValueError('Duplicate bar or unknown session: ' + str(key))
        for k in ('open', 'close', 'volume', 'split', 'dividend'):
            b[k] = number(b[k])
        if min(b['open'], b['close'], b['split']) <= 0 or min(b['volume'], b['dividend']) < 0:
            raise ValueError('Invalid prices/action: ' + str(key))
        if b.get('terminal_cash', '') != '':
            b['terminal_cash'] = number(b['terminal_cash'])
            if b['terminal_cash'] < 0:
                raise ValueError('Negative terminal settlement')
        else:
            b['terminal_cash'] = None
        bars[key] = b
    members = read_csv(data / 'membership.csv')

    def eligible(issuer, i):
        sector_rows = [m for m in members if m['issuer'] == issuer and m['start'] <= dates[i] < m['end']]
        if len(sector_rows) > 1:
            raise ValueError('Overlapping membership: ' + issuer)
        if not sector_rows:
            return None
        history = [bars.get((dates[j], issuer)) for j in range(i - 60, i)]
        if any(b is None for b in history):
            return None
        prev = history[-1]['close']
        adv = statistics.mean(b['close'] * b['volume'] for b in history)
        if prev < config.min_price or adv < config.min_adv:
            return None
        return sector_rows[0]['sector'], adv

    signals = defaultdict(list)
    audit = []
    seen = set()
    # Inputs are aggregated per filing/insider/issuer, never across insiders.
    for s in read_csv(data / 'signals.csv'):
        key = (s['accession'], s['insider'], s['issuer'])
        if key in seen:
            raise ValueError('Duplicate signal: ' + str(key))
        seen.add(key)
        accepted = timestamp(s['accepted_at'])
        accepted_date = s['acceptance_date']
        if accepted_date != accepted.astimezone(ZoneInfo('America/New_York')).date().isoformat():
            raise ValueError('Acceptance date inconsistent with timestamp')
        reason = ''
        if s['form'] != '4':
            reason = 'amendment_requires_review'
        elif s['role'] not in ('officer', 'director', 'officer_director'):
            reason = 'role'
        elif s['review'] != 'approved_open_market_common':
            reason = 'purchase_requires_review'
        elif number(s['purchase_value']) < config.min_purchase:
            reason = 'purchase_size'
        target = next((i for i, session in enumerate(sessions)
                       if session['date'] > accepted_date and
                       session['open_time'] >= accepted + timedelta(minutes=config.delay_minutes)), None)
        if not reason and (target is None or target not in indices):
            reason = 'outside_period'
        if reason:
            audit.append(dict(accession=s['accession'], issuer=s['issuer'], date=accepted_date, decision=reason))
        else:
            signals[target].append(s)

    cash = config.capital
    positions = {}
    trades, equity = [], []
    cost = config.cost_bps / 10_000
    benchmark = config.capital
    last_nav = config.capital
    for i in indices:
        day = dates[i]
        cash *= 1 + sessions[i]['cash_return']
        # Splits occur before the open. Dividend cash credited on ex-date to prior holders.
        for issuer, p in positions.items():
            b = bars.get((day, issuer))
            if b is None:
                raise ValueError(f'Missing held price {issuer} {day}; supply explicit terminal action')
            p['shares'] *= b['split']
            dividend = p['shares'] * b['dividend']
            cash += dividend
            p['income'] += dividend

        def sell(issuer, price, reason):
            nonlocal cash
            p = positions.pop(issuer)
            gross = p['shares'] * price
            net = gross * (1 - cost)
            cash += net
            trades.append(dict(issuer=issuer, sector=p['sector'], accession=p['accession'],
                               entry=p['entry'], exit=day, reason=reason, invested=p['invested'],
                               proceeds=net, dividends=p['income'], pnl=net + p['income'] - p['invested'],
                               gross_turnover=p['entry_gross'] + gross))

        for issuer in list(positions):
            if i >= positions[issuer]['exit_index']:
                history = [bars.get((dates[j], issuer)) for j in range(i - 60, i)]
                if any(b is None for b in history):
                    raise ValueError('Cannot verify exit capacity for ' + issuer)
                adv = statistics.mean(b['close'] * b['volume'] for b in history)
                exit_value = positions[issuer]['shares'] * bars[day, issuer]['open']
                if exit_value > config.capacity * adv:
                    raise ValueError('Scheduled exit exceeds 1% ADV; staged execution is required: ' + issuer)
                sell(issuer, bars[day, issuer]['open'], 'scheduled')
        candidates = sorted(signals[i], key=lambda s: (-number(s['purchase_value']), s['issuer'], s['accession'], s['insider']))
        for s in candidates:
            issuer = s['issuer']
            reason = ''
            info = eligible(issuer, i)
            if issuer in positions:
                reason = 'already_held'
            elif i + config.hold > indices[-1]:
                reason = 'boundary'
            elif not info:
                reason = 'universe_filter'
            elif len(positions) >= config.max_positions:
                reason = 'position_limit'
            elif (day, issuer) not in bars:
                raise ValueError('Missing entry price: ' + issuer + ' ' + day)
            else:
                sector, adv = info
                current_nav = cash + sum(p['shares'] * bars[day, k]['open'] for k, p in positions.items())
                # Sector cap uses post-cost NAV; solve purchase + used <= cap * (NAV - fee).
                if config.sector_cap is None:
                    room = math.inf
                else:
                    used = sum(p['shares'] * bars[day, k]['open'] for k, p in positions.items()
                               if p['sector'] == sector)
                    room = max(0, (config.sector_cap * current_nav - used) /
                               (1 + config.sector_cap * cost))
                gross = min(config.weight * current_nav, cash / (1 + cost), config.capacity * adv, room)
                if gross < 1:
                    reason = 'cash_or_sector_limit'
                else:
                    cash -= gross * (1 + cost)
                    positions[issuer] = dict(shares=gross / bars[day, issuer]['open'], sector=sector,
                                             entry=day, exit_index=i + config.hold, invested=gross * (1 + cost),
                                             entry_gross=gross, income=0, accession=s['accession'])
                    reason = 'entered'
            audit.append(dict(accession=s['accession'], issuer=issuer, date=day, decision=reason))
        for issuer in list(positions):
            terminal = bars[day, issuer]['terminal_cash']
            if terminal is not None:
                sell(issuer, terminal, 'terminal_settlement')
        exposure = sum(p['shares'] * bars[day, k]['close'] for k, p in positions.items())
        nav = cash + exposure
        benchmark *= 1 + sessions[i]['sp500_return']
        equity.append(dict(date=day, nav=nav, cash=cash, exposure=exposure / nav, positions=len(positions),
                           daily_return=nav / last_nav - 1, cash_return=sessions[i]['cash_return'], sp500=benchmark))
        last_nav = nav
    if positions:
        raise ValueError('Unexpected open positions at dataset boundary')
    returns = [r['daily_return'] - r['cash_return'] for r in equity]
    vol = statistics.stdev(returns) if len(returns) > 1 else 0
    peak, drawdown = config.capital, 0
    for r in equity:
        peak = max(peak, r['nav'])
        drawdown = min(drawdown, r['nav'] / peak - 1)
    metrics = dict(cagr=(last_nav / config.capital) ** (252 / len(equity)) - 1,
                   sharpe=statistics.mean(returns) / vol * math.sqrt(252) if vol else None,
                   max_drawdown=drawdown, trade_count=len(trades), ending_nav=last_nav,
                   average_exposure=statistics.mean(r['exposure'] for r in equity),
                   annualized_two_way_turnover=sum(t['gross_turnover'] for t in trades) /
                   statistics.mean(r['nav'] for r in equity) * 252 / len(equity),
                   sp500_total_return=benchmark / config.capital - 1)
    yearly = []
    for year in sorted({r['date'][:4] for r in equity}):
        rows = [r for r in equity if r['date'].startswith(year)]
        yearly.append(dict(year=year, net_return=math.prod(1 + r['daily_return'] for r in rows) - 1))
    sectors = []
    for sector in sorted({t['sector'] for t in trades}):
        rows = [t for t in trades if t['sector'] == sector]
        sectors.append(dict(sector=sector, trades=len(rows), pnl=sum(t['pnl'] for t in rows)))
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in [('equity', equity), ('trades', trades), ('decisions', audit), ('yearly', yearly), ('sectors', sectors)]:
        write_csv(out / (name + '.csv'), rows)
    manifest = dict(config=asdict(config), start=start, end=end, metrics=metrics,
                    inputs={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(data.glob('*.csv'))},
                    limitations=['No filtered-universe benchmark or factor/sector adjusted alpha in v1.',
                                 'No confidence intervals; metrics do not establish credible alpha.',
                                 'Amendments excluded pending event-level reconciliation.',
                                 'Oversize scheduled exits stop execution; staged exits are not implemented.',
                                 'Input provider is responsible for point-in-time coverage and corporate actions.'])
    (out / 'report.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest
