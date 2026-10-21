"""Local web server or one-shot agent, using the same workflow and API configuration."""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from .server import Application, MAX_PDF
from .locking import directory_lock


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == 'serve' or (argv[0].startswith('-') and argv[0] not in ('-h','--help')):
        from .server import main as serve
        return serve(argv[1:] if argv and argv[0]=='serve' else argv)
    parser=argparse.ArgumentParser(description='Read4MD / Paper2HTML 本地带图论文智能体')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('serve',help='启动本地网页（可直接运行 paper2html）')
    run=sub.add_parser('run',help='从本地PDF直接生成带图报告')
    run.add_argument('pdf',type=Path)
    run.add_argument('--base-url',default=os.environ.get('READ4MD_BASE_URL',''))
    run.add_argument('--model',default=os.environ.get('READ4MD_MODEL',''))
    run.add_argument('--data-dir',type=Path,default=Path('data'))
    run.add_argument('--reports-dir',type=Path,default=Path('reports'))
    run.add_argument('--metadata',type=Path,help='补充出版信息JSON文件')
    run.add_argument('--figure-plan',type=Path,help='已核对原图区域JSON文件')
    run.add_argument('--text-only',action='store_true',help='不发送图片；复杂原图需要figure-plan')
    run.add_argument('--regenerate',action='store_true')
    run.add_argument('--json-mode',action='store_true')
    run.add_argument('--max-tokens',type=int,default=12000)
    run.add_argument('--timeout',type=int,default=180)
    args=parser.parse_args(argv)
    app=None
    try:
        if args.pdf.stat().st_size>MAX_PDF:raise ValueError('PDF不能超过25 MB')
        args.data_dir.mkdir(parents=True,exist_ok=True)
        with directory_lock(args.data_dir/'.server.lock'):
            app=Application(args.data_dir,workers=1,reports_dir=args.reports_dir)
            app.store.recover()
            payload={'filename':args.pdf.name,'pdf_base64':base64.b64encode(args.pdf.read_bytes()).decode(),
                     'config':{'base_url':args.base_url,'model':args.model,'max_tokens':args.max_tokens,
                               'timeout':args.timeout,'vision':not args.text_only,'json_mode':args.json_mode},
                     'regenerate':args.regenerate}
            for option,key in [(args.metadata,'metadata'),(args.figure_plan,'figure_plan')]:
                if option:payload[key]=json.loads(option.read_text(encoding='utf-8'))
            job=app.submit(payload);last=None
            while job['status'] in ('queued','running'):
                if job['stage']!=last:print(job['stage'],flush=True);last=job['stage']
                time.sleep(0.2);job=app.store.get(job['id'])
            print(json.dumps(job,ensure_ascii=False,indent=2))
            if job['status']=='failed':raise SystemExit(1)
    except (ValueError,OSError) as exc:
        parser.exit(1,str(exc)+'\n')
    finally:
        if app:app.pool.shutdown(wait=True)
