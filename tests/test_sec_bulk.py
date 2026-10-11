import unittest

import pandas as pd

from datetime import date

from form4.sec_bulk import match_membership, screen_tables


def tables(owner_rows=None, transaction_rows=None, document_type='4'):
    return {
        'SUBMISSION': pd.DataFrame([{
            'ACCESSION_NUMBER': '0000000001-25-000001', 'DOCUMENT_TYPE': document_type,
            'ISSUERCIK': '123456789', 'ISSUERTRADINGSYMBOL': 'ABC', 'FILING_DATE': '02-JAN-2025',
            'AFF10B5ONE': '1',
        }]),
        'NONDERIV_TRANS': pd.DataFrame(transaction_rows or [{
            'ACCESSION_NUMBER': '0000000001-25-000001', 'TRANS_FORM_TYPE': '4',
            'TRANS_CODE': 'P', 'TRANS_ACQUIRED_DISP_CD': 'A', 'SECURITY_TITLE': 'Common Stock',
            'TRANS_SHARES': '1000', 'TRANS_PRICEPERSHARE': '125',
        }]),
        'REPORTINGOWNER': pd.DataFrame(owner_rows or [{
            'ACCESSION_NUMBER': '0000000001-25-000001', 'RPTOWNERCIK': '0000000042',
            'RPTOWNER_RELATIONSHIP': 'Director,Officer', 'RPTOWNER_TITLE': 'Chief Executive Officer',
        }]),
    }


class SecBulkScreenTests(unittest.TestCase):
    def test_screens_qualifying_purchase_and_keeps_it_unapproved(self):
        candidates, counts = screen_tables(tables(), {'0123456789'})

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['purchase_value'], 125000)
        self.assertEqual(candidates[0]['role'], 'officer_director')
        self.assertEqual(candidates[0]['accepted_at'], '')
        self.assertEqual(candidates[0]['review'], 'requires_original_filing_review')
        self.assertEqual(candidates[0]['tenb5_plan_indicator'], 'yes')
        self.assertEqual(counts['owner_purchase_rows'], 1)

    def test_joint_owner_purchase_value_is_not_attributed(self):
        owner_rows = [
            {'ACCESSION_NUMBER': '0000000001-25-000001', 'RPTOWNERCIK': '0000000042',
             'RPTOWNER_RELATIONSHIP': 'Director', 'RPTOWNER_TITLE': 'Director'},
            {'ACCESSION_NUMBER': '0000000001-25-000001', 'RPTOWNERCIK': '0000000043',
             'RPTOWNER_RELATIONSHIP': 'Officer', 'RPTOWNER_TITLE': 'President'},
        ]

        candidates, _ = screen_tables(tables(owner_rows=owner_rows), {'0123456789'})

        self.assertEqual(len(candidates), 2)
        self.assertTrue(all(row['purchase_value'] == '' for row in candidates))
        self.assertTrue(all(row['review'] == 'requires_joint_owner_allocation' for row in candidates))

    def test_excludes_non_purchase_non_common_and_below_threshold(self):
        cases = [
            {'TRANS_CODE': 'M', 'TRANS_PRICEPERSHARE': '125'},
            {'TRANS_CODE': 'P', 'TRANS_ACQUIRED_DISP_CD': 'D'},
            {'TRANS_CODE': 'P', 'SECURITY_TITLE': 'Preferred Stock'},
            {'TRANS_CODE': 'P', 'TRANS_SHARES': '10', 'TRANS_PRICEPERSHARE': '10'},
        ]
        for replacement in cases:
            with self.subTest(replacement=replacement):
                row = {
                    'ACCESSION_NUMBER': '0000000001-25-000001', 'TRANS_FORM_TYPE': '4',
                    'TRANS_CODE': 'P', 'TRANS_ACQUIRED_DISP_CD': 'A', 'SECURITY_TITLE': 'Common Stock',
                    'TRANS_SHARES': '1000', 'TRANS_PRICEPERSHARE': '125', **replacement,
                }
                candidates, _ = screen_tables(tables(transaction_rows=[row]), {'0123456789'})
                self.assertEqual(candidates, [])

    def test_matches_membership_on_filing_date_only(self):
        candidates = [
            {'ticker': 'ABC', 'filing_date': '02-JAN-2025'},
            {'ticker': 'OLD.A', 'filing_date': '31-DEC-2024'},
            {'ticker': 'ABC', 'filing_date': '01-JAN-2026'},
        ]
        membership = {
            'ABC': [(date(2024, 1, 1), date(2026, 1, 1))],
            'OLDA': [(date(2024, 1, 1), date(2025, 1, 1))],
        }

        matched = match_membership(candidates, membership)

        self.assertEqual([row['filing_date'] for row in matched], ['02-JAN-2025', '31-DEC-2024'])
        self.assertTrue(all(row['membership_match'] == 'ticker_date_candidate' for row in matched))


if __name__ == '__main__':
    unittest.main()