"""Enrich SEC bulk candidates with exact acceptance metadata from original filings."""
import hashlib
import html
import json
import re
import time
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, urljoin
from html.parser import HTMLParser

from .download import fetch
from .engine import read_csv, write_csv
from .sec import parse_submission


def parse_original_documents(headers, ownership_xml):
    text = html.unescape(headers.decode('utf-8', errors='replace'))
    accepted = re.search(r'<ACCEPTANCE-DATETIME>(\d{14})', text)
    accepted_value = accepted.group(1) if accepted else ''
    accession = re.search(r'<ACCESSION-NUMBER>([\d-]+)', text)
    if not accession:
        accession = re.search(r'ACCESSION NUMBER:\s*([\d-]+)', text)
    if not accepted:
        accepted_page = re.search(r'Accepted\s*</div>\s*<div[^>]*class="info"[^>]*>\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})',
                                  text, re.I)
        if accepted_page:
            accepted_value = ''.join(accepted_page.groups())
    if not accession:
        accession = re.search(r'Accession[\s\S]*?</strong>\s*([\d-]+)', text, re.I)
    if not accepted_value or not accession:
        raise ValueError('SEC index headers lack acceptance time or accession')
    combined = (f'<ACCEPTANCE-DATETIME>{accepted_value}\n'
                f'ACCESSION NUMBER: {accession[1]}\n').encode('ascii') + ownership_xml
    return parse_submission(combined)


def select_ownership_xml(index_payload):
    items = index_payload.get('directory', {}).get('item', [])
    xml_items = [item for item in items if str(item.get('name', '')).lower().endswith('.xml')]
    if not xml_items:
        raise ValueError('SEC accession index contains no XML document')
    preferred = [item for item in xml_items
                 if item.get('name', '').lower() in ('form4.xml', 'edgar.xml')]
    choices = preferred or xml_items
    choices.sort(key=lambda item: (('/' in item.get('name', '')), item.get('name', '').lower()))
    return choices[0]['name']


class _IndexLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            href = dict(attrs).get('href', '')
            if href:
                self.hrefs.append(href)


def xml_names_from_index_html(index_html):
    parser = _IndexLinks()
    parser.feed(index_html.decode('utf-8', errors='replace'))
    names = []
    for href in parser.hrefs:
        href = html.unescape(href).split('?', 1)[0]
        if href.lower().endswith('.xml'):
            names.append(href)
    names = list(dict.fromkeys(names))
    return sorted(names, key=lambda name: (('/xsl' in name.lower()), name.lower()))


def resolve_xml_url(archive_dir, xml_name):
    base = f'https://www.sec.gov/Archives/{archive_dir}/'
    return urljoin(base, quote(xml_name, safe='/:'))


def sec_archive_cik(candidate_issuer, accession):
    issuer = str(candidate_issuer).strip().lstrip('0')
    if not issuer:
        raise ValueError('Candidate has no issuer CIK: ' + accession)
    return issuer


def enrich_candidates(data='data', candidates='data/candidates/sec_purchase_review_candidates.csv',
                      out='data/candidates/sec_original_review.csv', email=''):
    data, candidates, out = Path(data), Path(candidates), Path(out)
    if '@' not in email:
        raise ValueError('Provide a real contact email for the SEC client header')
    if not candidates.exists():
        raise ValueError('Missing screened SEC candidates: ' + str(candidates))
    candidate_rows = read_csv(candidates)
    existing_reviews = {}
    if out.exists():
        for existing in read_csv(out):
            key = (existing.get('accession', ''), existing.get('insider', ''))
            existing_reviews[key] = (existing.get('reviewer_decision', ''),
                                     existing.get('reviewer_notes', ''))
    accessions = sorted({row['accession'] for row in candidate_rows if row.get('accession')})
    issuer_by_accession = {}
    for row in candidate_rows:
        accession = row['accession']
        issuer = row['issuer'].zfill(10)
        if accession in issuer_by_accession and issuer_by_accession[accession] != issuer:
            raise ValueError('Accession has conflicting issuer CIKs: ' + accession)
        issuer_by_accession[accession] = issuer
    if not accessions:
        raise ValueError('No accession numbers in candidate file')

    original_root = data / 'sec_original'
    enriched_by_key = {}
    failures = []
    source_hashes = {}
    for index, accession in enumerate(accessions, start=1):
        parts = accession.split('-')
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            failures.append(dict(accession=accession, error='invalid_accession_format'))
            continue
        issuer_cik = sec_archive_cik(issuer_by_accession.get(accession, ''), accession)
        accession_path = ''.join(parts)
        archive_dir = f'edgar/data/{issuer_cik}/{accession_path}'
        index_path = original_root / (accession + '-index.json')
        index_html_path = original_root / (accession + '-index.html')
        headers_path = original_root / (accession + '-index-headers.html')
        index_url = 'https://www.sec.gov/Archives/' + quote(f'{archive_dir}/index.json', safe='/')
        index_html_url = 'https://www.sec.gov/Archives/' + quote(
            f'{archive_dir}/{accession}-index.html', safe='/')
        headers_url = 'https://www.sec.gov/Archives/' + quote(
            f'{archive_dir}/{accession}-index-headers.html', safe='/')
        try:
            index_html = None
            try:
                index_bytes = fetch(index_url, index_path, 'Form4Research/0.1 ' + email)
                index_payload = json.loads(index_bytes.decode('utf-8'))
                xml_names = [select_ownership_xml(index_payload)]
            except (RuntimeError, json.JSONDecodeError):
                index_bytes = fetch(index_html_url, index_html_path, 'Form4Research/0.1 ' + email)
                index_html = index_bytes
                xml_names = xml_names_from_index_html(index_html)
                if not xml_names:
                    raise ValueError('SEC accession HTML index has no XML document links')
            try:
                headers = fetch(headers_url, headers_path, 'Form4Research/0.1 ' + email)
            except RuntimeError:
                if index_html is None:
                    index_html = fetch(index_html_url, index_html_path, 'Form4Research/0.1 ' + email)
                headers = index_html
                headers_path = index_html_path
            ownership_xml = None
            xml_name = ''
            xml_url = ''
            xml_error = None
            for candidate_name in xml_names:
                candidate_path = original_root / (accession + '-ownership-raw-' + Path(candidate_name).name)
                candidate_url = resolve_xml_url(archive_dir, candidate_name)
                try:
                    candidate_xml = fetch(candidate_url, candidate_path, 'Form4Research/0.1 ' + email)
                    if b'<ownershipDocument' not in candidate_xml:
                        raise RuntimeError('SEC XML URL returned a rendered/non-ownership document')
                    ownership_xml = candidate_xml
                    xml_path, xml_url, xml_name = candidate_path, candidate_url, candidate_name
                    break
                except RuntimeError as exc:
                    xml_error = exc
            if ownership_xml is None:
                raise RuntimeError(str(xml_error or 'SEC ownership XML was not retrievable'))
            index_digest = hashlib.sha256(index_bytes).hexdigest()
            headers_digest = hashlib.sha256(headers).hexdigest()
            xml_digest = hashlib.sha256(ownership_xml).hexdigest()
            source_hashes[accession] = dict(index=index_digest, headers=headers_digest,
                                            ownership_xml=xml_digest, ownership_xml_name=xml_name)
            parsed_rows = parse_original_documents(headers, ownership_xml)
            matches = [row for row in parsed_rows if row['accession'] == accession]
            if not matches:
                failures.append(dict(accession=accession, error='accession_not_found_in_original'))
                continue
            by_owner = {row['insider']: row for row in matches}
            for candidate in (row for row in candidate_rows if row['accession'] == accession):
                original = by_owner.get(candidate['insider'])
                if original is None:
                    failures.append(dict(accession=accession, error='candidate_owner_not_in_original',
                                         insider=candidate['insider']))
                    continue
                if original['issuer'] != candidate['issuer']:
                    failures.append(dict(accession=accession, error='issuer_cik_mismatch',
                                         expected=candidate['issuer'], actual=original['issuer']))
                    continue
                output = dict(candidate)
                prior_decision, prior_notes = existing_reviews.get(
                    (accession, candidate['insider']), ('', ''))
                review_text = (original['footnotes'] + ' ' + original['remarks']).lower()
                if any(term in review_text for term in (
                        'without the reporting person', 'without the reporting person\'s direction',
                        'broker error', 'broker initiated without')):
                    suggestion, suggestion_reason = 'suggest_exclude', 'filing describes broker-initiated or unauthorized activity'
                elif candidate.get('tenb5_plan_indicator') == 'yes':
                    suggestion, suggestion_reason = 'review_plan_discretion', 'SEC structured data flags a Rule 10b5-1 plan'
                else:
                    suggestion, suggestion_reason = 'manual_review_required', 'no automatic classification'
                output.update(accepted_at=original['accepted_at'],
                              acceptance_date=original['acceptance_date'],
                              original_form=original['form'],
                              parsed_purchase_value=original['purchase_value'],
                              original_role=original['role'],
                              original_review=original['review'],
                              security_titles=original['security_titles'],
                              original_footnotes=original['footnotes'],
                              original_remarks=original['remarks'],
                              tenb5_plan_indicator=candidate.get('tenb5_plan_indicator', 'unknown'),
                              review_suggestion=suggestion,
                              suggestion_reason=suggestion_reason,
                              enrichment_status='original_documents_parsed',
                              index_url='https://www.sec.gov/Archives/' + archive_dir + '/index.json',
                              ownership_xml_url=xml_url,
                              original_headers_sha256=headers_digest,
                              original_xml_sha256=xml_digest,
                              reviewer_decision=prior_decision, reviewer_notes=prior_notes,
                              review='requires_manual_open_market_and_amendment_review')
                enriched_by_key[(accession, candidate['insider'])] = output
        except Exception as exc:
            failures.append(dict(accession=accession, error=str(exc)))
        if index % 100 == 0 or index == len(accessions):
            print(f'Original SEC filings processed: {index}/{len(accessions)}; parsed owner rows: {len(enriched_by_key)}',
                  flush=True)
        time.sleep(.25)

    review_queue = []
    failure_by_accession = defaultdict(list)
    for failure in failures:
        failure_by_accession[failure['accession']].append(failure['error'])
    for candidate in candidate_rows:
        key = (candidate['accession'], candidate['insider'])
        if key in enriched_by_key:
            review_queue.append(enriched_by_key[key])
            continue
        parts = candidate['accession'].split('-')
        archive_dir = f'edgar/data/{str(int(parts[0]))}/{"".join(parts)}' if len(parts) == 3 else ''
        fallback = dict(candidate)
        prior_decision, prior_notes = existing_reviews.get(key, ('', ''))
        fallback.update(accepted_at='', acceptance_date='', original_form='', parsed_purchase_value='',
                        original_role='', original_review='', original_footnotes='', original_remarks='',
                        tenb5_plan_indicator=candidate.get('tenb5_plan_indicator', 'unknown'),
                        review_suggestion='manual_review_required',
                        suggestion_reason='original documents could not be retrieved',
                        enrichment_status='original_document_fetch_failed',
                        index_url=('https://www.sec.gov/Archives/' + archive_dir + '/index.json') if archive_dir else '',
                        ownership_xml_url='', original_headers_sha256='', original_xml_sha256='',
                        reviewer_decision=prior_decision, reviewer_notes=prior_notes,
                        review='blocked_until_original_filing_retrieved',
                        enrichment_error='; '.join(failure_by_accession.get(candidate['accession'], [])))
        review_queue.append(fallback)

    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, review_queue)
    manifest = dict(status='original_filings_fetched_pending_manual_review',
                    candidate_rows=len(candidate_rows), unique_accessions=len(accessions),
                    review_queue_rows=len(review_queue), enriched_rows=len(enriched_by_key),
                    unique_filings_downloaded=len(source_hashes),
                    failures=failures, original_sha256=source_hashes,
                    generated_at=datetime.now(timezone.utc).isoformat(), output=str(out),
                    limitations=['Original filing parse extracts purchase rows but does not classify every open-market versus private/plan transaction.',
                                 'All rows remain unapproved pending footnote and amendment review.',
                                 'Bulk ticker matching uses snapshot-inferred membership and is not a stable CIK/security crosswalk.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding='utf-8')
    return manifest