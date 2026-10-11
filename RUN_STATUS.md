# First execution status

## Completed

- Implemented a baseline research backtester, original Form 4 parser, SEC index/filing download commands, Yahoo downloader, and download integrity checks.
- Executed the real Yahoo download for the requested 2009–2025 range, including a warmup year.
- Retrieved 504 of 506 requested symbols: 501 current-constituent download candidates and three benchmark/yield series.
- Verified 2,007,249 observations and checksums for 505 source files (including the constituent seed).
- Preserved 18 missing numeric cells; no values were fabricated or filled.
- Added `verify`, `price-map`, and `normalize-prices` CLI commands. The cached real responses now produce 1,976,135 split-normalized candidate price rows for 495 symbols, with source hashes and omissions recorded in a manifest.
- Left three issuers with multiple current share classes unresolved instead of choosing a class silently. Added focused tests for split ratios, split-adjusted dividends, and missing observations; all pass.
- Added `membership-candidate` using a free public historical-constituent source. It produces 736 ticker intervals for 728 symbols in the 2016-01-04 to 2025-12-22 window, with a source hash and snapshot-gap warning.
- Retrieved all 40 official SEC quarterly Forms 3/4/5 bulk archives for 2016–2025. The conservative point-in-time ticker/date and $100k officer/director purchase screen produced 3,538 unapproved owner rows; quarter archives and hashes are retained.
- Added sequential original-filing enrichment using SEC acceptance headers and ownership XML. The batch has not yet produced a completed enriched candidate CSV; all rows remain unapproved.
- Added a 4,276-session XNYS candidate using cached `^SP500TR` levels and real FRED DGS3MO observations with an explicitly approximate cash-yield accrual. Source hashes and limitations are in the session manifest.
- Added `complete-universe`: replayed 77 dated constituent changes from the 2020-12-21 seed and matched all 78 later source snapshots with zero mismatches. The complete-data filter retained 479 tickers and excluded 128; it generated 12,550 daily top-10 liquidity rows and reduced SEC candidates from 3,538 to 1,189 (still unapproved).
- Narrowed original filing review to 28 next-session Top-10 candidates. Original filing evidence was retrieved for all 28, including acceptance timestamps and footnotes/remarks. Excel-saved review decisions were decoded and normalized; CIK padding and New York acceptance dates are restored during promotion.
- Added `promote-sec-reviews` safeguards for explicit approvals and source remarks. One MSFT filing stating the broker traded without the insider's knowledge was rejected despite the spreadsheet approval; 27 signals were promoted and the rejection recorded in the audit.
- Added `prepare-backtest` to validate and assemble the real session, complete-cohort price, membership, and signal candidates. Structural checks passed for 4,276 sessions, 621,854 price rows, 479 issuers, and 27 signals.
- Updated `prepare-backtest` to apply current constituent sector labels to historical membership by ticker. The current snapshot labels 429 of 479 membership rows; all 17 trade rows match. The sector source hash and match counts are recorded in `data/normalized/assembly_manifest.json`.
- Executed the 2021-01-04 through 2025-12-31 backtest with a 60-session hold, 10 bps each side, $1m capital, and default disabled sector cap. The engine sizing bug uncovered by the first real run was fixed and the run then completed.
- Sector caps remain disabled because current classifications applied to earlier periods are not point-in-time historical sectors. Sector labels are useful as a current-classification view, but may differ from historical assignments.
- Removed the P/E/EPS filter from the strategy and engine as requested. `earnings.csv` is no longer a required input; the liquidity, minimum-price, membership, and reviewed Form 4 filters remain.
- Added a Streamlit research dashboard at `app.py`; it shows current data assembly state when no validated report exists and real performance metrics only when an engine report plus equity/trade outputs are present.
- Syntax and CLI checks passed. Missing-input rejection was exercised successfully.

## Backtest Result

The completed run is in `output/2021-2025/`. It reports 2.54% CAGR, -0.33 excess-return Sharpe, -7.07% maximum drawdown, $1,132,885 ending NAV, and 17 closed trades. The S&P 500 total-return series gained 96.16% over the same dates. These figures are a mechanical simulation, not evidence of alpha: the benchmark is not matched to the restricted cohort, and no factor-adjusted alpha or confidence intervals are calculated.

The candidate cohort retained 479 names and excluded 128 for missing prices or ambiguous issuer/security mappings. This creates survivorship and data-availability selection bias; results must not be described as full historical S&P 500 performance. Cash accrual is an approximate Treasury-yield proxy, sector labels use current classifications applied retroactively, and corporate-action/security mapping has not been independently audited. SEC amendments remain unreconciled at the transaction-event level.

One of the 28 explicitly approved review rows was rejected at promotion: the MSFT filing states the broker traded without the insider's knowledge and the transaction was reversed. The remaining 27 were promoted; the reviewer audit is retained under `data/normalized/`.

## Remaining Validation

- Independently verify the Yahoo price/corporate-action inputs and point-in-time security mappings.
- Validate the approximate FRED cash accrual and Yahoo S&P 500 total-return benchmark.
- Reconcile Form 4 amendments and review the remaining original filing context.
- Build a matched benchmark for the same restricted cohort and add factor-adjusted analysis and confidence intervals.

Yahoo returned HTTP 400 for FDXF and HONA over this date range. These names were excluded by the complete-data selection, not silently filled or substituted.

## Artifacts

- `data/raw/`: actual source responses and provenance sidecars.
- `data/yahoo/`: real daily observations by symbol.
- `data/current_constituents.csv`: download seed, not historical membership.
- `data/download_status.csv`: per-symbol download coverage/errors.
- `data/readiness.json`: historical-input gaps.
- `data/verification.json`: download integrity results.

No demo data, synthetic prices, invented financial inputs, or fabricated returns were created.
