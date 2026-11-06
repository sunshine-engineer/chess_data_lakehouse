"""Live local-model routing probe. Run inside web image; no personal games needed."""
import json
import os
from pathlib import Path
import time
import urllib.request

from chess_lakehouse.personal.assistant import model_route, template_route
from chess_lakehouse.personal.reports import validate_filters
from chess_lakehouse.personal.settings import Settings

questions = [
    ('Summarize my games', 'overview', {}),
    ('Where do I underperform in blitz games?', 'openings', {'speed':'blitz'}),
    ('What is my best opening?', 'openings', {}),
    ('Compare White versus Black', 'color', {}),
    ('Compare rapid versus blitz', 'speed', {}),
    ('How did I score against stronger opponents?', 'opponents', {'opponent_band':'stronger'}),
    ('What is the best move here?', 'review', {}),
    ('Which games should I review?', 'review', {}),
]
if os.environ.get('STUDY_ROUTER') != 'ollama':
    raise SystemExit('Set STUDY_ROUTER=ollama for this live model probe')
rows = []
for question, expected, expected_filters in questions:
    fallback, explicit, _ = template_route(question)
    start = time.monotonic()
    intent, route, inferred = model_route(question, fallback)
    try:
        inferred = validate_filters(inferred, Settings())
    except ValueError:
        intent, route, inferred = fallback, 'ollama-schema-fallback', {}
    if any(explicit.get(k) != v for k, v in inferred.items()):
        intent, route, inferred = fallback, 'ollama-filter-disagreement-fallback', {}
    applied = dict(explicit)
    applied.update({k:v for k,v in inferred.items() if k not in applied})
    rows.append(dict(question=question, expected_intent=expected, intent=intent,
        expected_filters=expected_filters, filters=applied, routing=route,
        seconds=round(time.monotonic()-start,3), passed=intent==expected and applied==expected_filters))
host = os.environ.get('STUDY_LLM_URL', 'http://study-llm:11434').rstrip('/')
with urllib.request.urlopen(host+'/api/tags', timeout=5) as response:
    models = json.load(response)['models']
result = dict(source='Eight fixed routing questions; no personal games or generated ground truth',
    models=[{k:m.get(k) for k in ('name','digest','size')} for m in models], cases=rows,
    passed=sum(r['passed'] for r in rows), total=len(rows),
    accepted_model_routes=sum(r['routing']=='ollama-validated' for r in rows),
    limitation='Routing smoke test only. Facts, references and refusals are evaluated separately; no general language accuracy claim.')
target=Path('/reports/personal-demo/router-evaluation.json')
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
