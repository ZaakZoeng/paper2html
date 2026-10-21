#!/usr/bin/env python3
"""Check HTML structure, safe markup and text/link preservation against source MD."""
import argparse
import hashlib
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from .figure_format import find_figures

VOID={'br','hr','img','meta','link','input','wbr','source'}


class Inspect(HTMLParser):
    def __init__(self, article_only=False, strict=False):
        super().__init__(convert_charrefs=True)
        self.stack=[];self.text=[];self.links=[];self.images=0;self.headings=[];self.heading=None
        self.article_only=article_only;self.active=not article_only;self.root=None;self.attrs=None;self.strict=strict
        self.figures=[];self.image_alts=[]

    def handle_starttag(self,tag,attrs):
        d=dict(attrs)
        if self.strict:
            if tag in {'script','iframe','object','embed','form','style','link','base','svg','math'}: raise ValueError('Disallowed element: '+tag)
            if any(k.lower().startswith('on') for k in d): raise ValueError('Event handlers are not allowed')
            if re.search(r'url\s*\(|expression\s*\(',d.get('style',''),re.I): raise ValueError('Unsafe inline style')
            if tag=='a' and urlsplit(d.get('href','')).scheme.lower() not in ('','http','https','mailto'): raise ValueError('Unsafe link')
            if tag=='img' and not (re.match(r'^data:image/(png|jpeg|gif|webp);base64,',d.get('src','')) or urlsplit(d.get('src','')).scheme.lower() in ('http','https')): raise ValueError('Unsafe image source')
        if d.get('id')=='read4md-article':
            if self.attrs is not None: raise ValueError('Duplicate article root')
            self.attrs=d;self.active=True;self.root=len(self.stack)
        if self.active:
            if tag=='a': self.links.append(d.get('href',''))
            if tag=='img': self.images+=1;self.image_alts.append(d.get('alt',''))
            if tag in {'h1','h2','h3','h4','h5','h6'}: self.heading=[tag,[]]
            if 'data-read4md-figure' in d:self.figures.append(d['data-read4md-figure'])
        if tag not in VOID:self.stack.append(tag)

    def handle_endtag(self,tag):
        if tag in VOID:return
        if not self.stack or self.stack[-1]!=tag: raise ValueError('Unbalanced HTML: '+tag)
        self.stack.pop()
        if self.active and self.heading and tag==self.heading[0]:
            self.headings.append((tag,''.join(self.heading[1])));self.heading=None
        if self.article_only and self.root is not None and len(self.stack)==self.root:self.active=False

    def handle_data(self,data):
        if self.active:
            self.text.append(data)
            if self.heading:self.heading[1].append(data)


def norm(value):return re.sub(r'\s+','',value)


def validate_html(document,markdown,fragment=False):
    from .render_html import parser
    md=parser();tokens=md.parse(markdown)
    if fragment:
        tokens=[t for i,t in enumerate(tokens) if not (t.type in ('heading_open','heading_close') and t.tag=='h1') and not (t.type=='inline' and i>0 and tokens[i-1].type=='heading_open' and tokens[i-1].tag=='h1')]
    expected=Inspect();expected.feed(md.renderer.render(tokens,md.options,{}))
    actual=Inspect(article_only=True,strict=True);actual.feed(document)
    if actual.stack:raise ValueError('Unclosed HTML tags')
    if not actual.attrs:raise ValueError('Missing article root')
    digest=hashlib.sha256(markdown.encode()).hexdigest()
    if actual.attrs.get('data-source-sha256')!=digest:raise ValueError('Source Markdown hash differs')
    if actual.attrs.get('data-omit-title')!=str(fragment).lower():raise ValueError('Fragment mode differs')
    if norm(''.join(actual.text))!=norm(''.join(expected.text)):raise ValueError('HTML changed or omitted Markdown text')
    if [(t,norm(s)) for t,s in actual.headings]!=[(t,norm(s)) for t,s in expected.headings]:raise ValueError('Heading structure differs')
    if Counter(actual.links)!=Counter(expected.links):raise ValueError('Links changed or omitted')
    entries = find_figures(markdown)
    figures = len(entries)
    if any(not f['legacy'] for f in entries) and not any(f['legacy'] for f in entries):
        if actual.images != expected.images or [norm(a) for a in actual.image_alts] != [norm(a) for a in expected.image_alts]:
            raise ValueError('Markdown image inventory changed or omitted')
    if len(actual.figures)!=figures:raise ValueError('Figure blocks changed or omitted')
    return {'text_preserved':True,'links_preserved':True,'headings':len(actual.headings),'figure_blocks':figures,'images':actual.images}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('html',type=Path);ap.add_argument('--markdown',required=True,type=Path);ap.add_argument('--fragment',action='store_true')
    a=ap.parse_args()
    print(json.dumps(validate_html(a.html.read_text(encoding='utf-8'),a.markdown.read_text(encoding='utf-8'),a.fragment),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
