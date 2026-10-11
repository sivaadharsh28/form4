import unittest
import html

from form4.sec_enrich import (enrich_candidates, parse_original_documents,
                              resolve_xml_url, sec_archive_cik, select_ownership_xml,
                              xml_names_from_index_html)


class SecEnrichmentContractTests(unittest.TestCase):
    def test_requires_sec_contact_before_network_access(self):
        with self.assertRaisesRegex(ValueError, 'contact email'):
            enrich_candidates(email='')

    def test_parses_acceptance_time_with_original_ownership_xml(self):
        headers = (b'<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20160317163454\n'
                   b'<ACCESSION-NUMBER>0001122304-16-000216\n')
        ownership_xml = b'''<?xml version="1.0"?>
<ownershipDocument>
<documentType>4</documentType>
<issuer><issuerCik>1122304</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>1200743</rptOwnerCik></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction>
<securityTitle><value>Common Stock</value></securityTitle>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares>
<transactionPricePerShare><value>20</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument>'''

        rows = parse_original_documents(headers, ownership_xml)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['accepted_at'], '2016-03-17T16:34:54-04:00')
        self.assertEqual(rows[0]['accession'], '0001122304-16-000216')
        self.assertEqual(rows[0]['purchase_value'], 2000)

    def test_selects_actual_xml_filename_from_sec_directory_index(self):
        index = {'directory': {'item': [
            {'name': 'filing-doc.htm'},
            {'name': 'form4.xml'},
        ]}}

        self.assertEqual(select_ownership_xml(index), 'form4.xml')

    def test_parses_acceptance_from_html_escaped_index_page(self):
        headers = html.escape('<ACCEPTANCE-DATETIME>20210322161530\n'
                              '<ACCESSION-NUMBER>0001127602-21-002713')
        ownership_xml = b'''<?xml version="1.0"?>
<ownershipDocument><documentType>4</documentType>
<issuer><issuerCik>1058090</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>1188560</rptOwnerCik></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares>
<transactionPricePerShare><value>20</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'''

        rows = parse_original_documents(headers.encode(), ownership_xml)

        self.assertEqual(rows[0]['accepted_at'], '2021-03-22T16:15:30-04:00')

    def test_finds_xml_names_in_sec_index_html(self):
        html_index = b'<a href="filing.xml">XML</a><a href="primary.htm">HTML</a>'
        self.assertEqual(xml_names_from_index_html(html_index), ['filing.xml'])

    def test_resolves_root_relative_and_local_xml_links(self):
        archive_dir = 'edgar/data/1127602/000112760221002713'
        self.assertEqual(resolve_xml_url(archive_dir, 'form4.xml'),
                         'https://www.sec.gov/Archives/edgar/data/1127602/000112760221002713/form4.xml')
        self.assertEqual(resolve_xml_url(archive_dir, '/Archives/edgar/data/789019/000112760221002713/form4.xml'),
                         'https://www.sec.gov/Archives/edgar/data/789019/000112760221002713/form4.xml')

    def test_html_index_orders_raw_xml_before_xsl_rendered_xml(self):
        index_html = (b'<a href="/Archives/edgar/data/789019/acc/xslF345X03/form4.xml">Rendered</a>'
                      b'<a href="/Archives/edgar/data/789019/acc/form4.xml">Raw</a>')
        self.assertEqual(xml_names_from_index_html(index_html), [
            '/Archives/edgar/data/789019/acc/form4.xml',
            '/Archives/edgar/data/789019/acc/xslF345X03/form4.xml',
        ])

    def test_uses_issuer_cik_for_filings_submitted_by_an_agent(self):
        self.assertEqual(sec_archive_cik('0000789019', '0001062993-21-002647'), '789019')

    def test_parses_acceptance_from_legacy_sec_index_html(self):
        headers = b'''<div class="infoHead">Accepted</div>
<div class="info">2021-03-11 18:04:46</div>
<div><strong>SEC <acronym>Accession</acronym> <acronym>No.</acronym></strong> 0001062993-21-002647</div>'''
        xml = b'''<?xml version="1.0"?><ownershipDocument><documentType>4</documentType>
<issuer><issuerCik>789019</issuerCik></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>1111111</rptOwnerCik></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><securityTitle><value>Common Stock</value></securityTitle>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding><transactionAmounts>
<transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>20</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'''

        rows = parse_original_documents(headers, xml)

        self.assertEqual(rows[0]['accepted_at'], '2021-03-11T18:04:46-05:00')
        self.assertEqual(rows[0]['accession'], '0001062993-21-002647')


if __name__ == '__main__':
    unittest.main()