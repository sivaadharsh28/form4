# Implemented Backtest Strategy

## Strategy

The current run tests reviewed Form 4 open-market common-stock purchases by officers and directors. A signal must be explicitly approved, be an original Form 4 (amendments are excluded), and represent at least $100,000 in reported purchases for that insider and issuer. The issuer must be in the reconstructed historical S&P 500 membership and pass the price and liquidity filters. There is no P/E or analyst-rating filter.

Entry is at the open of the first later exchange session whose open is at least 60 minutes after SEC acceptance. This prevents same-day use of an unprocessed filing. Eligible stocks need a prior close of at least $10 and prior 60-session average dollar volume of at least $50 million.

## Portfolio Rules and Assumptions

The 2021-2025 run starts with $1,000,000. It targets at most 5% of current NAV per position, holds at most 20 issuers, and limits entry size to 1% of trailing average dollar volume and available cash. Positions exit at the open 60 session intervals after entry. Repeated signals do not resize an existing position or restart its holding period. The configured sector cap is disabled.

Execution is modeled at session opens, with 10 basis points of cost on each side and fractional shares. Splits adjust shares before the open; dividends are credited on the ex-date. Overnight cash earns the supplied approximate Treasury-yield return. Missing held prices stop the run; scheduled exits exceeding the 1% ADV limit also stop because staged execution is not implemented. Positions cannot cross the evaluation boundary.

The historical membership reconstruction does not contain point-in-time sector assignments. For the displayed trades and sector P&L grouping, the current `current_sector` values in `data/current_constituents.csv` are applied retroactively by ticker. This makes the sector breakdown usable as a rough current-classification view, but labels may differ from what was known or assigned historically. The sector cap remains disabled.

## Recorded Results

The recorded run covers 2021-01-04 through 2025-12-31 and closed 17 trades. Starting from $1,000,000, it ended at $1,132,885, with a 2.54% CAGR, -0.33 excess-return Sharpe, -7.07% maximum drawdown, 4.12% average exposure, and 0.34x annualized two-way turnover. Calendar-year returns were +0.55% (2021), -2.91% (2022), +5.34% (2023), +3.86% (2024), and +6.06% (2025).

The S&P 500 total-return series rose 96.16% over the same dates, but it is not a matched benchmark for this restricted cohort. The universe retained 479 names and excluded 128 for incomplete price coverage or ambiguous mappings. These figures are mechanical candidate results, not evidence of alpha: structural input checks are not independent source validation, and the universe has survivorship and data-availability bias.

## Next Steps

1. Replace the retroactive current-sector proxy with validated point-in-time historical sector intervals and compare how the sector breakdown changes.
2. Independently audit historical membership, security mappings, prices, corporate actions, and cash-return inputs.
3. Reconcile Form 4 amendments and filing-level transaction context.
4. Build a matched benchmark for the same eligible cohort, then add factor attribution and uncertainty estimates.
5. Model staged exits and trading halts before treating capacity-constrained results as executable.

The current detailed artifacts are in `output/2021-2025/`; the methodology and readiness caveats are also described in `README.md` and `RUN_STATUS.md`.