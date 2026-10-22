import copy
import json
import tempfile
import time
import unittest
import threading
import hashlib
from dataclasses import asdict
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pathlib import Path
from unittest.mock import patch

from paper2html.agent import check_anchor, checked_notes
from paper2html.provider import CompatibleProvider, ModelConfig, ModelOutputError, ProviderError
from paper2html.server import Application, handler_for
from paper2html import __version__
from paper2html.pdf import extract
import fitz
from test_agent import FakeProvider, make_pdf, EVIDENCE, QUOTE, FIGURE
import base64


class AnchorRepairTests(unittest.TestCase):
    def test_two_column_pdf_does_not_interleave_left_and_right_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'two-columns.pdf'
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_textbox(fitz.Rect(40, 80, 270, 400), 'LEFT_FIRST\nLEFT_MIDDLE\nLEFT_LAST', fontsize=12)
                page.insert_textbox(fitz.Rect(320, 80, 550, 400), 'RIGHT_FIRST\nRIGHT_MIDDLE\nRIGHT_LAST', fontsize=12)
                doc.save(path)
            text = extract(path)['pages'][0]['text']
            self.assertLess(text.index('LEFT_LAST'), text.index('RIGHT_FIRST'))

    def test_contiguous_source_id_arrays_preserve_exact_long_caption(self):
        text = QUOTE*20
        class Provider:
            def complete_json(self, *args):
                return {'evidence': [], 'figures': [dict(FIGURE, caption_source_id=['P1-S0', 'P1-S500'])]}
        result = checked_notes(Provider(), 'read', {1: text})
        self.assertEqual(result['figures'][0]['caption_quote'], text[:1000])
        self.assertEqual(result['figures'][0]['caption_pdf_page'], 1)

    def test_caption_source_supplies_provisional_image_page_when_model_omits_it(self):
        row = copy.deepcopy(FIGURE)
        row.pop('pdf_page')
        row['caption_source_id'] = 'P3-S0'
        class Provider:
            def complete_json(self, *args):
                return {'evidence': [], 'figures': [row]}
        result = checked_notes(Provider(), 'read', {3: QUOTE})
        self.assertEqual(result['figures'][0]['pdf_page'], 3)
        self.assertEqual(result['figures'][0]['caption_pdf_page'], 3)

    def test_noncontiguous_or_cross_page_source_ids_cannot_fabricate_quotes(self):
        for ids in [['P1-S0', 'P1-S1000'], ['P1-S0', 'P2-S0']]:
            class Provider:
                def complete_json(self, *args):
                    return {'evidence': [dict(EVIDENCE, source_id=ids)], 'figures': []}
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, '同页|连续'):
                checked_notes(Provider(), 'read', {1: QUOTE*20, 2: QUOTE*20})
    def test_id_repairs_can_be_reordered_and_preserve_valid_rows(self):
        valid = copy.deepcopy(EVIDENCE)
        class Provider:
            calls = 0
            def complete_json(self, *args):
                self.calls += 1
                if self.calls == 1:
                    return {'evidence': [valid, dict(EVIDENCE, quote='This fabricated quote is invalid'),
                                         dict(EVIDENCE, quote='Another fabricated quote is invalid')], 'figures': []}
                return {'repairs': [
                    {'id': 'evidence:3', 'item': dict(EVIDENCE, claim='third', source_id='P1-S0')},
                    {'id': 'evidence:2', 'item': dict(EVIDENCE, claim='second', source_id='P1-S0')}]}
        result = checked_notes(Provider(), 'read', {1: QUOTE})
        self.assertEqual(result['evidence'][0], valid)
        self.assertEqual([r['claim'] for r in result['evidence'][1:]], ['second', 'third'])

    def test_full_list_retry_does_not_overwrite_validated_evidence(self):
        valid = copy.deepcopy(EVIDENCE)
        class Provider:
            calls = 0
            def complete_json(self, *args):
                self.calls += 1
                if self.calls == 1:
                    return {'evidence': [valid, dict(EVIDENCE, quote='This fabricated quote is invalid')], 'figures': []}
                return {'evidence': [dict(EVIDENCE, claim='Do not overwrite this'), dict(EVIDENCE, claim='repaired')], 'figures': []}
        result = checked_notes(Provider(), 'read', {1: QUOTE})
        self.assertEqual(result['evidence'][0], valid)
        self.assertEqual(result['evidence'][1]['claim'], 'repaired')

    def test_bad_shape_retry_retains_pending_ids_for_following_attempt(self):
        prompts = []
        class Provider:
            def complete_json(self, system, prompt):
                prompts.append(prompt)
                if len(prompts) == 1:
                    return {'evidence': [dict(EVIDENCE, quote='This fabricated quote is invalid')], 'figures': []}
                if len(prompts) == 2:
                    return {'repairs': []}
                self.assert_id = 'evidence:1' in prompt
                return {'repairs': [{'id': 'evidence:1', 'item': copy.deepcopy(EVIDENCE)}]}
        provider = Provider()
        result = checked_notes(provider, 'read', {1: QUOTE})
        self.assertTrue(provider.assert_id)
        self.assertEqual(result['evidence'], [EVIDENCE])

    def test_typography_normalization_keeps_wrong_pages_and_inventions_invalid(self):
        check_anchor({'pages': [1], 'quote': 'The efficient office experiment'}, {1: 'The efﬁcient ofﬁce experi-\nment'})
        with self.assertRaisesRegex(ValueError, 'pages=\\[2\\]'):
            check_anchor({'pages': [2], 'quote': QUOTE}, {1: QUOTE, 2: 'unrelated text'})

    def test_repair_preserves_valid_rows_and_fills_exact_source_quote(self):
        good, bad = copy.deepcopy(EVIDENCE), dict(EVIDENCE, quote='A fabricated and invalid sentence.')
        calls = []
        class Provider:
            def complete_json(self, system, prompt):
                calls.append(prompt)
                if len(calls) == 1:
                    return {'evidence': [good, bad], 'figures': [], 'warnings': []}
                return {'evidence': [{'claim': '修正后的事实', 'source_id': 'P1-S0'}], 'figures': []}
        result = checked_notes(Provider(), 'read', {1: QUOTE})
        self.assertEqual(result['evidence'][0], good)
        self.assertEqual(result['evidence'][1]['quote'], QUOTE)
        self.assertEqual(result['evidence'][1]['pages'], [1])
        self.assertEqual(len(calls), 2)
        self.assertIn('index', calls[1])

    def test_invalid_evidence_cannot_be_dropped_during_repair(self):
        class Provider:
            calls = 0
            def complete_json(self, *args):
                self.calls += 1
                return {'evidence': [dict(EVIDENCE, quote='Invented statement not in the PDF.')] if self.calls == 1 else [], 'figures': []}
        with self.assertRaisesRegex(ValueError, '不能删除'):
            checked_notes(Provider(), 'read', {1: QUOTE})


class Response:
    def __init__(self, raw): self.raw = raw
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, *args): return self.raw


class TransportDiagnosticsTests(unittest.TestCase):
    def invoke(self, raw):
        provider = CompatibleProvider(ModelConfig('http://localhost:1234/v1', 'test'))
        events = []
        provider.on_event = lambda **event: events.append(event)
        with patch('paper2html.provider.build_opener') as opener:
            opener.return_value.open.return_value = Response(raw)
            try:
                return provider.complete_json('system', 'user')
            finally:
                self.assertEqual(events[-1]['request_state'], 'API 调用结束')

    def test_empty_and_html_responses_are_transport_errors(self):
        for raw, message in [(b'', '空响应'), (b'<html>gateway</html>', 'API 响应不是有效 JSON')]:
            with self.subTest(raw=raw), self.assertRaisesRegex(ProviderError, message):
                self.invoke(raw)

    def test_model_json_error_is_distinct_from_transport_error(self):
        with self.assertRaisesRegex(ModelOutputError, '模型正文不是有效 JSON'):
            self.invoke(json.dumps({'choices': [{'message': {'content': 'not json'}}]}).encode())

    def test_bad_response_shape_and_unusable_usage_are_handled(self):
        with self.assertRaisesRegex(ProviderError, '不兼容'):
            self.invoke(b'{"choices":[null]}')
        self.assertEqual(self.invoke(b'{"choices":[{"message":{"content":"{}"}}],"usage":{"prompt_tokens":"unknown"}}'), {})


class DiagnosticHTTPTests(unittest.TestCase):
    def test_connection_test_csrf_error_reporting_and_job_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Application(Path(tmp)/'data')
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            class Provider:
                usage = {'calls': 1, 'prompt_tokens': 4, 'completion_tokens': 3}
                def complete_json(self, *args): return {'ok': True}
            try:
                with urlopen(url+'/api/config') as response:
                    config = json.load(response)
                self.assertEqual(config['version'], __version__)
                payload = json.dumps({'config': {'base_url': 'https://example.test/v1', 'model': 'test'}}).encode()
                headers = {'Content-Type': 'application/json'}
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url+'/api/test', payload, headers))
                self.assertEqual(caught.exception.code, 403)
                headers['X-Paper2HTML-Token'] = config['csrf_token']
                with patch('paper2html.server.CompatibleProvider', return_value=Provider()):
                    with urlopen(Request(url+'/api/test', payload, headers)) as response:
                        self.assertTrue(json.load(response)['ok'])
                with patch('paper2html.server.CompatibleProvider') as provider:
                    provider.return_value.complete_json.side_effect = ProviderError('HTTP 403 permission denied')
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(Request(url+'/api/test', payload, headers))
                    self.assertEqual(caught.exception.code, 502)
                    self.assertIn('403', json.load(caught.exception)['error'])
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url+'/api/test', b'{broken json', headers))
                self.assertEqual(caught.exception.code, 400)
                with urlopen(url+'/api/jobs') as response:
                    self.assertEqual(json.load(response)['jobs'], [])
            finally:
                server.shutdown(); server.server_close(); thread.join(); app.pool.shutdown()


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdf = make_pdf(self.root/'paper.pdf')
        self.app = Application(self.root/'data', workers=2, reports_dir=self.root/'reports')
        self.payload = {'filename': 'paper.pdf', 'pdf_base64': base64.b64encode(self.pdf).decode(),
                        'config': {'base_url': 'https://example.test/v1', 'model': 'test', 'api_key': 'FIRST_SECRET', 'vision': False}}

    def tearDown(self):
        self.app.pool.shutdown(wait=True)
        self.tmp.cleanup()

    def wait(self, job):
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            result = self.app.store.get(job['id'])
            if result['status'] not in ('running', 'queued'):
                # The finally block writes usage after terminal status.
                if 'usage' in result: return result
            time.sleep(.01)
        self.fail('task did not finish')

    def submit(self, payload, provider):
        with patch('paper2html.server.CompatibleProvider', return_value=provider):
            return self.wait(self.app.submit(payload))

    def test_resume_reuses_validated_steps_but_requires_same_configuration(self):
        class Failing(FakeProvider):
            def complete_json(self, system, prompt):
                if not prompt.startswith(('从以下', '阅读以下', '核对该主图')):
                    raise ProviderError('simulated connection failure')
                return super().complete_json(system, prompt)
        first_provider = Failing()
        first_provider.config = ModelConfig('https://example.test/v1', 'test', 'FIRST_SECRET', vision=False)
        first = self.submit(self.payload, first_provider)
        self.assertEqual(first['status'], 'failed')
        self.assertTrue(list((self.app.store.root/first['id']/'checkpoints').glob('*.json')))
        payload = copy.deepcopy(self.payload)
        payload['resume_from'] = first['id']
        payload['config']['base_url'] = 'https://different.test/v1'
        with self.assertRaisesRegex(ValueError, '配置不一致'):
            self.app.submit(payload)
        payload['config']['base_url'] = self.payload['config']['base_url']
        payload['config']['api_key'] = 'REPLACED_SECRET'
        provider = FakeProvider()
        provider.config = ModelConfig('https://example.test/v1', 'test', 'REPLACED_SECRET', vision=False)
        resumed = self.submit(payload, provider)
        self.assertEqual(resumed['status'], 'completed', resumed)
        self.assertTrue(resumed['checkpoint_reused'])
        self.assertEqual(provider.usage['calls'], 1)
        for path in self.app.store.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(b'FIRST_SECRET', path.read_bytes())
                self.assertNotIn(b'REPLACED_SECRET', path.read_bytes())

    def test_independent_runs_keep_separate_reports_and_do_not_redirect(self):
        original = self.submit(self.payload, FakeProvider())
        payload = dict(self.payload, independent=True)
        compared = self.submit(payload, FakeProvider())
        self.assertEqual(original['status'], 'completed', original)
        self.assertEqual(compared['status'], 'completed', compared)
        self.assertNotEqual(original['report_dir'], compared['report_dir'])
        self.assertTrue(Path(original['report_dir']).is_dir())
        self.assertTrue(Path(compared['report_dir']).is_dir())
        self.assertEqual(len(self.app.store.list_jobs()), 2)

    def test_031_failure_can_resume_with_default_doi_lookup_setting(self):
        class Failing(FakeProvider):
            def complete_json(self, *args): raise ProviderError('failed')
        first=self.submit(self.payload, Failing())
        cfg=asdict(ModelConfig('https://example.test/v1','test','FIRST_SECRET',vision=False))
        cfg.pop('api_key');cfg.pop('metadata_lookup')
        identity={'pdf':hashlib.sha256(self.pdf).hexdigest(),'config':cfg,'metadata':{},'figure_plan':None,'schema':1}
        fingerprint=hashlib.sha256(json.dumps(identity,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest()
        self.app.store.update(first['id'],version='0.3.1',fingerprint=fingerprint)
        resumed=self.submit(dict(self.payload,resume_from=first['id']),FakeProvider())
        self.assertEqual(resumed['status'],'completed',resumed)

    def test_missing_metadata_labels_and_date_can_complete_workflow(self):
        class MissingDate(FakeProvider):
            def complete_json(self, system, prompt):
                result=super().complete_json(system,prompt)
                if prompt.startswith('从以下'):
                    result['metadata'].update(journal_abbr=None,short_name=None,publication_date=None)
                    result['metadata_evidence']=[e for e in result['metadata_evidence'] if e['field']!='publication_date']
                return result
        source={'source':'Crossref','date':'20210416','field':'published-online','doi':'10.1234/example',
                'url':'https://api.crossref.org/works/10.1234/example'}
        with patch('paper2html.bibliography.pdf_publication_dates',return_value=[]), patch(
                'paper2html.bibliography.crossref_publication_date',return_value=source):
            result=self.submit(self.payload,MissingDate())
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['metadata']['publication_date'],'20210416')
        self.assertEqual(result['publication_source'],source)
        self.assertIn('journal_abbr',result['generated_labels'])
        validation=json.loads((Path(result['report_dir'])/'validation.json').read_text(encoding='utf-8'))
        self.assertEqual(validation['publication_source'],source)


if __name__ == '__main__':
    unittest.main()
