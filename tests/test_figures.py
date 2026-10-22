import copy
import json
import tempfile
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

    def test_failed_visual_check_does_not_publish(self):
        store=Store(self.root/'data');job='a'*32;store.create(job,'input.pdf','fake')
        work=store.root/job;work.mkdir();(work/'source.pdf').write_bytes(self.pdf.read_bytes())
        Agent(store,VisionProvider(False)).run(job)
        self.assertEqual(store.get(job)['status'],'failed')
        self.assertFalse(list(store.reports.glob('*/bundle.json')))

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
