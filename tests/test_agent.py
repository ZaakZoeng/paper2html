import copy
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import base64

import fitz

from paper2html.agent import Agent, check_anchor, checked_call, chunks, render_html, validate_article
from paper2html.pdf import extract
from paper2html.provider import CompatibleProvider, ModelConfig, ProviderError
from paper2html.server import Application, handler_for
from paper2html.store import Store

META = {"title":"An example paper", "authors":"A. Author", "journal":"Example Journal", "journal_abbr":"Example", "publication_date":"20260101", "doi":"10.1234/example", "short_name":"Example"}
QUOTE = "The experiment used 100 samples and achieved an accuracy of 90 percent."
CAPTION = "Figure 1. Experimental results. (A) Training data. (B) Accuracy on 100 samples."
FIGURE = {"source":"Figure 1", "pdf_page":1, "title":"实验结果", "caption_quote":CAPTION,
          "caption_zh":["实验结果。", "（A）训练数据。", "（B）100 个样本的准确率。"]}
EVIDENCE = {"claim":"研究使用100个样本，准确率为90%。", "pages":[1], "quote":QUOTE, "note":"实验结果"}


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    text = '\n'.join(["An example paper", "A. Author, Example Journal", "Published 1 January 2026. DOI: 10.1234/example", QUOTE,
                     "This study reports a controlled experiment. The authors describe a limited sample.",
                     "Further independent evaluation is needed before generalizing these findings."])
    page.insert_textbox(fitz.Rect(40, 40, 550, 800), text, fontsize=11)
    pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,500,300),False);pix.clear_with(180)
    page.insert_image(fitz.Rect(40,300,550,500),pixmap=pix)
    page.insert_textbox(fitz.Rect(40,550,550,700),CAPTION,fontsize=11)
    doc.save(path)
    doc.close()
    return path.read_bytes()


def article():
    paragraph = "这项研究关注实验条件下的方法表现。作者报告的结果需要结合样本范围和评价条件理解，现有证据不能证明该方法在所有实际场景下均可推广。"
    return {"headline":"实验条件下的方法表现", "lead":[], "sections":[
        {"heading":"论文一句话", "paragraphs":[EVIDENCE["claim"]]},
        {"heading":"研究背景", "paragraphs":[paragraph] * 8},
        {"heading":"研究结果", "paragraphs":[paragraph] * 8, "figures":[copy.deepcopy(FIGURE)]}],
        "conclusion":[paragraph], "reference":"A. Author. An example paper. https://doi.org/10.1234/example"}


class FakeProvider:
    def __init__(self):
        self.usage = {"calls":0, "prompt_tokens":0, "completion_tokens":0}

    def complete_json(self, system, user):
        self.usage["calls"] += 1
        if user.startswith("从以下 PDF 页提取出版信息"):
            return {"metadata":copy.deepcopy(META), "metadata_evidence":[
                {"field":f, "pages":[1], "quote":"Published 1 January 2026. DOI: 10.1234/example"}
                for f in ("title", "authors", "journal", "publication_date", "doi")], "date_basis":"Published"}
        if user.startswith("阅读以下论文片段"):
            return {"evidence":[copy.deepcopy(EVIDENCE)], "figures":[copy.deepcopy(FIGURE)], "warnings":[]}
        if user.startswith("核对该主图"):
            return {"evidence":[], "figures":[copy.deepcopy(FIGURE)], "warnings":[]}
        return {"article":article()}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def new_job(self, job_id):
        self.store.create(job_id, "input.pdf", "fake")
        work = self.root / job_id
        work.mkdir()
        make_pdf(work / "source.pdf")
        return work

    def test_complete_workflow_and_doi_duplicate(self):
        work = self.new_job("a" * 32)
        Agent(self.store, FakeProvider()).run("a" * 32)
        job = self.store.get("a" * 32)
        self.assertEqual(job["status"], "completed", job)
        self.assertEqual(job["usage"]["calls"], 4)
        markdown = (self.root / job["output_dir"] / job["artifacts"]["markdown"]).read_text()
        self.assertTrue(markdown.startswith("# Example｜"))
        self.assertNotIn("配图位置",markdown)
        self.assertIn("![Figure 1](assets/figure-1.png)",markdown)
        self.assertEqual(len(list((self.root/job["output_dir"]).glob("*.html"))),1)
        self.assertTrue((work/"report.zip").is_file())
        bundle = json.loads((work / "evidence.json").read_text())
        self.assertEqual(bundle["evidence"][0]["quote"], QUOTE)
        self.new_job("b" * 32)
        Agent(self.store, FakeProvider()).run("b" * 32)
        self.assertEqual(self.store.get("b" * 32)["existing_job"], "a" * 32)
        self.new_job("c" * 32)
        Agent(self.store, FakeProvider()).run("c" * 32, regenerate=True)
        self.assertEqual(self.store.get("c" * 32)["status"], "completed")

    def test_invented_evidence_fails_and_releases_claim(self):
        self.new_job("a" * 32)
        self.store.claim("hash:test", "a" * 32)
        provider = FakeProvider()
        original = provider.complete_json
        def fabricated(system, user):
            r = original(system, user)
            if user.startswith("阅读以下论文片段"):
                r["evidence"][0]["quote"] = "This fabricated claim does not occur in the PDF."
            return r
        provider.complete_json = fabricated
        Agent(self.store, provider).run("a" * 32)
        self.assertEqual(self.store.get("a" * 32)["status"], "failed")
        self.assertIsNone(self.store.claim("hash:test", "b" * 32))
        self.assertEqual(provider.usage["calls"], 4)  # metadata + 3 bounded attempts

    def test_quote_must_match_cited_page(self):
        with self.assertRaises(ValueError):
            check_anchor({"pages":[2], "quote":QUOTE}, {1:QUOTE, 2:"unrelated"})

    def test_chunking_preserves_oversized_page(self):
        text = "0123456789" * 5000
        parts = list(chunks([{"page":1,"text":text}], limit=1000))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(p[0]["page"] == 1 for p in parts))
        reconstructed = parts[0][0]["text"] + ''.join(p[0]["text"][500:] for p in parts[1:])
        self.assertEqual(reconstructed, text)

    def test_missing_or_duplicate_figures_rejected(self):
        meta = dict(META, one_line_summary="summary")
        data = {"metadata":meta, "evidence":[EVIDENCE], "article":article()}
        data["article"]["sections"][-1]["figures"] = []
        with self.assertRaisesRegex(ValueError, "主图|main figure"):
            validate_article(data, meta, [EVIDENCE], [FIGURE])
        data["article"]["sections"][-1]["figures"] = [FIGURE, FIGURE]
        with self.assertRaisesRegex(ValueError, "主图|main figure"):
            validate_article(data, meta, [EVIDENCE], [FIGURE])

    def test_html_escapes_model_markup(self):
        data = {"metadata":META, "article":article()}
        data["article"]["lead"] = ['<script>alert("x")</script>']
        assets=self.root/'assets';assets.mkdir()
        pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,10,10),False);pix.clear_with(180);pix.save(assets/'figure-1.png')
        rendered = render_html(data,self.root/'example.md',{'Figure 1':{'path':'assets/figure-1.png'}})
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_atomic_duplicate_claims(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i:self.store.claim("same-doi", str(i)), range(8)))
        self.assertEqual(results.count(None), 1)

    def test_restart_marks_interrupted_jobs_failed(self):
        self.new_job("a" * 32)
        self.store.claim("hash:test", "a" * 32)
        self.store.recover()
        self.assertEqual(self.store.get("a" * 32)["status"], "failed")
        self.assertIsNone(self.store.claim("hash:test", "b" * 32))

    def test_scanned_pdf_has_actionable_failure(self):
        job_id = "d" * 32
        self.store.create(job_id, "scan.pdf", "fake")
        work = self.root / job_id
        work.mkdir()
        doc = fitz.open(); doc.new_page(); doc.save(work / "source.pdf"); doc.close()
        Agent(self.store, FakeProvider()).run(job_id)
        self.assertIn("OCR", self.store.get(job_id)["error"])


class ProviderTests(unittest.TestCase):
    def test_http_adapter_and_truncation(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                content = {"choices":[{"finish_reason":"length" if body["model"] == "truncated" else "stop", "message":{"content":"```json\n{\"ok\":true}\n```"}}], "usage":{"prompt_tokens":12,"completion_tokens":4}}
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(content).encode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1"
            cfg = ModelConfig(url, "test", "secret-key", json_mode=True)
            provider = CompatibleProvider(cfg)
            self.assertEqual(provider.complete_json("system", "prompt"), {"ok":True})
            self.assertEqual(provider.usage["prompt_tokens"], 12)
            self.assertEqual(requests[0][0], "/v1/chat/completions")
            self.assertEqual(requests[0][1], "Bearer secret-key")
            self.assertEqual(requests[0][2]["response_format"], {"type":"json_object"})
            self.assertNotIn("secret-key", repr(cfg))
            with self.assertRaisesRegex(ProviderError, "截断"):
                CompatibleProvider(ModelConfig(url, "truncated")).complete_json("s", "u")
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_reject_insecure_remote_endpoint(self):
        with self.assertRaises(ValueError):
            ModelConfig("http://remote.example/v1", "test", "secret")


class HTTPTests(unittest.TestCase):
    def test_upload_poll_download_duplicate_and_csrf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = make_pdf(root / "input.pdf")
            app = Application(root / "data", workers=1)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(app))
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(url + "/") as response:
                    self.assertIn(b"Paper2HTML", response.read())
                with urlopen(url + "/api/config") as response:
                    token = json.load(response)["csrf_token"]
                payload = {"filename":"input.pdf", "pdf_base64":base64.b64encode(pdf).decode(), "config":{"base_url":"https://example.test/v1", "model":"test", "api_key":"TOP_SECRET_KEY"}}
                headers = {"Content-Type":"application/json", "X-Paper2HTML-Token":token}
                with self.assertRaises(HTTPError) as caught:
                    urlopen(Request(url + "/api/jobs", json.dumps(payload).encode(), {"Content-Type":"application/json"}))
                self.assertEqual(caught.exception.code, 403)
                with patch("paper2html.server.CompatibleProvider", return_value=FakeProvider()):
                    with urlopen(Request(url + "/api/jobs", json.dumps(payload).encode(), headers)) as response:
                        job = json.load(response)
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        with urlopen(url + "/api/jobs/" + job["id"]) as response:
                            state = json.load(response)
                        if state["status"] in ("completed", "failed"): break
                        time.sleep(0.02)
                    self.assertEqual(state["status"], "completed", state)
                    with urlopen(url + "/api/jobs/" + job["id"] + "/files/markdown") as response:
                        self.assertIn("attachment", response.headers["Content-Disposition"])
                        self.assertIn("论文一句话", response.read().decode())
                    with urlopen(url + "/api/jobs/" + job["id"] + "/files/html") as response:
                        self.assertIn("data:image/png;base64,",response.read().decode())
                        self.assertIn("img-src 'self' data:",response.headers['Content-Security-Policy'])
                    with urlopen(url + "/api/jobs/" + job["id"] + "/files/bundle") as response:
                        self.assertEqual(response.headers['Content-Type'],'application/zip')
                        import io,zipfile
                        with zipfile.ZipFile(io.BytesIO(response.read())) as z:
                            self.assertTrue(any(n.endswith('assets/figure-1.png') for n in z.namelist()))
                    with urlopen(Request(url + "/api/jobs", json.dumps(payload).encode(), headers)) as response:
                        duplicate = json.load(response)
                    self.assertEqual(duplicate["existing_job"], job["id"])
                for path in (root / "data").rglob("*"):
                    if path.is_file(): self.assertNotIn(b"TOP_SECRET_KEY", path.read_bytes(), str(path))
            finally:
                server.shutdown(); server.server_close(); thread.join(); app.pool.shutdown()


if __name__ == "__main__":
    unittest.main()
