import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz
from paper2html.agent import Agent,validate_metadata
from paper2html.bibliography import complete_publication_date,MissingPublicationDate
from paper2html.publication import reference_url,date_component
from paper2html.store import Store
from test_agent import META,QUOTE,EVIDENCE,FakeProvider,article


BIO='bioRxiv preprint\nhttps://doi.org/10.64898/2026.09.29.755561\nthis version posted October 6, 2026.'
ARXIV='arXiv:2501.12345v2 [cs.AI] 6 October 2026'

def partial():
    return {'metadata':dict(META,journal=None,journal_abbr=None,publication_date=None,doi=None,short_name=None),
            'metadata_evidence':[{'field':k,'pages':[1],'quote':QUOTE} for k in ('title','authors')]}


class PreprintMetadataTests(unittest.TestCase):
    def validate(self,text,result=None,overrides=None):
        result=copy.deepcopy(result or partial())
        validate_metadata(result,{1:QUOTE+'\n'+text},overrides or {},allow_missing_date=True)
        with patch('paper2html.bibliography.crossref_publication_date') as lookup:
            complete_publication_date(result,{1:QUOTE+'\n'+text})
            lookup.assert_not_called()
        validate_metadata(result,{1:QUOTE+'\n'+text},overrides or {},external_date=result.get('publication_source'))
        return result

    def test_biorxiv_date_is_current_version_not_doi_or_journal_date(self):
        result=self.validate(BIO)
        m=result['metadata']
        self.assertEqual(m['publication_date'],'20261006')
        self.assertEqual(m['publication_type'],'preprint')
        self.assertEqual(m['preprint_server'],'bioRxiv')
        self.assertIsNone(m['journal'])
        self.assertEqual(m['journal_abbr'],'bioRxiv')
        self.assertEqual(m['doi'],'10.64898/2026.09.29.755561')
        self.assertEqual(result['publication_source']['kind'],'preprint_version')
        self.assertIn('不是正式',result['date_basis'])

    def test_medrxiv_preprint_is_identified(self):
        result=self.validate(BIO.replace('bioRxiv','medRxiv'))
        self.assertEqual(result['metadata']['preprint_server'],'medRxiv')

    def test_arxiv_without_doi_uses_versioned_identifier(self):
        result=self.validate(ARXIV)
        self.assertIsNone(result['metadata']['doi'])
        self.assertEqual(result['metadata']['publication_date'],'20261006')
        self.assertEqual(reference_url(result['metadata']),'https://arxiv.org/abs/2501.12345v2')

    def test_preprint_without_date_is_explicitly_undated(self):
        result=self.validate('arXiv:2501.12345v2 [cs.AI]')
        self.assertIsNone(result['metadata']['publication_date'])
        self.assertEqual(date_component(result['metadata']),'undated')
        self.assertIn('不推测',result['date_basis'])

    def test_biorxiv_doi_date_and_accepted_date_do_not_supply_posting_date(self):
        result=self.validate('bioRxiv preprint\nhttps://doi.org/10.64898/2026.09.29.755561\nAccepted: 9 March 2026')
        self.assertIsNone(result['metadata']['publication_date'])

    def test_conflicting_version_dates_require_confirmation(self):
        text=BIO+'\nthis version posted October 7, 2026.'
        with self.assertRaisesRegex(MissingPublicationDate,'预印本.*多个'):
            self.validate(text)
        result=self.validate(text,overrides={'publication_date':'20261007'})
        self.assertEqual(result['metadata']['publication_date'],'20261007')
        self.assertEqual(result['publication_source']['source'],'user')

    def test_journal_paper_cannot_be_relabelled_by_model_or_late_reference(self):
        result=partial();result['metadata'].update(publication_type='preprint',preprint_server='arXiv')
        with self.assertRaisesRegex(ValueError,'journal'):
            validate_metadata(result,{1:QUOTE,8:ARXIV},{},allow_missing_date=True)

    def test_preprint_missing_title_still_fails(self):
        result=partial();result['metadata']['title']=None
        with self.assertRaisesRegex(ValueError,'title'):
            self.validate(BIO,result)

    def test_arxiv_cannot_inject_unrelated_source_url(self):
        result=partial();result['metadata']['source_url']='https://untrusted.example/'
        result=self.validate(ARXIV,result)
        self.assertEqual(result['metadata']['source_url'],'https://arxiv.org/abs/2501.12345v2')

    def test_arxiv_link_keeps_uploaded_version_even_if_journal_doi_exists(self):
        metadata={'publication_type':'preprint','preprint_server':'arXiv','preprint_id':'2501.12345v2','doi':'10.1234/published'}
        self.assertEqual(reference_url(metadata),'https://arxiv.org/abs/2501.12345v2')

    def test_reference_to_preprint_does_not_reclassify_journal_article(self):
        result={'metadata':dict(META),'metadata_evidence':[{'field':k,'pages':[1],'quote':QUOTE} for k in ('title','authors','journal','publication_date','doi')]}
        pages={1:QUOTE+'\nWe compare with a bioRxiv preprint in the introduction.',2:BIO}
        validate_metadata(result,pages,{})
        self.assertNotEqual(result['metadata'].get('publication_type'),'preprint')


class PreprintWorkflowTests(unittest.TestCase):
    def run_workflow(self,marker):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);store=Store(root/'data',root/'reports');job='a'*32
        store.create(job,'preprint.pdf','fake');work=store.root/job;work.mkdir()
        with fitz.open() as doc:
            page=doc.new_page()
            page.insert_textbox(fitz.Rect(40,40,550,700),'An example paper\nA. Author\n'+QUOTE+'\n'+marker+'\n'+('The authors describe research methods and controlled observations.\n'*8),fontsize=11)
            doc.save(work/'source.pdf')
        class Provider(FakeProvider):
            def complete_json(self,system,user):
                self.usage['calls']+=1
                if user.startswith('从以下'):return partial()
                if user.startswith('阅读以下'):return {'evidence':[copy.deepcopy(EVIDENCE)],'figures':[],'warnings':[]}
                draft=article()
                for section in draft['sections']:section.pop('figures',None)
                link='https://arxiv.org/abs/2501.12345v2' if 'arXiv' in marker else 'https://doi.org/10.64898/2026.09.29.755561'
                draft['reference']='A. Author. An example paper. '+link
                return {'article':draft}
        with patch('paper2html.bibliography.crossref_publication_date') as lookup:
            Agent(store,Provider()).run(job)
            lookup.assert_not_called()
        result=store.get(job);self.assertEqual(result['status'],'completed',result)
        folder=store.root/result['output_dir']
        md=next(folder.glob('*.md')).read_text(encoding='utf-8')
        html=next(folder.glob('*.html')).read_text(encoding='utf-8')
        self.assertIn('**预印本**',md)
        self.assertIn('预印本',html)
        self.assertIn('不是正式期刊发表日期',md)
        self.assertTrue((work/'report.zip').is_file())
        validation=json.loads((folder/'validation.json').read_text(encoding='utf-8'))
        self.assertEqual(validation['publication_source']['kind'],'preprint_version')
        return result,md

    def test_biorxiv_end_to_end(self):
        result,md=self.run_workflow(BIO)
        self.assertTrue(result['canonical_name'].startswith('20261006_bioRxiv_'))

    def test_arxiv_no_doi_end_to_end(self):
        result,md=self.run_workflow(ARXIV)
        self.assertIn('https://arxiv.org/abs/2501.12345v2',md)
        self.assertNotIn('10.1234/example',md)

    def test_arxiv_unknown_date_end_to_end(self):
        result,md=self.run_workflow('arXiv:2501.12345v2 [cs.AI]')
        self.assertTrue(result['canonical_name'].startswith('undated_arXiv_'))
        self.assertIn('发布日期未确认',md)
