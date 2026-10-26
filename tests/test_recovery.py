import copy
import json
import unittest
from paper2html.agent import check_anchor, validate_notes, checked_call, validate_article
from paper2html.provider import parse_json, ModelOutputError, OutputTruncated, ProviderError
from paper2html.recovery import assemble_article, repair_context
from test_agent import FIGURE, EVIDENCE, META, CAPTION, article


class RecoveryTests(unittest.TestCase):
    def test_json_trailing_commas_and_bom_leave_string_contents_unchanged(self):
        raw='\ufeff```json\n{"quote":"commas ,} and ,] and \\\"quoted\\\"", "rows":[1,2,],}\n```'
        result=parse_json(raw)
        self.assertEqual(result,{'quote':'commas ,} and ,] and "quoted"','rows':[1,2]})

    def test_malformed_json_is_carried_to_llm_repair(self):
        raw='{"answer": "value"'
        prompts=[]
        class Provider:
            def complete_json(self,system,user):
                prompts.append(user)
                return parse_json(raw if len(prompts)==1 else '{"answer":"value"}')
        result=checked_call(Provider(),'Return answer JSON',lambda r:None)
        self.assertEqual(result,{'answer':'value'})
        self.assertIn(raw,prompts[1])
        self.assertEqual(len(prompts),2)

    def test_repair_budget_does_not_append_partial_or_oversized_output(self):
        error=ModelOutputError('bad','x'*100)
        self.assertEqual(repair_context(None,error,99),'')
        self.assertIn('x'*100,repair_context(None,error,100))
        self.assertIsNone(ModelOutputError('bad','x'*60001).output)

    def test_truncated_output_gets_bounded_repair_without_changing_token_config(self):
        prompts=[]
        class Provider:
            def complete_json(self,system,user):
                prompts.append(user)
                if len(prompts)==1: raise OutputTruncated('输出被截断，请精简','{"article":')
                return {'article':{}}
        self.assertEqual(checked_call(Provider(),'schema',lambda r:None),{'article':{}})
        self.assertIn('精简',prompts[1])
        self.assertIn('{"article":',prompts[1])

    def test_credentials_and_transport_errors_do_not_trigger_llm_format_retry(self):
        class Provider:
            calls=0
            def complete_json(self,system,user):
                self.calls+=1
                raise ProviderError('密钥无效')
        p=Provider()
        with self.assertRaises(ProviderError): checked_call(p,'schema',lambda r:None)
        self.assertEqual(p.calls,1)

    def test_safe_note_shapes_normalize_without_inventing_quote(self):
        f=copy.deepcopy(FIGURE)
        f.update(source='Fig. 1',pdf_page='1',caption_zh='完整中文图注')
        result={'evidence':[{'claim':'已有事实','pages':'1','quote':EVIDENCE['quote']}],'figures':[f]}
        validate_notes(result,{1:EVIDENCE['quote']+'\n'+CAPTION})
        self.assertEqual(result['evidence'][0]['pages'],[1])
        self.assertEqual(f['source'],'Figure 1')
        self.assertEqual(f['caption_zh'],['完整中文图注'])
        with self.assertRaises(ValueError):
            check_anchor({'pages':'1','quote':'This quotation was never in the source PDF.'},{1:CAPTION})

    def test_program_assembles_verified_captions_and_normalizes_paragraphs(self):
        data={'article':article(),'metadata':copy.deepcopy(META),'evidence':[EVIDENCE]}
        data['metadata']['one_line_summary']='事实摘要'
        data['article']['sections'][-1]['figures']=['Fig. 1']
        data['article']['sections'][0]['paragraphs']='事实段落'
        assemble_article(data,[FIGURE])
        self.assertEqual(data['article']['sections'][-1]['figures'],[FIGURE])
        self.assertEqual(data['article']['sections'][0]['paragraphs'],['事实段落'])
        validate_article(data,data['metadata'],[EVIDENCE],[FIGURE])
        data['article']['sections'][-1]['figures'][0]['caption_zh'].append('mutation')
        self.assertNotIn('mutation',FIGURE['caption_zh'])

    def test_repeated_captions_are_replaced_with_trusted_values(self):
        data={'article':article()}
        data['article']['sections'][-1]['figures'][0]['caption_zh']=['incorrect copied caption']
        assemble_article(data,[FIGURE])
        self.assertEqual(data['article']['sections'][-1]['figures'][0]['caption_zh'],FIGURE['caption_zh'])

    def test_unknown_or_duplicate_figure_cannot_bypass_coverage_check(self):
        data={'article':article(),'metadata':dict(META,one_line_summary='事实摘要'),'evidence':[EVIDENCE]}
        data['article']['sections'][-1]['figures']=['Figure 99']
        with self.assertRaises(ValueError): assemble_article(data,[FIGURE])
        data['article']['sections'][-1]['figures']=['Figure 1','Figure 1']
        assemble_article(data,[FIGURE])
        with self.assertRaisesRegex(ValueError,'exactly once'): validate_article(data,data['metadata'],[EVIDENCE],[FIGURE])
