"""Curated product stories and safe links into checked-in methodology."""
from pathlib import PurePosixPath
import re
from urllib.parse import urlsplit

import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

CASES={
 'personal':{'title':'A practical game-review workflow','category':'PERSONAL STUDY','document':'product-personal-study.md',
    'question':'How can a player turn their game history into a specific practice session?',
    'result':'Private libraries, linked findings, position replay and version-pinned studies.',
    'action':'Open Personal Study','href':'/#personal'},
 'pipelines':{'title':'Three pipelines, one trusted dataset','category':'DATA ENGINEERING','document':'product-pipelines.md',
    'question':'How do execution time, memory and storage change when the same archive uses Python, parallel Python or Spark?',
    'result':'Repeated measurements and semantic equality checks make the trade-offs inspectable.',
    'action':'Compare pipelines','href':'/#pipelines'},
 'outcome':{'title':'What ratings can tell us','category':'OUTCOME ESTIMATES','document':'ml-outcome.md',
    'question':'Do ratings and the clock improve outcome probabilities over the historical class proportions?',
    'result':'Compare the model with ratings-only and class-prior baselines; inspect the draw-class weakness.',
    'action':'Explore estimates','href':'/#models'},
 'length':{'title':'Estimating game length','category':'GAME DURATION','document':'ml-length.md',
    'question':'How much can we estimate about game length before the first move?',
    'result':'A measured error in full moves and a median baseline keep the estimate in context.',
    'action':'Try a scenario','href':'/#models'},
 'clusters':{'title':'Patterns in observed play','category':'PLAYER PROFILES','document':'ml-clusters.md',
    'question':'Which broad patterns appear among players with enough observed games?',
    'result':'Compare groups by their historical behavior without assigning skill or personality labels.',
    'action':'Inspect the profiles','href':'/#models?tab=profiles'}}

GUIDES={'web-app','personal-study','optimized-pipeline','optimized-validation','web-ml-validation',
    'personal-workspace-validation','product-workspace-validation','product-workspace-plan','case-studies','board-learning-archives','board-learning-archives-validation'}
EVIDENCE={'reports/comparison-v2.json':'/api/evidence/benchmarks',
    'reports/scale/comparison-v2.json':'/api/evidence/benchmarks?workload=scale',
    'reports/ml-evaluation.json':'/api/evidence/models'}
REPOSITORY='https://github.com/sunshine-engineer/chess_data_lakehouse/blob/CXOED/product-workspace/'


class Links(Treeprocessor):
    def run(self,root):
        for link in root.iter('a'):
            href=link.get('href','');parts=urlsplit(href)
            if parts.scheme or parts.netloc or href.startswith('#'):continue
            path=str(PurePosixPath(parts.path))
            while path.startswith('../'):path=path[3:]
            if path in EVIDENCE:target=EVIDENCE[path]
            elif path.endswith('.md') and PurePosixPath(path).stem in GUIDES:target='/guides/'+PurePosixPath(path).stem
            elif path.endswith('.md') and PurePosixPath(path).name in [v['document'] for v in CASES.values()]:
                target='/case-studies/'+next(k for k,v in CASES.items() if v['document']==PurePosixPath(path).name)
            else:target=REPOSITORY+('docs/'+path if '/' not in path and path.endswith('.md') else path)
            link.set('href',target+('#'+parts.fragment if parts.fragment else ''))


class ProductLinks(Extension):
    def extendMarkdown(self,md):md.treeprocessors.register(Links(md),'product_links',5)


def render(source):
    source=re.sub(r'^# [^\n]+\n','',source,count=1)
    return markdown.markdown(source,extensions=['tables','fenced_code','toc',ProductLinks()])
