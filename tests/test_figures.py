import copy
import json
import tempfile
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from paper2html.agent import Agent
from paper2html.figures import prepare_figures
from paper2html.locking import directory_lock
from paper2html.provider import CompatibleProvider, ModelConfig
from paper2html.store import Store
from paper2html.engine.render_html import build
from test_agent import make_pdf, FIGURE, FakeProvider


class VisionProvider(FakeProvider):
    def __init__(self, complete=True):
        super().__init__()
        self.config=SimpleNamespace(vision=True,api_key='')
        self.complete=complete
        self.images=[]

    def complete_json_with_images(self,system,user,images):
        self.usage['calls']+=1
        self.images.append(images)
        if user.startswith('依次给出'):
            return {'source':'Figure 1','pdf_page':1,'regions':[{'pdf_page':1,'clip':[40,300,550,500]}]}
        return {'complete':self.complete,'reason':'核对全图各分面'}


class FigureAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.pdf=self.root/'input.pdf';make_pdf(self.pdf)

    def tearDown(self):self.tmp.cleanup()

    def vector_pdf(self):
        with fitz.open(self.pdf) as doc:
            doc[0].draw_rect(fitz.Rect(20,300,35,450),color=(0,0,1))
            doc.save(self.root/'vector.pdf')
        return self.root/'vector.pdf'

    def test_vision_agent_exports_complete_immutable_report(self):
        store=Store(self.root/'data');job='a'*32;store.create(job,'input.pdf','fake')
        work=store.root/job;work.mkdir();(work/'source.pdf').write_bytes(self.pdf.read_bytes())
        model=VisionProvider();Agent(store,model).run(job)
        result=store.get(job);self.assertEqual(result['status'],'completed',result)
        self.assertEqual(len(model.images),2)
        folder=store.root/result['output_dir']
        self.assertTrue((folder/'assets'/'figure-1.png').exists())
        self.assertTrue((store.reports/result['canonical_name']/'bundle.json').exists())
        self.assertEqual(len(list(folder.glob('*.html'))),1)
        review=json.loads((folder/'visual-review.json').read_text(encoding='utf-8'));self.assertTrue(review[0]['complete'])
        page=(folder/(result['canonical_name']+'.html')).read_text(encoding='utf-8')
        self.assertIn('data:image/png;base64,',page)
        self.assertIn('color:#ffa900;',page) # 原文信息
        self.assertIn('border-left:4px solid #0396ff;',page)

    def test_failed_visual_check_publishes_explicit_original_page_preview(self):
        store=Store(self.root/'data');job='a'*32;store.create(job,'input.pdf','fake')
        work=store.root/job;work.mkdir();(work/'source.pdf').write_bytes(self.pdf.read_bytes())
        Agent(store,VisionProvider(False)).run(job)
        job=store.get(job)
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(job['fallback_figures'],['Figure 1'])
        folder=store.root/job['output_dir']
        review=json.loads((folder/'visual-review.json').read_text(encoding='utf-8'))[0]
        self.assertFalse(review['complete'])
        self.assertEqual(review['fallback'],'source-pages')
        record=json.loads((folder/'figures.json').read_text(encoding='utf-8'))['figures']['Figure 1']
        self.assertEqual(record['method'],'source-pages-fallback')
        self.assertEqual(record['review_status'],'unconfirmed')
        validation=json.loads((folder/'validation.json').read_text(encoding='utf-8'))
        self.assertEqual(validation['figure_review_status'],'needs_review')
        self.assertEqual(validation['fallback_figures'],['Figure 1'])
        self.assertIn('原页预览',(folder/(job['canonical_name']+'.html')).read_text(encoding='utf-8'))
        markdown=(folder/(job['canonical_name']+'.md')).read_text(encoding='utf-8')
        self.assertLess(markdown.index('**原页预览**'),markdown.index('![Figure 1]'))

    def test_incomplete_crop_is_relocated_and_rechecked_with_new_pixels(self):
        class Adaptive(VisionProvider):
            def __init__(self):
                super().__init__(); self.locations=0; self.checks=0; self.prompts=[]
            def complete_json_with_images(self, system, user, images):
                self.prompts.append(user); self.images.append(images)
                if user.startswith('依次给出'):
                    self.locations+=1
                    return {'source':'Figure 1','pdf_page':1,'regions':[{'pdf_page':1,'clip':[40,320 if self.locations==1 else 300,550,500]}]}
                self.checks+=1
                return {'complete':self.checks>1,'reason':'顶部被截断，需要向上扩大裁切'}
        work=self.root/'adaptive';work.mkdir();model=Adaptive()
        prepare_figures(model,self.pdf,[FIGURE],work)
        self.assertEqual((model.locations,model.checks),(2,2))
        self.assertIn('顶部被截断',model.prompts[2])
        self.assertIn('previous_plan',model.prompts[2])
        self.assertNotEqual(model.images[1][-1],model.images[3][-1])
        self.assertTrue((work/'figure-check-1/attempt-1/assets/figure-1.png').exists())
        self.assertTrue((work/'figure-check-1/attempt-2/assets/figure-1.png').exists())

    def test_same_bad_coordinates_cannot_pass_by_rechecking(self):
        work=self.root/'same';work.mkdir();model=VisionProvider(False)
        _,figures,warnings=prepare_figures(model,self.pdf,[FIGURE],work)
        self.assertIn('原页预览',figures[0]['caption_zh'][0])
        self.assertTrue(warnings)
        self.assertEqual(len(model.images),4) # one review, then two relocation attempts
        self.assertFalse(list((work/'checkpoints').glob('vision-*.json')))

    def test_verified_vision_checkpoints_resume_but_model_changes_invalidate(self):
        work=self.root/'first';work.mkdir();model=VisionProvider()
        prepare_figures(model,self.pdf,[FIGURE],work)
        second=self.root/'second';second.mkdir()
        shutil.copytree(work/'checkpoints',second/'checkpoints')
        resumed=VisionProvider()
        path,_,_=prepare_figures(resumed,self.pdf,[FIGURE],second)
        self.assertEqual(resumed.images,[])
        self.assertTrue(path.exists())
        self.assertTrue((second/'figure-review/assets/figure-1.png').exists())
        resumed.config.model='changed-model'
        prepare_figures(resumed,self.pdf,[FIGURE],second)
        self.assertEqual(len(resumed.images),2)

    def test_different_pdf_cannot_reuse_vision_checkpoint(self):
        work=self.root/'cache';work.mkdir();model=VisionProvider()
        prepare_figures(model,self.pdf,[FIGURE],work)
        different=self.vector_pdf();fresh=VisionProvider()
        prepare_figures(fresh,different,[FIGURE],work)
        self.assertEqual(len(fresh.images),2)

    def test_failure_on_later_figure_preserves_earlier_verified_checkpoint(self):
        class TwoFigures(VisionProvider):
            def __init__(self, fail_second):
                super().__init__(); self.fail_second=fail_second
            def complete_json_with_images(self, system, user, images):
                self.images.append(images)
                source='Figure 2' if 'Figure 2' in user else 'Figure 1'
                if user.startswith('依次给出'):
                    return {'source':source,'pdf_page':1,'regions':[{'pdf_page':1,'clip':[40,300,550,500]}]}
                return {'complete':not (source=='Figure 2' and self.fail_second),'reason':'第二张图边界缺失' if self.fail_second else '完整'}
        f2=dict(FIGURE,source='Figure 2');work=self.root/'partial';work.mkdir()
        prepare_figures(TwoFigures(True),self.pdf,[FIGURE,f2],work)
        self.assertEqual(len(list((work/'checkpoints').glob('vision-*.json'))),1)
        resumed=self.root/'resumed';resumed.mkdir()
        shutil.copytree(work/'checkpoints',resumed/'checkpoints')
        model=TwoFigures(False)
        prepare_figures(model,self.pdf,[FIGURE,f2],resumed)
        self.assertEqual(len(model.images),2)

    def test_text_only_ambiguity_fails_but_manual_plan_works(self):
        pdf=self.vector_pdf();work=self.root/'work';work.mkdir()
        with self.assertRaisesRegex(ValueError,'原图需要视觉定位'):
            prepare_figures(FakeProvider(),pdf,[FIGURE],work,vision=False)
        work2=self.root/'manual';work2.mkdir()
        supplied={'figures':[{'source':'Figure 1','pdf_page':1,'clip':[20,290,550,505]}]}
        path,figures,warnings=prepare_figures(FakeProvider(),pdf,[FIGURE],work2,supplied,vision=False)
        self.assertTrue(path.exists());self.assertTrue(warnings)
        self.assertEqual(figures[0]['caption_pdf_page'],1)

    def test_manual_plan_cannot_use_another_pdf(self):
        work=self.root/'work';work.mkdir()
        with self.assertRaisesRegex(ValueError,'哈希'):
            prepare_figures(FakeProvider(),self.pdf,[FIGURE],work,{'source_pdf_sha256':'wrong','figures':[]},False)

    def test_visual_format_failure_uses_original_pages_with_bounded_calls(self):
        class Broken(VisionProvider):
            def complete_json_with_images(self,system,user,images):
                self.images.append(images)
                return {'unexpected':'wrong schema'}
        model=Broken();work=self.root/'broken';work.mkdir()
        _,_,warnings=prepare_figures(model,self.pdf,[FIGURE],work)
        self.assertEqual(len(model.images),3)
        self.assertTrue(warnings)
        record=json.loads((work/'figure-review.json').read_text(encoding='utf-8'))['figures']['Figure 1']
        self.assertEqual(record['regions'][0]['clip'],[0.0,0.0,595.0,842.0])

    def test_transport_error_is_not_disguised_as_image_fallback(self):
        from paper2html.provider import ProviderError
        class Unavailable(VisionProvider):
            def complete_json_with_images(self,*args): raise ProviderError('HTTP 401')
        work=self.root/'unavailable';work.mkdir()
        with self.assertRaisesRegex(ProviderError,'401'):
            prepare_figures(Unavailable(),self.pdf,[FIGURE],work)

    def test_visual_request_budget_stops_format_retry(self):
        from paper2html.figures import vision_call
        model=VisionProvider()
        def invalid(result): raise ValueError('格式持续错误')
        with self.assertRaisesRegex(ValueError,'上限'):
            vision_call(model,'schema',[],invalid,{'remaining':2})
        self.assertEqual(len(model.images),2)

    def test_heading_rules_cover_all_primary_sections(self):
        labels=['论文一句话','研究背景','模型架构','研究结果','贡献与局限','结语']
        text='# Journal｜标题\n\n'+'\n\n'.join('## '+s for s in labels)+'\n\n### 描述性小节\n\n## 原文信息\n'
        page,_=build(text,self.root/'article.md')
        self.assertEqual(page.count('border-left:4px solid #0396ff;'),6)
        self.assertEqual(page.count('color:#ffa900;'),2)

    def test_directory_process_lock(self):
        with directory_lock(self.root/'.lock'):
            with self.assertRaisesRegex(ValueError,'已被另一个服务使用'):
                with directory_lock(self.root/'.lock'):pass

    def test_vision_transport_uses_configured_endpoint_and_images(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'choices':[{'message':{'content':'{"complete":true}'}}]}).encode()
        with patch('paper2html.provider.build_opener') as opener:
            opener.return_value.open.return_value=Response()
            provider=CompatibleProvider(ModelConfig('http://localhost:1234/v1','vision-model','secret'))
            provider.complete_json_with_images('s','caption',['data:image/png;base64,AA=='])
            request=opener.return_value.open.call_args.args[0];body=json.loads(request.data)
            self.assertEqual(request.full_url,'http://localhost:1234/v1/chat/completions')
            self.assertEqual(body['messages'][1]['content'][1]['type'],'image_url')
            self.assertEqual(body['model'],'vision-model')
