"""Promote explicitly reviewed SEC filing candidates to engine signals."""
import hashlib
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .engine import number, read_csv, write_csv


APPROVE = 'approve_open_market_common'
EXCLUDE = 'exclude'
ALLOWED_DECISIONS = {APPROVE, EXCLUDE, ''}


def promote_reviewed_candidates(review_path='data/candidates/sec_original_review.csv',
                                out='data/normalized/signals.csv',
                                rejected_out='data/normalized/signals_review_rejected.csv'):
    review_path, out, rejected_out = map(Path, (review_path, out, rejected_out))
    if not review_path.exists():
        raise ValueError('Missing original filing review queue: ' + str(review_path))
    rows = read_csv(review_path)
    signals, audit = [], []
    for row in rows:
        decision = (row.get('reviewer_decision') or '').replace('\xa0', ' ').strip().lower()
        notes = (row.get('reviewer_notes') or '').strip()
        accession = row.get('accession', '')
        if decision not in ALLOWED_DECISIONS:
            audit.append(dict(accession=accession, insider=row.get('insider', ''),
                              status='invalid_reviewer_decision', decision=decision))
            continue
        if decision == EXCLUDE:
            audit.append(dict(accession=accession, insider=row.get('insider', ''),
                              status='reviewer_excluded', decision=decision,
                              reasons='' if notes else 'reviewer_notes_not_supplied'))
            continue
        if decision != APPROVE:
            audit.append(dict(accession=accession, insider=row.get('insider', ''),
                              status='awaiting_review', decision=''))
            continue

        failures = []
        if row.get('enrichment_status') != 'original_documents_parsed':
            failures.append('original_documents_not_retrieved')
        if row.get('review_queue_decision') != 'review_original_filing':
            failures.append('not_in_top10_next_session_review_queue')
        if row.get('original_form') != '4':
            failures.append('original_form_not_4')
        if row.get('original_review') != 'requires_review':
            failures.append('original_parser_did_not_mark_reviewable')
        original_context = ' '.join((row.get('original_footnotes') or '',
                                     row.get('original_remarks') or '')).lower()
        unauthorized_terms = ('without the reporting person\'s direction',
                              'without the reporting person\'s approval',
                              'without the reporting person\'s knowledge',
                              'broker initiated without the reporting person')
        if any(term in original_context for term in unauthorized_terms):
            failures.append('filing_says_transaction_was_not_authorized_by_insider')
        if row.get('original_role') not in ('officer', 'director', 'officer_director'):
            failures.append('original_role_not_eligible')
        if 'common' not in (row.get('security_titles') or '').lower() and 'ordinary' not in (row.get('security_titles') or '').lower():
            failures.append('common_or_ordinary_security_not_verified')
        try:
            purchase_value = number(row.get('parsed_purchase_value', ''))
            if purchase_value < 100_000:
                failures.append('original_purchase_below_minimum')
        except (TypeError, ValueError):
            purchase_value = 0
            failures.append('original_purchase_value_missing')
        if not row.get('accepted_at'):
            failures.append('acceptance_timestamp_missing')
        if failures:
            audit.append(dict(accession=accession, insider=row.get('insider', ''),
                              status='approval_rejected_by_validation', decision=decision,
                              reasons=';'.join(failures)))
            continue
        accepted_at = datetime.fromisoformat(row['accepted_at'].replace('Z', '+00:00'))
        acceptance_date = accepted_at.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        signals.append(dict(accession=accession, issuer=row['issuer'].zfill(10),
                             insider=row['insider'].zfill(10), accepted_at=row['accepted_at'],
                             acceptance_date=acceptance_date,
                             form=row['original_form'], role=row['original_role'],
                             purchase_value=purchase_value, review='approved_open_market_common'))
        audit.append(dict(accession=accession, insider=row.get('insider', ''),
                          status='approved_for_backtest', decision=decision))

    out.parent.mkdir(parents=True, exist_ok=True)
    rejected_out.parent.mkdir(parents=True, exist_ok=True)
    signal_fields = ['accession', 'issuer', 'insider', 'accepted_at', 'acceptance_date',
                     'form', 'role', 'purchase_value', 'review']
    with out.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=signal_fields)
        writer.writeheader()
        writer.writerows(signals)
    audit_rows = [dict(accession=row.get('accession', ''), insider=row.get('insider', ''),
                       status=row.get('status', ''), decision=row.get('decision', ''),
                       reasons=row.get('reasons', '')) for row in audit]
    write_csv(rejected_out, audit_rows)
    manifest = dict(status='signals_promoted_from_explicit_manual_review',
                    review_rows=len(rows), approved_signals=len(signals),
                    awaiting_review=sum(row['status'] == 'awaiting_review' for row in audit),
                    excluded=sum(row['status'] == 'reviewer_excluded' for row in audit),
                    rejected_approvals=sum(row['status'] == 'approval_rejected_by_validation' for row in audit),
                    review_sha256=hashlib.sha256(review_path.read_bytes()).hexdigest(),
                    output=str(out), audit_output=str(rejected_out),
                    generated_at=datetime.now(timezone.utc).isoformat(),
                    limitations=['Promotion validates fields and an explicit reviewer decision but cannot independently establish that the human review was correct.',
                                 'Use only after checking open-market status, footnotes, amendments, owner attribution, and the complete filing context.'])
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest