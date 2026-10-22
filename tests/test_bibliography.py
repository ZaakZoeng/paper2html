import copy
import json
import unittest
from unittest.mock import patch

from paper2html.bibliography import (filename_labels, pdf_publication_dates, select_metadata_pages,
    complete_publication_date, crossref_publication_date, MissingPublicationDate)
from paper2html.agent import validate_metadata, checked_call
from test_agent import META, QUOTE


def date_record():
    return {'DOI': META['doi'], 'title': [META['title']], 'container-title': [META['journal']],
            'published-online': {'date-parts': [[2021, 4, 16]]},
            'published-print': {'date-parts': [[2021, 5, 1]]}}


class Response:
    def __init__(self, record): self.raw=json.dumps({'message': record}).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *args): return self.raw


class BibliographyTests(unittest.TestCase):
    def partial(self):
        metadata = dict(META, journal_abbr=None, short_name=None, publication_date=None)
        evidence = [{'field': key, 'pages': [1], 'quote': QUOTE} for key in ('title','authors','journal','doi')]
        return {'metadata': metadata, 'metadata_evidence': evidence}

    def test_missing_filename_labels_are_generated_without_claiming_official_abbreviation(self):
        result = self.partial()
        validate_metadata(result, {1: QUOTE}, {}, allow_missing_date=True)
        self.assertEqual(result['metadata']['journal_abbr'], 'Example-Journal')
        self.assertTrue(result['metadata']['short_name'].startswith('An-example-paper-'))
        self.assertIn('short_name', result['generated_labels'])
        self.assertIsNone(result['metadata']['publication_date'])

    def test_filename_labels_preserve_user_values_and_handle_non_ascii_names(self):
        metadata = dict(META)
        self.assertEqual(filename_labels(metadata), {})
        metadata = dict(META, title='中文标题', journal='中文期刊', journal_abbr=None, short_name=None)
        first = filename_labels(metadata)
        self.assertEqual(first, filename_labels(dict(META, title='中文标题', journal='中文期刊', journal_abbr=None, short_name=None)))
        self.assertTrue(metadata['short_name'].isascii())
        self.assertLessEqual(len(metadata['journal_abbr']), 80)

    def test_received_and_accepted_dates_cannot_be_used_as_publication_dates(self):
        pages={15:'Received: 11 June 2020; Accepted: 9 March 2021;'}
        self.assertEqual(pdf_publication_dates(pages), [])
        result=self.partial()
        with self.assertRaisesRegex(MissingPublicationDate, 'DOI 查询已关闭'):
            complete_publication_date(result, pages, allow_lookup=False)
        self.assertIsNone(result['metadata']['publication_date'])

    def test_model_cannot_promote_accepted_date_to_publication_date(self):
        result=self.partial()
        result['metadata']['publication_date']='20210309'
        quote='Received: 11 June 2020; Accepted: 9 March 2021;'
        result['metadata_evidence'].append({'field':'publication_date','pages':[15],'quote':quote})
        validate_metadata(result,{1:QUOTE,15:quote},{},allow_missing_date=True)
        self.assertIsNone(result['metadata']['publication_date'])
        self.assertFalse(any(e['field']=='publication_date' for e in result['metadata_evidence']))

    def test_late_publication_marker_is_kept_even_when_front_pages_fill_budget(self):
        pages=[{'page': n, 'text': 'x'*12000} for n in range(1,4)]
        pages.append({'page': 18, 'text': 'Received: 1 January 2020;\nPublished online: 16 April 2021\nReferences'})
        selected=select_metadata_pages(pages)
        self.assertTrue(any(p['page']==18 and '16 April' in p['text'] for p in selected))
        self.assertLessEqual(sum(len(p['text']) for p in selected),36000)
        result=self.partial()
        with patch('paper2html.bibliography.crossref_publication_date') as lookup:
            complete_publication_date(result, {p['page']:p['text'] for p in pages})
            lookup.assert_not_called()
        self.assertEqual(result['metadata']['publication_date'],'20210416')
        self.assertEqual(result['publication_source']['source'],'PDF')
        validate_metadata(result, {1: QUOTE, 18:pages[-1]['text']}, {})

    def test_conflicting_pdf_publication_dates_require_confirmation(self):
        with self.assertRaisesRegex(MissingPublicationDate, '多个'):
            complete_publication_date(self.partial(), {1:'Published: 16 April 2021', 2:'Published: 20 April 2021'})

    def fetch(self, record):
        with patch('paper2html.bibliography.build_opener') as opener:
            opener.return_value.open.return_value=Response(record)
            return crossref_publication_date(META)

    def test_crossref_prefers_online_date_and_records_provenance(self):
        source=self.fetch(date_record())
        self.assertEqual(source['date'],'20210416')
        self.assertEqual(source['field'],'published-online')
        self.assertEqual(source['doi'],META['doi'])
        result=self.partial()
        validate_metadata(result, {1:QUOTE}, {}, allow_missing_date=True)
        complete_publication_date(result, {1:QUOTE}, lookup=lambda metadata:source)
        validate_metadata(result, {1:QUOTE}, {}, external_date=result['publication_source'])
        self.assertIn('不属于 PDF',result['date_basis'])
        with self.assertRaisesRegex(ValueError, 'publication_date.*原文依据'):
            validate_metadata(result, {1:QUOTE}, {})

    def test_crossref_rejects_mismatched_identity_and_partial_or_creation_dates(self):
        for key, value in [('DOI','10.1234/wrong'),('title',['Wrong paper']),('container-title',['Wrong journal'])]:
            record=date_record(); record[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): self.fetch(record)
        record=date_record()
        record.pop('published-print')
        record['published-online']={'date-parts':[[2021,4]]}
        record['created']={'date-parts':[[2021,4,16]]}
        with self.assertRaisesRegex(ValueError,'完整'): self.fetch(record)

    def test_lookup_failure_is_actionable_and_does_not_fabricate_date(self):
        def unavailable(metadata): raise TimeoutError()
        result=self.partial()
        with self.assertRaisesRegex(MissingPublicationDate,'publication_date'):
            complete_publication_date(result, {1:QUOTE}, lookup=unavailable)
        self.assertIsNone(result['metadata']['publication_date'])

    def test_missing_fact_does_not_trigger_three_llm_repair_calls(self):
        class Provider:
            calls=0
            def complete_json(self,*args): self.calls+=1; return {}
        provider=Provider()
        def checker(result): raise MissingPublicationDate('missing')
        with self.assertRaises(MissingPublicationDate): checked_call(provider,'prompt',checker)
        self.assertEqual(provider.calls,1)

    def test_provided_date_does_not_use_network(self):
        result={'metadata':dict(META)}
        def unexpected(metadata): self.fail('lookup must not run')
        complete_publication_date(result,{1:'Accepted: 9 March 2021'},lookup=unexpected)
        self.assertEqual(result['metadata']['publication_date'],META['publication_date'])
