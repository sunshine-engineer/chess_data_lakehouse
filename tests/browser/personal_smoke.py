"""Synthetic account/library browser acceptance with an explicit offline API transport."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from playwright.sync_api import sync_playwright,expect

url=os.environ.get('WEB_URL','http://web:8080')
artifacts=Path('/artifacts');artifacts.mkdir(parents=True,exist_ok=True)
fixture=Path('/fixtures/personal-demo.pgn').read_bytes()
errors=[];checks=[]
suffix=uuid4().hex[:6]

with sync_playwright() as p:
    browser=p.chromium.launch();context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page()
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url+'/#personal',wait_until='networkidle')
    expect(page.locator('#study-auth')).to_be_visible()
    assert page.locator('#study-month').count()==0
    assert page.locator('input[name=approved]').count()==0
    page.screenshot(path=str(artifacts/'workspace-welcome.png'),full_page=True)
    page.locator('#study-signup-tab').click()
    page.locator('#study-auth-form input[name=username]').fill('DemoBrowser'+suffix)
    page.locator('#study-auth-form input[name=password]').fill('a-valid-browser-password')
    page.locator('#study-auth-submit').click()
    expect(page.locator('#study-home')).to_be_visible()
    page.get_by_role('link',name='Import my games').click()
    expect(page.locator('#study-import-page')).to_be_visible()
    assert page.locator('#study-player').get_attribute('readonly') is None
    assert page.locator('#study-player').input_value()==''
    page.locator('#study-import-form input[name=file]').set_input_files('/fixtures/personal-demo.pgn')
    page.locator('#study-import-submit').click()
    expect(page.locator('#study-metrics .metric strong').first).to_have_text('24',timeout=60000)
    assert re.search(r'#personal/[a-f0-9-]+/overview',page.url),page.url
    assert page.locator('#study-import-page').is_hidden()
    expect(page.locator('#study-analysis-title')).to_have_text('DemoStudent')
    assert 1<=page.locator('#study-findings .study-finding').count()<=3
    page.screenshot(path=str(artifacts/'workspace-focus-desktop.png'),full_page=True)
    checks.append('manual PGN auto-detection and analysis redirect')
    # A finding links to an actual game rather than an ungrounded tactical claim.
    page.locator('#study-findings a').first.click()
    expect(page.locator('#study-game-page')).to_be_visible()
    expect(page.locator('#study-board .chess-square')).to_have_count(64)
    assert '/game/' in page.url
    # Keep the browser in the pending state long enough to exercise automatic
    # polling, even when the real short Stockfish job finishes immediately.
    polls=[]
    def pending_review(route):
        response=route.fetch();value=response.json()
        for job in value.get('analysis_jobs',[]):job['status']='running'
        polls.append(True);route.fulfill(response=response,json=value)
    page.route('**/api/personal/games/*',pending_review)
    page.locator('#study-analyze').click()
    page.locator('#study-note textarea').fill('Unsaved draft must survive engine polling.')
    page.locator('#study-flip').click()
    first_square=page.locator('#study-board .chess-square small').first.text_content()
    deadline=time.monotonic()+15
    while len(polls)<2 and time.monotonic()<deadline:page.wait_for_timeout(250)
    assert len(polls)>=2
    expect(page.locator('#study-note textarea')).to_have_value('Unsaved draft must survive engine polling.')
    assert page.locator('#study-board .chess-square small').first.text_content()==first_square
    page.unroute('**/api/personal/games/*',pending_review)
    expect(page.locator('#study-engine')).to_contain_text('Allows checkmate in one',timeout=60000)
    page.locator('#study-engine button').filter(has_text='Opponent reply 1: Qh4#').first.click()
    expect(page.locator('#study-position-label')).to_contain_text('ENGINE VARIATION')
    page.locator('#study-engine button').filter(has_text='Return to played game').first.click()
    page.locator('#study-note textarea').fill('Synthetic practice: inspect forcing replies before pushing a pawn.')
    page.locator('#study-note select').select_option('practiced');page.locator('#study-note button').click()
    expect(page.locator('#study-alert')).to_contain_text('Practice note saved')
    page.locator('#study-game-question textarea').fill('What is the best move here?')
    page.locator('#study-game-question button').click()
    expect(page.locator('#study-game-answer')).to_contain_text('Stockfish')
    page.locator('.study-replay').screenshot(path=str(artifacts/'workspace-board.png'))
    page.locator('#study-game-back').click()
    expect(page.locator('#study-games tbody tr')).to_have_count(10)
    page.locator('#study-next').click();expect(page.locator('#study-page-label')).to_have_text('Games 11–20')
    checks.append('deep game link, board, engine variations, evidence question, notes and pagination')
    page.locator('[data-study-tab=questions]').click()
    page.locator('#study-question textarea').fill('Summarize my games')
    page.locator('#study-question button').click()
    expect(page.locator('#study-answer')).to_contain_text('24 matching games')
    page.locator('#study-question textarea').fill('What should I study next?')
    page.locator('#study-question button').click()
    expect(page.locator('#study-answer')).to_contain_text('Next:')
    page.locator('#study-question textarea').fill('Will my rating reach 2000 next month?')
    page.locator('#study-question button').click()
    expect(page.locator('#study-answer')).to_contain_text('cannot predict')
    checks.append('grounded counts, practice plan and unsupported prediction refusal')
    page.locator('[data-study-tab=saved]').click()
    page.locator('#study-save input').fill('Synthetic original collection')
    page.locator('#study-save button').click()
    expect(page.locator('#study-saved-list')).to_contain_text('Synthetic original collection')
    for format in ('parquet','pgn','manifest','report'):
        href=page.locator('#study-current-exports a').filter(has_text={'parquet':'Download data','pgn':'Download games','manifest':'Data dictionary','report':'Download study report'}[format]).get_attribute('href')
        response=page.request.get(url+href);assert response.ok,response.text()
        data=response.body();assert hashlib.sha256(data).hexdigest()==response.headers['x-content-sha256']
        if format=='parquet':assert data[:4]==b'PAR1' and data[-4:]==b'PAR1'
    checks.append('version-pinned saved study and four checksummed export formats')
    # A different month is accepted without changing account or an approval field.
    page.get_by_role('link',name='Import games',exact=True).click()
    page.locator('#study-import-form input[name=file]').set_input_files({'name':'synthetic-september.pgn','mimeType':'application/x-chess-pgn',
        'buffer':fixture.replace(b'2026.10.',b'2026.09.').replace(b'lichess.org/demo',b'lichess.org/othr')})
    page.locator('#study-import-submit').click()
    expect(page.locator('#study-metrics .metric strong').first).to_have_text('48',timeout=60000)
    library=page.url.split('#personal/')[1].split('/')[0]
    page.locator('#study-period').select_option('custom')
    page.locator('#study-filters input[name=from_date]').fill('2026-09-01')
    page.locator('#study-filters input[name=to_date]').fill('2026-09-30')
    page.locator('#study-filters button').click()
    expect(page.locator('#study-metrics .metric strong').first).to_have_text('24')
    page.locator('#study-period').select_option('all');page.locator('#study-filters button').click()
    expect(page.locator('#study-metrics .metric strong').first).to_have_text('48')
    checks.append('different month import and analytical date range')
    # Full browser/API flow uses the explicit QA server's synthetic HTTP transport.
    page.get_by_role('link',name='Import games',exact=True).click();page.locator('#study-source-api').click()
    page.locator('#study-import-form input[name=url]').fill('https://lichess.org/api/games/user/DemoStudent?tags=true&clocks=true&evals=true&opening=true&literate=true')
    page.locator('#study-import-form input[name=url]').blur()
    expect(page.locator('#study-api-preview')).to_contain_text('all available')
    page.locator('#study-import-submit').click()
    expect(page.locator('#study-metrics .metric strong').first).to_have_text('48',timeout=60000)
    # Identical source games reuse payloads and do not publish another snapshot.
    session=page.request.get(url+'/api/personal/session').json()
    headers={'X-Study-Action':'1','X-CSRF-Token':session['user']['csrf']}
    versions=page.request.get(url+'/api/personal/libraries/'+library+'/versions').json()['versions']
    assert len(versions)==2
    checks.append('Lichess link import with no max option and duplicate snapshot reuse (offline transport)')
    page.get_by_role('link',name='Import games',exact=True).click();page.locator('#study-import-mode').select_option('replace')
    expect(page.locator('#study-mode-help')).to_contain_text('earlier saved versions')
    page.locator('#study-import-submit').click()
    expect(page.locator('#study-metrics .metric strong').first).to_have_text('24',timeout=60000)
    page.locator('[data-study-tab=saved]').click()
    saved=page.request.get(url+'/api/personal/studies').json()['studies'][0]
    response=page.request.get(url+'/api/personal/export?study_id='+saved['id']+'&format=manifest')
    assert response.json()['version']==1 and response.json()['rows']==24
    checks.append('clear replacement semantics and preserved old saved collection')
    page.locator('[data-study-tab=overview]').click()
    expect(page.locator('#study-findings')).to_be_visible()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'mobile overflow'
    page.screenshot(path=str(artifacts/'workspace-focus-mobile.png'),full_page=True)
    # A second account cannot read the first account's game collection or export.
    second=browser.new_context(viewport={'width':390,'height':844});other=second.new_page()
    other.goto(url+'/#personal',wait_until='networkidle');other.locator('#study-signup-tab').click()
    other.locator('#study-auth-form input[name=username]').fill('OtherBrowser'+suffix)
    other.locator('#study-auth-form input[name=password]').fill('a-valid-browser-password')
    other.locator('#study-auth-submit').click();expect(other.locator('#study-home')).to_be_visible()
    assert other.request.get(url+'/api/personal/libraries').json()['libraries']==[]
    auth=other.request.get(url+'/api/personal/session').json()['user']
    forbidden=other.request.post(url+'/api/personal/report',headers={'X-Study-Action':'1','X-CSRF-Token':auth['csrf']},data={'library_id':library})
    assert forbidden.status==400
    assert other.request.get(url+'/api/personal/export?study_id='+saved['id']).status==400
    other.goto(url+'/#personal/'+library+'/overview',wait_until='networkidle')
    expect(other.locator('#study-alert')).to_contain_text('not found in your account')
    checks.append('two-account browser isolation and protected deep links/exports')
    assert not errors,errors
    browser.close()

(artifacts/'workspace-acceptance.json').write_text(json.dumps({'status':'passed','source':'Explicitly labelled synthetic DemoStudent PGNs; offline Lichess transport',
    'checks':checks,'desktop_mobile':True,'javascript_errors':errors,'live_lichess_verified':False},indent=2)+'\n')
print('Workspace browser acceptance passed:',len(checks),'scenarios; synthetic HTTP transport only.')
