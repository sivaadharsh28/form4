import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(page_title='Form 4 Strategy Research', page_icon='F4', layout='wide')

st.markdown('''
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Sans:wght@400;500;600;700&display=swap');
:root {
  --ink: #17251f;
  --muted: #68766f;
  --paper: #f2f5f1;
  --line: #dce4de;
  --green: #176b4d;
  --mint: #d9eee3;
  --coral: #d7674a;
  --gold: #c18b31;
}
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; color: var(--ink); }
.stApp { background: var(--paper); }
[data-testid="stHeader"] { background: rgba(242,245,241,.88); }
[data-testid="stSidebar"] { background: #e8eee9; border-right: 1px solid var(--line); }
h1, h2, h3, p, label, li { color: var(--ink) !important; letter-spacing: 0; }
h1 { font-size: 2.1rem; font-weight: 700; }
div[data-testid="stMetric"] { background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 14px 16px; }
div[data-testid="stMetricLabel"] { color: var(--muted); }
div[data-testid="stMetricValue"] { color: var(--ink); font-family: 'DM Mono', monospace; }
.eyebrow { color: var(--green); font: 500 12px 'DM Mono', monospace; text-transform: uppercase; letter-spacing: .08em; }
.lede { color: var(--muted); font-size: 15px; margin-top: -10px; }
.status-line { border-left: 4px solid var(--gold); background: #fff; color: var(--ink) !important; padding: 14px 18px; border-radius: 4px; }
.footnote { color: var(--muted); font-size: 12px; }
</style>
''', unsafe_allow_html=True)


def load_csv(path):
    return pd.read_csv(path, parse_dates=['date'] if path.name == 'equity.csv' else None)


def money(value):
    return f'${value:,.0f}' if value is not None else 'N/A'


def percent(value):
    return f'{value:.1%}' if value is not None else 'N/A'


def blocked_view(results_dir):
    st.markdown('<div class="eyebrow">Systematic insider research · 2016–2025 candidate window</div>', unsafe_allow_html=True)
    st.title('Form 4 Strategy Monitor')
    st.markdown('<p class="lede">Performance reporting is gated on a validated, point-in-time dataset.</p>', unsafe_allow_html=True)
    st.markdown('<div class="status-line"><strong>Backtest not run.</strong> No report.json was found in the selected results directory, so no performance values are displayed.</div>', unsafe_allow_html=True)

    readiness_path = Path('data/readiness.json')
    readiness = {}
    if readiness_path.exists():
        readiness = json.loads(readiness_path.read_text(encoding='utf-8'))
    st.subheader('Research readiness')
    st.caption(f'Results path: {results_dir.resolve()}')
    if readiness:
        st.write(f"Current input status: **{readiness.get('status', 'unknown')}**")
        for item in readiness.get('missing', []):
            st.markdown(f'- {item}')
    st.info('The complete-data gate narrows the universe to names with reconciled dated membership, an unambiguous observed CIK, and continuous daily prices. This selection creates survivorship/data-availability bias. The P/E filter has been removed; approved original-filing signals and remaining input validation are still required before any backtest.')
    st.subheader('Data assembly')
    bulk_archives = list(Path('data/sec_bulk/raw').glob('*_form345.zip'))
    candidate_path = Path('data/candidates/sec_purchase_review_candidates.csv')
    complete_sec_path = Path('data/candidates/sec_complete_candidates.csv')
    candidate_count = 0
    if candidate_path.exists():
        try:
            candidate_count = max(0, sum(1 for _ in candidate_path.open(encoding='utf-8')) - 1)
        except OSError:
            candidate_count = 0
    enriched_path = Path('data/candidates/sec_original_review.csv')
    enriched_count = 0
    original_parsed_count = 0
    blocked_review_count = 0
    reviewer_decisions = 0
    plan_flag_count = 0
    plan_unknown_count = 0
    suggested_exclusions = 0
    promoted_signal_count = 0
    signals_path = Path('data/normalized/signals.csv')
    if signals_path.exists() and signals_path.stat().st_size:
        try:
            promoted_signal_count = len(pd.read_csv(signals_path))
        except pd.errors.EmptyDataError:
            promoted_signal_count = 0
    if enriched_path.exists():
        try:
            review_rows = pd.read_csv(enriched_path, dtype=str, keep_default_na=False)
            enriched_count = len(review_rows)
            if 'enrichment_status' in review_rows:
                original_parsed_count = int((review_rows['enrichment_status'] == 'original_documents_parsed').sum())
                blocked_review_count = enriched_count - original_parsed_count
            if 'reviewer_decision' in review_rows:
                reviewer_decisions = int(review_rows['reviewer_decision'].str.strip().ne('').sum())
            if 'tenb5_plan_indicator' in review_rows:
                plan_flag_count = int((review_rows['tenb5_plan_indicator'] == 'yes').sum())
                plan_unknown_count = int((review_rows['tenb5_plan_indicator'] == 'unknown').sum())
            if 'review_suggestion' in review_rows:
                suggested_exclusions = int((review_rows['review_suggestion'] == 'suggest_exclude').sum())
        except OSError:
            enriched_count = 0
    gate_manifest_path = Path('data/candidates/complete_universe_audit.manifest.json')
    gate_manifest = json.loads(gate_manifest_path.read_text(encoding='utf-8')) if gate_manifest_path.exists() else {}
    sec_gate_manifest_path = complete_sec_path.with_suffix('.manifest.json')
    sec_gate_manifest = json.loads(sec_gate_manifest_path.read_text(encoding='utf-8')) if sec_gate_manifest_path.exists() else {}
    assembly = [
        {'dataset': 'Historical membership',
         'state': 'Dated changes replayed; snapshots reconciled' if gate_manifest.get('status') else 'Membership candidate not reconciled',
         'artifact': 'data/candidates/membership_effective_ticker.csv'},
        {'dataset': 'Complete price + issuer cohort',
         'state': f"{gate_manifest.get('candidate_tickers', 0):,} eligible; {gate_manifest.get('excluded_tickers', 0):,} excluded" if gate_manifest else 'Completeness gate not run',
         'artifact': 'data/candidates/complete_universe_audit.csv'},
        {'dataset': 'Daily top-10 liquidity list',
         'state': f"{gate_manifest.get('top10_rows', 0):,} date/ticker selections; liquidity-ranked, no P/E filter" if gate_manifest else 'Not built',
         'artifact': 'data/candidates/top10_liquidity_candidate.csv'},
        {'dataset': 'Sessions, benchmark, cash proxy',
         'state': 'Candidate built; Treasury accrual is approximate' if Path('data/candidates/sessions.csv').exists() else 'Not built',
         'artifact': 'data/candidates/sessions.csv'},
        {'dataset': 'SEC bulk quarters', 'state': f'{len(bulk_archives)} of 40 archives cached' if len(bulk_archives) < 40 else '40 of 40 archives cached',
         'artifact': 'data/sec_bulk/raw/'},
        {'dataset': 'Purchase candidates',
         'state': f"{sec_gate_manifest.get('included', 0):,} complete-cohort candidates; all unapproved" if sec_gate_manifest else ('Awaiting complete-cohort filter' if not candidate_path.exists() else f'{candidate_count:,} unapproved bulk candidates'),
         'artifact': str(complete_sec_path)},
        {'dataset': 'Original filing review',
         'state': f'{original_parsed_count:,} of {enriched_count:,} fetched; {blocked_review_count:,} blocked; 10b5-1 yes {plan_flag_count:,}/unknown {plan_unknown_count:,}; exclusion hints {suggested_exclusions:,}; decisions {reviewer_decisions:,}' if enriched_path.exists() else 'Queued / not complete',
         'artifact': str(enriched_path)},
        {'dataset': 'Approved signals',
         'state': f'{promoted_signal_count:,} promoted' if signals_path.exists() else 'None; review decisions still needed',
         'artifact': str(signals_path)},
        {'dataset': 'Normalized session/prices/membership inputs', 'state': 'Candidate artifacts exist; final bundle not assembled', 'artifact': 'data/normalized/'},
    ]
    st.dataframe(pd.DataFrame(assembly), use_container_width=True, hide_index=True)
    st.caption('For each fetched SEC row, inspect its footnotes/remarks and linked original documents. Set reviewer_decision to approve_open_market_common or exclude, and record the evidence in reviewer_notes. Then run `python -m form4 promote-sec-reviews`.')
    with st.expander('Backtest files required before results can appear', expanded=True):
        st.code('data/normalized/sessions.csv\ndata/normalized/prices.csv\ndata/normalized/membership.csv\ndata/normalized/signals.csv\n' + str(results_dir / 'report.json'))


def show_results(results_dir, report):
    metrics = report.get('metrics', {})
    equity_path = results_dir / 'equity.csv'
    trades_path = results_dir / 'trades.csv'
    if not equity_path.exists() or not trades_path.exists():
        st.error('The report is incomplete: equity.csv and trades.csv are required for performance validation.')
        return
    equity = load_csv(equity_path)
    trades = load_csv(trades_path)
    if equity.empty or not {'date', 'nav', 'sp500', 'daily_return'}.issubset(equity.columns):
        st.error('equity.csv is empty or does not match the report schema.')
        return

    st.markdown('<div class="eyebrow">Form 4 Strategy Research · Backtest report</div>', unsafe_allow_html=True)
    st.title('Strategy Performance')
    st.markdown(f"<p class='lede'>{report.get('start', 'Unknown start')} to {report.get('end', 'Unknown end')} · capital {money(report.get('config', {}).get('capital'))} · hold {report.get('config', {}).get('hold', 'N/A')} sessions</p>", unsafe_allow_html=True)
    st.warning('Historical simulation only. This run uses a 479-name complete-price cohort and excludes 128 names, creating survivorship and data-availability bias. The S&P 500 comparison is not matched to that restricted cohort. These results do not establish alpha or future investability.')
    gate_manifest_path = Path('data/candidates/complete_universe_audit.manifest.json')
    if gate_manifest_path.exists():
        gate_manifest = json.loads(gate_manifest_path.read_text(encoding='utf-8'))
        st.caption(f"Complete-data cohort: {gate_manifest.get('candidate_tickers', 'N/A'):,} included, {gate_manifest.get('excluded_tickers', 'N/A'):,} excluded. {int(metrics.get('trade_count', 0))} closed trades from 27 promoted signals; repeat signals while already held were skipped.")

    first = st.columns(4)
    first[0].metric('Strategy CAGR', percent(metrics.get('cagr')))
    first[1].metric('S&P 500 total return', percent(metrics.get('sp500_total_return')))
    first[2].metric('Max drawdown', percent(metrics.get('max_drawdown')))
    first[3].metric('Excess-return Sharpe', f"{metrics['sharpe']:.2f}" if metrics.get('sharpe') is not None else 'N/A')
    second = st.columns(4)
    second[0].metric('Ending NAV', money(metrics.get('ending_nav')))
    second[1].metric('Closed trades', f"{int(metrics.get('trade_count', len(trades))):,}")
    second[2].metric('Average exposure', percent(metrics.get('average_exposure')))
    second[3].metric('Annualized turnover', f"{metrics['annualized_two_way_turnover']:.2f}x" if metrics.get('annualized_two_way_turnover') is not None else 'N/A')

    nav_base = float(equity['nav'].iloc[0])
    benchmark_base = float(equity['sp500'].iloc[0])
    equity['strategy_index'] = equity['nav'] / nav_base * 100
    equity['benchmark_index'] = equity['sp500'] / benchmark_base * 100
    equity['peak'] = equity['nav'].cummax()
    equity['drawdown'] = equity['nav'] / equity['peak'] - 1

    performance_tab, trade_tab, audit_tab = st.tabs(['Performance', 'Trades', 'Audit & inputs'])
    with performance_tab:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=equity['date'], y=equity['strategy_index'], name='Strategy',
                                 line=dict(color='#176b4d', width=2.5), hovertemplate='%{x|%Y-%m-%d}<br>Index %{y:.1f}<extra>Strategy</extra>'))
        fig.add_trace(go.Scatter(x=equity['date'], y=equity['benchmark_index'], name='S&P 500',
                                 line=dict(color='#d7674a', width=1.8), hovertemplate='%{x|%Y-%m-%d}<br>Index %{y:.1f}<extra>S&P 500</extra>'))
        fig.update_layout(height=390, margin=dict(l=0, r=10, t=20, b=0), paper_bgcolor='rgba(0,0,0,0)',
                          plot_bgcolor='white', yaxis_title='Growth of 100', xaxis_title='',
                          legend=dict(orientation='h', y=1.08), hovermode='x unified')
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(gridcolor='#e7ece8', zeroline=False)
        st.plotly_chart(fig, use_container_width=True)

        draw = go.Figure(go.Scatter(x=equity['date'], y=equity['drawdown'] * 100, name='Drawdown',
                                    line=dict(color='#d7674a', width=1.5), fill='tozeroy',
                                    fillcolor='rgba(215,103,74,.14)', hovertemplate='%{x|%Y-%m-%d}<br>%{y:.1f}%<extra></extra>'))
        draw.update_layout(height=230, margin=dict(l=0, r=10, t=20, b=0), paper_bgcolor='rgba(0,0,0,0)',
                           plot_bgcolor='white', yaxis_title='Drawdown', xaxis_title='')
        draw.update_xaxes(showgrid=False)
        draw.update_yaxes(gridcolor='#e7ece8', ticksuffix='%')
        st.plotly_chart(draw, use_container_width=True)

        yearly_path = results_dir / 'yearly.csv'
        if yearly_path.exists():
            yearly = load_csv(yearly_path)
            if not yearly.empty and {'year', 'net_return'}.issubset(yearly.columns):
                st.subheader('Calendar-year returns')
                colors = ['#176b4d' if value >= 0 else '#d7674a' for value in yearly['net_return']]
                annual = go.Figure(go.Bar(x=yearly['year'], y=yearly['net_return'] * 100,
                                          marker_color=colors, hovertemplate='%{x}<br>%{y:.1f}%<extra></extra>'))
                annual.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor='rgba(0,0,0,0)',
                                     plot_bgcolor='white', yaxis_title='Return', xaxis_title='')
                annual.update_yaxes(gridcolor='#e7ece8', ticksuffix='%')
                st.plotly_chart(annual, use_container_width=True)

    with trade_tab:
        if trades.empty:
            st.info('The report contains no closed trades.')
        elif 'pnl' in trades:
            pnl = pd.to_numeric(trades['pnl'], errors='coerce').dropna()
            winners, losers = pnl[pnl > 0], pnl[pnl < 0]
            profit_factor = winners.sum() / abs(losers.sum()) if len(losers) and losers.sum() else None
            stats = st.columns(4)
            stats[0].metric('Win rate', percent((pnl > 0).mean()) if len(pnl) else 'N/A')
            stats[1].metric('Average trade P&L', money(pnl.mean()) if len(pnl) else 'N/A')
            stats[2].metric('Profit factor', f'{profit_factor:.2f}' if profit_factor is not None else 'N/A')
            stats[3].metric('Median trade P&L', money(pnl.median()) if len(pnl) else 'N/A')
            if 'sector' in trades:
                st.caption('Sector labels use the current constituent classification applied retroactively by ticker; they may differ from each issuer’s historical sector.')
                sectors = sorted(trades['sector'].dropna().unique().tolist())
                selected = st.selectbox('Filter sector', ['All sectors', *sectors])
                visible = trades if selected == 'All sectors' else trades[trades['sector'] == selected]
            else:
                visible = trades
            st.dataframe(visible, use_container_width=True, hide_index=True)
            sector_path = results_dir / 'sectors.csv'
            if sector_path.exists():
                sector_data = load_csv(sector_path)
                if not sector_data.empty and {'sector', 'pnl'}.issubset(sector_data.columns):
                    sector_data = sector_data.sort_values('pnl')
                    colors = ['#d7674a' if value < 0 else '#176b4d' for value in sector_data['pnl']]
                    sector_fig = go.Figure(go.Bar(x=sector_data['pnl'], y=sector_data['sector'], orientation='h', marker_color=colors))
                    sector_fig.update_layout(height=max(240, 32 * len(sector_data)), margin=dict(l=0, r=10, t=10, b=0),
                                             paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='white', xaxis_title='Realized P&L', yaxis_title='')
                    st.subheader('Realized P&L by entry sector')
                    st.plotly_chart(sector_fig, use_container_width=True)

    with audit_tab:
        st.subheader('Declared limitations')
        for limitation in report.get('limitations', []):
            st.markdown(f'- {limitation}')
        st.subheader('Input fingerprints')
        inputs = report.get('inputs', {})
        if inputs:
            st.dataframe(pd.DataFrame([{'file': name, 'sha256': digest} for name, digest in inputs.items()]),
                         use_container_width=True, hide_index=True)
        decisions_path = results_dir / 'decisions.csv'
        if decisions_path.exists():
            decisions = load_csv(decisions_path)
            if not decisions.empty and 'decision' in decisions:
                st.subheader('Signal decision counts')
                counts = decisions['decision'].value_counts().rename_axis('decision').reset_index(name='count')
                st.dataframe(counts, use_container_width=True, hide_index=True)
        st.caption('The current engine does not calculate factor-adjusted alpha or confidence intervals.')


st.sidebar.markdown('<div class="eyebrow">Research workspace</div>', unsafe_allow_html=True)
st.sidebar.title('Run artifacts')
results_text = st.sidebar.text_input('Results directory', value='output/2021-2025')
results_dir = Path(results_text).expanduser()
report_path = results_dir / 'report.json'
if not report_path.exists():
    blocked_view(results_dir)
else:
    try:
        report_data = json.loads(report_path.read_text(encoding='utf-8'))
        if not report_data.get('metrics') or not report_data.get('inputs'):
            st.error('The report is missing metrics or input fingerprints; refusing to display it as a complete run.')
        else:
            show_results(results_dir, report_data)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        st.error(f'Could not load the selected report: {error}')