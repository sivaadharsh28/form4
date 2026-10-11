import csv
import tempfile
import unittest
from pathlib import Path

from form4.sec_review import promote_reviewed_candidates


def row(decision='', notes='', status='original_documents_parsed', value='125000'):
    return dict(accession='0000000001-25-000001', issuer='0000000002', insider='0000000003',
                accepted_at='2025-01-02T16:30:00-05:00', acceptance_date='2025-01-02',
                original_form='4', original_role='director', parsed_purchase_value=value,
                security_titles='Common Stock', enrichment_status=status,
                review_queue_decision='review_original_filing', original_review='requires_review',
                reviewer_decision=decision, reviewer_notes=notes)


class SecReviewPromotionTests(unittest.TestCase):
    def test_unreviewed_candidate_never_becomes_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'review.csv'
            with source.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row()))
                writer.writeheader()
                writer.writerow(row())

            result = promote_reviewed_candidates(source, root / 'signals.csv', root / 'audit.csv')

            self.assertEqual(result['approved_signals'], 0)
            self.assertEqual(result['awaiting_review'], 1)

    def test_explicit_approval_requires_notes_and_valid_source_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'review.csv'
            with source.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row()))
                writer.writeheader()
                writer.writerow(row('approve_open_market_common', 'Verified open-market purchase'))
                writer.writerow(row('approve_open_market_common', '', value='99000'))
                writer.writerow(row('approve_open_market_common', 'Verified', status='original_document_fetch_failed'))

            result = promote_reviewed_candidates(source, root / 'signals.csv', root / 'audit.csv')

            self.assertEqual(result['approved_signals'], 1)
            self.assertEqual(result['rejected_approvals'], 2)
            with (root / 'signals.csv').open(newline='', encoding='utf-8') as handle:
                signals = list(csv.DictReader(handle))
            self.assertEqual(signals[0]['review'], 'approved_open_market_common')

    def test_invalid_decision_is_audited_not_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'review.csv'
            with source.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(row()))
                writer.writeheader()
                writer.writerow(row('looks_good', ''))

            result = promote_reviewed_candidates(source, root / 'signals.csv', root / 'audit.csv')

            self.assertEqual(result['approved_signals'], 0)
            self.assertEqual(result['rejected_approvals'], 0)
            with (root / 'audit.csv').open(newline='', encoding='utf-8') as handle:
                audit = list(csv.DictReader(handle))
            self.assertEqual(audit[0]['status'], 'invalid_reviewer_decision')

    def test_rejects_filing_that_says_purchase_was_without_insider_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'review.csv'
            candidate = row('approve_open_market_common', 'Reviewed filing')
            candidate['original_remarks'] = 'Broker purchased shares without the reporting person\'s knowledge.'
            candidate['issuer'] = '1234'
            candidate['insider'] = '5678'
            candidate['acceptance_date'] = '1/2/2025'
            with source.open('w', newline='', encoding='utf-8') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(candidate))
                writer.writeheader()
                writer.writerow(candidate)

            result = promote_reviewed_candidates(source, root / 'signals.csv', root / 'audit.csv')

            self.assertEqual(result['approved_signals'], 0)
            with (root / 'audit.csv').open(newline='', encoding='utf-8') as handle:
                audit = list(csv.DictReader(handle))
            self.assertIn('filing_says_transaction_was_not_authorized_by_insider', audit[0]['reasons'])


if __name__ == '__main__':
    unittest.main()