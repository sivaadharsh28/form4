"""Derive an observed ticker-to-CIK crosswalk from SEC quarterly bulk submissions."""
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .engine import read_csv, write_csv


def normalize_ticker(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def build_observed_crosswalk(data='data', membership='data/candidates/membership_ticker.csv',
                             start_year=2016, end_year=2025,
                             out='data/candidates/ticker_cik_observations.csv'):
    data, membership, out = Path(data), Path(membership), Path(out)
    if not membership.exists():
        raise ValueError('Missing historical ticker membership candidate: ' + str(membership))
    member_rows = read_csv(membership)
    member_tickers = {normalize_ticker(row['ticker']) for row in member_rows}
    observations = defaultdict(list)
    archive_hashes = {}
    missing_archives = []
    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            archive_path = data / 'sec_bulk' / 'raw' / f'{year}q{quarter}_form345.zip'
            if not archive_path.exists():
                missing_archives.append(archive_path.name)
                continue
            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            archive_hashes[archive_path.name] = digest
            try:
                with __import__('zipfile').ZipFile(archive_path) as archive:
                    frame = pd.read_csv(archive.open('SUBMISSION.tsv'), sep='\t', dtype=str,
                                        keep_default_na=False,
                                        usecols=['DOCUMENT_TYPE', 'ISSUERCIK', 'ISSUERTRADINGSYMBOL', 'FILING_DATE'])
            except (KeyError, ValueError) as exc:
                raise ValueError('Invalid SEC bulk submission schema: ' + archive_path.name) from exc
            frame = frame[frame['DOCUMENT_TYPE'].isin(('3', '4', '5'))]
            frame['ticker_key'] = frame['ISSUERTRADINGSYMBOL'].map(normalize_ticker)
            frame = frame[frame['ticker_key'].isin(member_tickers) & frame['ISSUERCIK'].ne('')]
            for row in frame.itertuples(index=False):
                try:
                    filed = datetime.strptime(row.FILING_DATE, '%d-%b-%Y').date().isoformat()
                except ValueError:
                    continue
                observations[(row.ticker_key, row.ISSUERTRADINGSYMBOL, row.ISSUERCIK.zfill(10))].append(filed)

    grouped = defaultdict(list)
    for (ticker_key, source_ticker, cik), filed_dates in observations.items():
        grouped[ticker_key].append(dict(ticker=source_ticker, ticker_key=ticker_key, issuer=cik,
                                        first_filing_observed=min(filed_dates),
                                        last_filing_observed=max(filed_dates),
                                        filing_observations=len(filed_dates)))
    result = []
    for ticker_key, rows in sorted(grouped.items()):
        ambiguous = len({row['issuer'] for row in rows}) > 1
        for row in rows:
            row['mapping_status'] = 'ambiguous_ticker_maps_to_multiple_ciks' if ambiguous else 'single_observed_cik_candidate'
            result.append(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, result)
    manifest = dict(status='observed_ticker_cik_crosswalk_candidate',
                    period_start=f'{start_year}-01-01', period_end_exclusive=f'{end_year + 1}-01-01',
                    membership_sha256=hashlib.sha256(membership.read_bytes()).hexdigest(),
                    archives_processed=len(archive_hashes), missing_archives=missing_archives,
                    ticker_count=len({row['ticker_key'] for row in result}),
                    ticker_cik_rows=len(result),
                    ambiguous_tickers=sorted({row['ticker'] for row in result if row['mapping_status'].startswith('ambiguous')}),
                    archive_sha256=archive_hashes, generated_at=datetime.now(timezone.utc).isoformat(),
                    output=str(out),
                    limitations=['Observed filing dates are not exact security-name effective dates.',
                                 'Some members have no Form 3/4/5 filing in the selected period and will not appear.',
                                 'Ticker reuse and multi-class issuers are explicitly ambiguous.',
                                 'Crosswalk does not supply historical sector classifications or delisting settlement data.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest