# Form 4 purchase strategy â€” first implementation

Python 3.10+, standard library, plus `tzdata` on Windows for original SEC timestamps.

**Real data only. No demo data or simulated historical results.** Yahoo price downloads run independently of the backtest. `data/readiness.json` records missing research inputs. Downloading prices is not a completed backtest.

## Run the real-data download

```powershell
python -m form4 download
```

Downloads 2009â€“2025 prices from Yahoo Finance for the current S&P 500 constituent download seed, SPY, S&P 500 total-return index (`^SP500TR`), and Treasury bill yield (`^IRX`). Raw responses, source URLs, timestamps, checksums, corporate events, failures, and CSV prices are retained in `data/`. The extra year is for trailing liquidity warmup. End dates for downloads are exclusive. Reruns reuse cached raw responses. Delete a specific cached response to refresh it.

Current constituents are **not** substituted for historical membership. Removed/delisted companies, original symbol mappings, historical sector changes, and price/settlement coverage still need independent reconciliation. Yahoo split-adjusted OHLC cannot be passed directly into the raw-price engine. Adjusted close is appropriate for total-return comparisons, not execution prices. `^IRX` is a yield, not a cash total-return series. Missing data remains missing.

### Prepare a price candidate

```powershell
python -m form4 verify
python -m form4 price-map
python -m form4 normalize-prices
```

`price-map` writes `data/symbol_mapping.csv`. It preselects issuers with one downloaded share class and leaves multiple-class issuers blank; inspect the mapping and choose at most one class per issuer by setting `selected` to `true`. `normalize-prices` checks raw-response hashes and writes `data/candidates/prices.csv` plus a manifest. It reverses later split adjustments for historical OHLC, volume, and dividends, preserving actual historical share units expected by the engine. Missing quotes are omitted, never filled.

This is only a **price candidate**, not a valid backtest input: the symbol map is based on current constituents, is survivorship-biased, and does not resolve historical ticker changes. The output does not provide validated historical sessions, membership, EPS, cash returns, delisting settlements, or reviewed signals. The manifest records missing downloads, unresolved share classes, and source hashes. Do not place this file in `data/normalized` or interpret its presence as backtest readiness until those remaining inputs and coverage have been independently validated.

### Historical S&P 500 ticker membership candidate

```powershell
python -m form4 membership-candidate
```

This caches the public [fja05680/sp500 historical components CSV](https://github.com/fja05680/sp500) under `data/source/` and writes `data/candidates/membership_ticker.csv` plus a manifest. The default window is 2016-01-04 through 2025-12-22 inclusive. The candidate carries each observed ticker set forward until the next source snapshot and uses exclusive interval end dates.

Treat this as a **review candidate**, not as validated point-in-time membership: source snapshots are irregular (the current copy has gaps as long as 91 days), so inferred boundaries may not equal effective index-change dates. The source also warns that its historical change list is assembled from the original dataset and later public change records and may be incomplete. The generated file is ticker-based and lacks stable issuer/security IDs and historical sectors; it is not the engine's `membership.csv` and must not be used directly for backtest claims. Reconcile boundary dates against S&P announcements, map historical tickers to stable securities, add point-in-time sectors, and ensure prices/terminal outcomes exist before converting it to engine input. The source file hash is in the manifest so the exact downloaded snapshot can be identified.

### Strict Complete-Data Universe

```powershell
python -m form4 complete-universe
```

This seeds from the December 21, 2020 snapshot, replays dated additions/removals through 2025, and checks the reconstruction against every later available snapshot. It then keeps only tickers with one observed SEC issuer CIK, no competing ticker/share class for that issuer, and valid price bars on every expected XNYS session from the ticker's first available bar through the end of 2025. The test warmup is 60 sessions.

Outputs are `data/candidates/membership_complete.csv`, `prices_complete.csv`, `complete_universe_audit.csv`, `top10_liquidity_candidate.csv`, and the filtered, still-unapproved SEC candidate list. The current run reconciled 77 dated changes against 78 snapshots with no mismatches; 479 tickers passed and 128 were excluded with reasons. The 12,550 top-10 rows rank complete-data constituents by prior 60-session average dollar volume. The strategy intentionally has **no P/E filter**.

This is a deliberate complete-data survivor cohort, **not** the full historical S&P 500. It excludes names with missing/delisted price histories and ambiguous security mappings, so results will have survivorship and data-availability selection bias. Snapshot replay checks internal consistency, not accuracy against S&P's licensed constituent record. Keep the manifest and report the selected cohort; do not claim full-index performance.

## Original SEC data

```powershell
python -m form4 sec-index --year 2018 --quarter 1 --email YOUR_CONTACT_EMAIL
python -m form4 sec-filing --path PATH_FROM_INDEX --email YOUR_CONTACT_EMAIL
```

The filing command keeps original submission bytes and extracts original acceptance timestamps, Table I P acquisitions, officer/director roles, purchase values, and footnotes. Review rows require classification before entry. Set `review` to `approved_open_market_common` only after verifying the filing. Joint-owner filings require attribution; their value is deliberately blank. Identified private purchases are excluded. Code P by itself does not establish an open-market transaction.

Form 4/A entries are quarantined. Original trades are not retroactively changed, and amendments do not create additional signals. Event-level reconciliation of corrections is unfinished. SEC requests use an identifying contact header, caching, and sequential throttling below five requests per second. Do not run multiple SEC download processes concurrently.

### Bulk SEC research candidates

```powershell
python -m form4 sec-bulk --start-year 2016 --end-year 2025 --email YOUR_CONTACT_EMAIL
python -m form4 sec-enrich --email YOUR_CONTACT_EMAIL
```

`sec-bulk` downloads the SEC's official quarterly Forms 3/4/5 archives and screens non-derivative common/ordinary-stock code-P acquisitions with reported value of at least `$100,000`, an officer/director relationship, and a ticker/date match to the membership candidate. The ten-year archives are cached under `data/sec_bulk/raw/`; `sec_purchase_review_candidates.csv` and its manifest retain checksums and quarter counts. After `complete-universe`, `sec-enrich` defaults to `sec_complete_candidates.csv` and fetches only the narrowed candidates' SEC acceptance headers and ownership XML.

These are **not approved signals**. SEC bulk fields do not include exact acceptance timestamps or all narrative context; the original filing parser deliberately leaves classification at `requires_review`. Manually verify open-market common-stock purchases, footnotes, joint-owner allocation, ticker/security identity, and amendments before setting `review=approved_open_market_common` in any final signals file. Do not copy the candidate CSV directly to `signals.csv`.

For the reduced 28-row review list, see `data/candidates/REVIEW_GUIDE.md`. All 28 have original SEC evidence, acceptance timestamps, parsed purchase/role fields, and source hashes; all remain unapproved. Set an explicit reviewer decision and notes, then run `python -m form4 promote-sec-reviews` to create `signals.csv`. Rows blocked on SEC retrieval remain marked `original_document_fetch_failed` and cannot be promoted.

### Exchange sessions and cash-return candidate

```powershell
python -m form4 session-candidate
```

This writes `data/candidates/sessions.csv` from the XNYS calendar, the cached Yahoo `^SP500TR` series, and FRED's daily DGS3MO yield. The cash series uses the prior available quoted yield multiplied by elapsed calendar days/365. This is an approximate bill-roll proxy, not an observed cash total-return index; its sources and method are recorded in the manifest. Independently validate calendar dates, benchmark levels, and cash accrual before using it as `data/normalized/sessions.csv`.

Sources: [SEC developer resources](https://www.sec.gov/about/developer-resources), [SEC insider transaction guide](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-69), [current constituent download seed](https://en.wikipedia.org/wiki/List_of_S%26P_500_companies).

## Baseline backtester

```powershell
python -m form4 backtest --data data/normalized --start 2010-01-01 --end 2018-12-31
```

Rules: historical S&P 500 membership; prior 60-session average dollar volume â‰¥$50m; previous close â‰¥$10; reviewed officer/director open-market common-stock purchase â‰¥$100k per filing and insider; next session after SEC acceptance date, with â‰¥60 minutes processing delay. Entry-day disclosures never trade the same day. There is no P/E or analyst-rating filter in this version.

Allocate up to 5% of opening NAV, 20 positions, 1% trailing dollar-volume entry capacity, and available cash including fees. During `prepare-backtest`, current constituent sectors are joined to historical membership by ticker and applied retroactively for trade labeling and sector P&L summaries. These are not point-in-time historical classifications, so sector caps remain disabled by default; an optional `--sector-cap` should only be used with validated historical sector intervals. Partial allocations are allowed. Rank by purchase dollars then stable issuer/accession/insider identifiers. Repeat signals neither resize nor reset exits. Exit at the open 60 session intervals after entry (entry open to the 61st session's open). Default cost is 10 bps each side. Capital defaults to $1m. Alternatives `--hold 20/120` and `--cost-bps 25` are explicit experiments.

Splits adjust shares before the open; dividends are credited on ex-date to prior holders (an approximation to payment-date cash availability). Explicit terminal settlement can be zero. A missing held price stops execution. Fixed-time exits include modeled costs and must meet the 1% trailing dollar-volume cap; an oversized exit stops the run pending a staged-execution model. Trading halts are not yet simulated. Sector is fixed at entry for held positions. Fractional shares are allowed. Cash earns the supplied return on cash held overnight. Positions may not cross the selected evaluation boundary; late entries are rejected. Final-test dates require `--allow-final-test`. Keep 2019â€“2022 validation separate and freeze rules before inspecting 2023â€“2025 results.

### Required normalized CSV inputs

Use stable issuer IDs (CIK where appropriate) and one selected common share class per issuer. End dates below are exclusive. Daily prices must be in actual historical share units; EPS must be on the matching historical share basis. Never use today's TTM earnings for older entries.

| File | Columns |
|---|---|
| `sessions.csv` | `date,open_at,close_at,cash_return,sp500_return` |
| `prices.csv` | `date,issuer,open,close,volume,split,dividend,terminal_cash` |
| `membership.csv` | `issuer,start,end,sector` |
| `signals.csv` | `accession,issuer,insider,accepted_at,acceptance_date,form,role,purchase_value,review` |

Times must be ISO 8601 with UTC offsets; acceptance_date is the SEC New York local date. Supply an actual exchange calendar with early closes and at least 60 sessions before the run. Membership intervals must not overlap. For prices, split is the effective ratio (1 when no split), dividend is cash per post-split share, and terminal_cash is blank except an explicit settlement per share on that session. Signals are aggregated once per accession/issuer/insider. Roles: `officer`, `director`, `officer_director`. Review approval is explicit; all other states are skipped.

## Prepare and run the 2021-2025 candidate backtest

```powershell
python -m form4 prepare-backtest --start 2021-01-04 --end 2025-12-31 --hold 60
python -m form4 backtest --data data/normalized --out output/2021-2025 --start 2021-01-04 --end 2025-12-31 --hold 60 --cost-bps 10 --allow-final-test
```

`prepare-backtest` checks schemas, session ordering, duplicate bars, membership overlap, signal approvals and timestamps, signal membership at entry, and price/warmup coverage before assembling the four engine inputs. Its manifest explicitly says structural validation is not independent source validation.

The current run is in `output/2021-2025/`: 2.54% CAGR, -0.33 excess-return Sharpe, -7.07% maximum drawdown, $1,132,885 ending NAV, and 17 closed trades. The full S&P 500 total-return benchmark gained 96.16%; it is not matched to this restricted cohort. The strategy had low average exposure, and repeat signals while already holding a name were skipped. Trades are labeled with current constituent sectors joined by ticker during `prepare-backtest`; these labels are retroactive proxies, not point-in-time historical classifications, and the sector cap remains disabled.

The 479-name complete-price cohort excludes 128 names and is subject to survivorship/data-availability bias. This is not full historical S&P 500 performance and does not establish alpha. The approximate cash proxy, source price/corporate-action accuracy, security mapping, Form 4 amendment reconciliation, matched-universe benchmark, factor attribution, and confidence intervals remain unvalidated. See `RUN_STATUS.md` and `data/readiness.json` for the current study state.

