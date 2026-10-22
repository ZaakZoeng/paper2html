import copy
import unittest
from paper2html.agent import validate_figure_review, validate_notes, checked_call, check_anchor


class CaptionReviewTests(unittest.TestCase):
    def review(self):
        return {'figures': [{'source': 'Figure 4', 'pdf_page': 9,
                'caption_pdf_page': 9, 'title': '免疫浸润',
                'caption_quote': 'Fig. 4 Spatial proteomics analysis of tumor immune contexture.',
                'caption_zh': ['图4 肿瘤免疫微环境的空间蛋白质组学分析。']}],
                'warnings': ['正文与图注统计方法不同。']}

    def test_extraneous_caption_explanations_never_enter_evidence_ledger(self):
        for evidence in ([{'pdf_page': 9, 'text': 'caption explanation'}],
                         [{'claim': 'explanation', 'pdf_page': 9, 'quote': 'quote'}],
                         [{'pdf_page': 9, 'quote': 'quote'}]):
            result = self.review()
            result['evidence'] = evidence
            validate_figure_review(result, {9: result['figures'][0]['caption_quote']}, 'Figure 4')
            self.assertNotIn('evidence', result)
            self.assertEqual(result['warnings'], ['正文与图注统计方法不同。'])

    def test_caption_anchor_and_requested_figure_remain_strict(self):
        result = self.review()
        for pages, source in (({9: 'Invented caption'}, 'Figure 4'),
                              ({8: result['figures'][0]['caption_quote']}, 'Figure 4'),
                              ({9: result['figures'][0]['caption_quote']}, 'Figure 3')):
            with self.assertRaises(ValueError):
                validate_figure_review(copy.deepcopy(result), pages, source)

    def test_reading_evidence_still_requires_claim(self):
        with self.assertRaisesRegex(ValueError, 'claim'):
            validate_notes({'evidence': [{'pages': [9], 'quote': 'a valid original quotation'}], 'figures': []}, {9: 'a valid original quotation'})

    def test_single_page_alias_is_normalized_without_changing_quote(self):
        row = {'pdf_page': 9, 'quote': 'a valid original quotation'}
        check_anchor(row, {9: row['quote']})
        self.assertEqual(row['pages'], [9])
        row['pages'] = [8]
        with self.assertRaises(ValueError):
            check_anchor(row, {9: row['quote']})

    def test_llm_repair_receives_previous_output_and_original_schema(self):
        prompts = []
        class Provider:
            def complete_json(self, system, user):
                prompts.append(user)
                return {'wrong': 'retained value'} if len(prompts) == 1 else {'required': 'retained value'}
        def check(result):
            if 'required' not in result:
                raise ValueError('required 字段缺失')
        self.assertEqual(checked_call(Provider(), 'Return {"required":"text"}', check), {'required': 'retained value'})
        self.assertIn('"wrong": "retained value"', prompts[1])
        self.assertIn('required 字段缺失', prompts[1])
        self.assertTrue(prompts[1].startswith('Return {"required":"text"}'))
