"""Download and conservatively screen the SEC's quarterly Forms 3/4/5 datasets."""
import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import time

import pandas as pd

from .download import fetch
from .engine import read_csv, write_csv


BASE_URL = 'https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets'
TABLES = ('SUBMISSION.tsv', 'NONDERIV_TRANS.tsv', 'REPORTINGOWNER.tsv')


def _read_tables(archive_bytes):
    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        names = set(archive.namelist())
        missing = set(TABLES) - names
        if missing:
            raise ValueError('SEC archive missing tables: ' + ', '.join(sorted(missing)))
        return {name.removesuffix('.tsv'): pd.read_csv(archive.open(name), sep='\t', dtype=str,
                                                      keep_default_na=False)
                for name in TABLES}


def screen_tables(tables, issuer_ciks=None, minimum_purchase=100_000):
    submissions = tables['SUBMISSION'].copy()
    transactions = tables['NONDERIV_TRANS'].copy()
    owners = tables['REPORTINGOWNER'].copy()
    required = {
        'SUBMISSION': {'ACCESSION_NUMBER', 'DOCUMENT_TYPE', 'ISSUERCIK', 'ISSUERTRADINGSYMBOL', 'FILING_DATE'},
        'NONDERIV_TRANS': {'ACCESSION_NUMBER', 'SECURITY_TITLE', 'TRANS_FORM_TYPE', 'TRANS_CODE',
                           'TRANS_SHARES', 'TRANS_PRICEPERSHARE', 'TRANS_ACQUIRED_DISP_CD'},
        'REPORTINGOWNER': {'ACCESSION_NUMBER', 'RPTOWNERCIK', 'RPTOWNER_RELATIONSHIP', 'RPTOWNER_TITLE'},
    }
    for name, columns in required.items():
        if not columns.issubset(tables[name].columns):
            raise ValueError('SEC ' + name + ' schema is missing required columns')

    submissions['ISSUERCIK'] = submissions['ISSUERCIK'].str.zfill(10)
    if 'AFF10B5ONE' not in submissions.columns:
        submissions['AFF10B5ONE'] = ''
    submissions['AFF10B5ONE'] = submissions['AFF10B5ONE'].fillna('').str.strip()
    submission_filter = submissions['DOCUMENT_TYPE'] == '4'
    if issuer_ciks is not None:
        normalized_ciks = {str(cik).zfill(10) for cik in issuer_ciks}
        submission_filter &= submissions['ISSUERCIK'].isin(normalized_ciks)
    submissions = submissions[submission_filter]
    transactions = transactions[(transactions['TRANS_FORM_TYPE'] == '4') &
                                (transactions['TRANS_CODE'] == 'P') &
                                (transactions['TRANS_ACQUIRED_DISP_CD'] == 'A') &
                                transactions['SECURITY_TITLE'].str.contains(r'common|ordinary', case=False, regex=True)]
    if submissions.empty or transactions.empty:
        return [], dict(issuer_filings=len(submissions), qualifying_purchase_transactions=0,
                        owner_purchase_rows=0, accessions=0)

    transactions['shares_value'] = pd.to_numeric(transactions['TRANS_SHARES'], errors='coerce')
    transactions['price_value'] = pd.to_numeric(transactions['TRANS_PRICEPERSHARE'], errors='coerce')
    transactions['notional'] = transactions['shares_value'] * transactions['price_value']
    transactions['unpriced'] = (transactions['notional'].isna() |
                                 (transactions['shares_value'] <= 0) |
                                 (transactions['price_value'] <= 0))
    transactions = transactions.merge(
        submissions[['ACCESSION_NUMBER', 'ISSUERCIK', 'ISSUERTRADINGSYMBOL', 'FILING_DATE', 'AFF10B5ONE']],
        on='ACCESSION_NUMBER', how='inner', validate='many_to_one')
    owners = owners[owners['ACCESSION_NUMBER'].isin(submissions['ACCESSION_NUMBER'])].copy()
    owners['RPTOWNER_RELATIONSHIP'] = owners['RPTOWNER_RELATIONSHIP'].fillna('')
    grouped_transactions = {}
    for accession, rows in transactions.groupby('ACCESSION_NUMBER', sort=False):
        has_unpriced = bool(rows['unpriced'].any())
        value = None if has_unpriced else float(rows['notional'].sum())
        if value is None or value < minimum_purchase:
            continue
        grouped_transactions[accession] = (value, has_unpriced,
                                             '; '.join(sorted(set(rows['SECURITY_TITLE']))))

    owner_rows = []
    stats = dict(issuer_filings=len(submissions),
                 qualifying_purchase_transactions=len(transactions), owner_purchase_rows=0,
                 accessions=len(grouped_transactions))
    for accession, transactions_info in grouped_transactions.items():
        value, _, security_titles = transactions_info
        accession_owners = owners[owners['ACCESSION_NUMBER'] == accession]
        qualifying_owners = accession_owners[accession_owners['RPTOWNER_RELATIONSHIP'].str.contains(
            r'Officer|Director', case=False, regex=True)]
        if qualifying_owners.empty:
            continue
        multiple_owners = len(accession_owners) != 1
        submission = submissions[submissions['ACCESSION_NUMBER'] == accession].iloc[0]
        plan_flag = str(submission.get('AFF10B5ONE', '')).strip().lower()
        plan_indicator = 'yes' if plan_flag in ('1', 'true', 'yes') else 'no' if plan_flag in ('0', 'false', 'no') else 'unknown'
        for _, owner in qualifying_owners.iterrows():
            relationship = owner['RPTOWNER_RELATIONSHIP']
            is_officer = bool(re.search(r'Officer', relationship, re.I))
            is_director = bool(re.search(r'Director', relationship, re.I))
            role = 'officer_director' if is_officer and is_director else 'officer' if is_officer else 'director'
            owner_rows.append(dict(accession=accession, issuer=submission['ISSUERCIK'],
                                   ticker=submission['ISSUERTRADINGSYMBOL'],
                                   insider=owner['RPTOWNERCIK'].zfill(10),
                                   filing_date=submission['FILING_DATE'], form='4', role=role,
                                   officer_title=owner['RPTOWNER_TITLE'],
                                   purchase_value='' if multiple_owners else value,
                                   security_titles=security_titles,
                                   tenb5_plan_indicator=plan_indicator,
                                   review='requires_joint_owner_allocation' if multiple_owners else
                                   'requires_original_filing_review', accepted_at='',
                                   source='SEC Insider Transactions Data Sets'))
    stats['owner_purchase_rows'] = len(owner_rows)
    return owner_rows, stats


def _normalize_ticker(value):
    return re.sub(r'[^A-Z0-9]', '', str(value).upper())


def _membership_by_ticker(path):
    intervals = {}
    for row in read_csv(path):
        intervals.setdefault(_normalize_ticker(row['ticker']), []).append(
            (datetime.strptime(row['start'], '%Y-%m-%d').date(),
             datetime.strptime(row['end'], '%Y-%m-%d').date()))
    return intervals


def match_membership(candidates, membership_intervals):
    matched = []
    for candidate in candidates:
        filing_date = datetime.strptime(candidate['filing_date'], '%d-%b-%Y').date()
        ticker_key = _normalize_ticker(candidate['ticker'])
        intervals = membership_intervals.get(ticker_key, [])
        if any(start <= filing_date < end for start, end in intervals):
            item = dict(candidate)
            item['membership_match'] = 'ticker_date_candidate'
            matched.append(item)
    return matched


def download_and_screen(data='data', out='data/candidates/sec_purchase_review_candidates.csv',
                        start_year=2016, end_year=2025, minimum_purchase=100_000, email='',
                        membership='data/candidates/membership_ticker.csv'):
    data, out = Path(data), Path(out)
    if start_year < 2006 or end_year < start_year:
        raise ValueError('SEC bulk data begins in 2006 and the year range must be ordered')
    if '@' not in email:
        raise ValueError('Provide a real contact email for the SEC client header')
    membership_path = Path(membership)
    if not membership_path.exists():
        raise ValueError('Missing point-in-time ticker membership candidate; run membership-candidate first: ' +
                         str(membership_path))
    membership_intervals = _membership_by_ticker(membership_path)

    all_candidates, quarter_stats, source_hashes = [], [], {}
    for year in range(start_year, end_year + 1):
        for quarter in range(1, 5):
            name = f'{year}q{quarter}_form345.zip'
            url = f'{BASE_URL}/{name}'
            archive_path = data / 'sec_bulk' / 'raw' / name
            archive_bytes = fetch(url, archive_path, 'Form4Research/0.1 ' + email)
            source_hashes[name] = hashlib.sha256(archive_bytes).hexdigest()
            tables = _read_tables(archive_bytes)
            candidates, counts = screen_tables(tables, None, minimum_purchase)
            member_candidates = match_membership(candidates, membership_intervals)
            all_candidates.extend(member_candidates)
            counts['point_in_time_ticker_matches'] = len(member_candidates)
            quarter_stats.append(dict(year=year, quarter=quarter, **counts))
            print(f'{year} Q{quarter}: {len(member_candidates)} point-in-time ticker review candidates', flush=True)
            time.sleep(.25)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, all_candidates)
    manifest = dict(status='screened_candidates_require_original_filing_review',
                    period_start=f'{start_year}-01-01', period_end_exclusive=f'{end_year + 1}-01-01',
                    membership_candidate=str(membership_path), survivorship_bias=False,
                    minimum_purchase=minimum_purchase, candidate_rows=len(all_candidates),
                    quarters=quarter_stats, source_sha256=source_hashes,
                    generated_at=datetime.now(timezone.utc).isoformat(), output=str(out),
                    limitations=['Membership match is by ticker and filing date against snapshot-inferred intervals, not a stable issuer-ID join.',
                                 'Bulk tables contain filing date but not the exact SEC acceptance timestamp needed for trading delay.',
                                 'Structured fields omit narrative context and may have extraction errors; original filings must be checked.',
                                 'Amendments and corrections require event-level reconciliation.',
                                 'All rows remain unapproved; the candidate file is not signals.csv.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest