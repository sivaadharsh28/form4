"""Cache SEC indexes and parse original ownership submissions conservatively."""
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .download import fetch
from .engine import number, write_csv


def index(root, year, quarter, email):
    if '@' not in email:
        raise ValueError('Provide a real contact email for the SEC client header')
    path = Path(root) / 'sec' / f'{year}-QTR{quarter}-master.idx'
    url = f'https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx'
    body = fetch(url, path, 'Form4Research/0.1 ' + email).decode('latin-1')
    rows = []
    for line in body.splitlines():
        parts = line.split('|')
        if len(parts) == 5 and parts[0].isdigit() and parts[2] in ('4', '4/A'):
            rows.append(dict(index_cik=parts[0], name=parts[1], form=parts[2],
                             date=parts[3], path=parts[4]))
    write_csv(path.with_suffix('.csv'), rows)
    return rows


def parse_submission(body):
    """Do not infer open-market status merely from transaction code P."""
    text = body.decode('utf-8', errors='replace')
    accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', text)
    accession = re.search(r'ACCESSION NUMBER:\s*([\d-]+)', text)
    xml = re.search(r'(<ownershipDocument\b.*?</ownershipDocument>)', text, re.S)
    if not accepted or not accession or not xml:
        raise ValueError('Submission lacks original acceptance header, accession, or ownership XML')
    instant = datetime.strptime(accepted[1], '%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('America/New_York'))
    root = ET.fromstring(xml[1])
    for node in root.iter():
        node.tag = node.tag.split('}')[-1]
    get = lambda node, path: (node.findtext(path) or '').strip()
    issuer = get(root, 'issuer/issuerCik').zfill(10)
    owners = root.findall('reportingOwner')
    notes = ' '.join(''.join(n.itertext()) for n in root.findall('footnotes/footnote'))
    remarks = get(root, 'remarks')
    all_notes = (notes + ' ' + remarks).lower()
    amounts = []
    titles = []
    for t in root.findall('nonDerivativeTable/nonDerivativeTransaction'):
        if get(t, 'transactionCoding/transactionCode') != 'P':
            continue
        if get(t, 'transactionAmounts/transactionAcquiredDisposedCode/value') != 'A':
            continue
        title = get(t, 'securityTitle/value')
        if not re.search(r'common|ordinary', title, re.I):
            continue
        try:
            shares = number(get(t, 'transactionAmounts/transactionShares/value'))
            price = number(get(t, 'transactionAmounts/transactionPricePerShare/value'))
        except ValueError:
            continue
        if shares > 0 and price > 0:
            amounts.append(shares * price)
            titles.append(title)
    if not amounts:
        return []
    review = 'requires_review'
    if any(term in all_notes for term in ('private placement', 'privately negotiated', 'private purchase', 'subscription agreement')):
        review = 'excluded_private'
    if len(owners) != 1:
        review = 'requires_joint_owner_allocation'
    rows = []
    for owner in owners:
        officer = get(owner, 'reportingOwnerRelationship/isOfficer').lower() in ('1', 'true')
        director = get(owner, 'reportingOwnerRelationship/isDirector').lower() in ('1', 'true')
        role = 'officer_director' if officer and director else 'officer' if officer else 'director' if director else 'other'
        rows.append(dict(accession=accession[1], issuer=issuer,
                         insider=get(owner, 'reportingOwnerId/rptOwnerCik').zfill(10),
                         accepted_at=instant.isoformat(), acceptance_date=instant.date().isoformat(),
                         form=get(root, 'documentType'), role=role,
                         purchase_value=sum(amounts) if len(owners) == 1 else '', review=review,
                         officer_title=get(owner, 'reportingOwnerRelationship/officerTitle'),
                         security_titles='; '.join(sorted(set(titles))), footnotes=notes, remarks=remarks))
    return rows


def filing(root, path, email):
    if '@' not in email or not re.fullmatch(r'edgar/data/\d+/[\d-]+\.txt', path):
        raise ValueError('Expected SEC index filing path and contact email')
    url = 'https://www.sec.gov/Archives/' + path
    body = fetch(url, Path(root) / 'sec' / Path(path).name, 'Form4Research/0.1 ' + email)
    time.sleep(.25)  # sequential process, fewer than five requests per second
    rows = parse_submission(body)
    write_csv(Path(root) / 'sec' / (Path(path).stem + '-review.csv'), rows)
    return rows
