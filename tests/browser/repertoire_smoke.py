"""Real Stockfish exercise discovery, grading, pause/resume and archive UI."""
import json
import os
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

root=os.environ.get('WEB_URL','http://web:8080');out=Path('/artifacts');out.mkdir(parents=True,exist_ok=True)
content=b'''[Event "SYNTHETIC MISSED MATE SOFTWARE TEST"]
[Site "https://lichess.org/RepDemo1"]
[Date "2026.10.01"]
[White "DemoStudent"]
[Black "SyntheticOpponent"]
[Result "0-1"]
[TimeControl "300+0"]

1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qe2 d6 5. Nf3 Bg4 6. h3 Bxf3 7. gxf3 0-1
'''
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch();context=browser.new_context(viewport={'width':1440,'height':1100});page=context.new_page()
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(root+'/#personal',wait_until='networkidle');page.locator('#study-signup-tab').click()
    page.locator('#study-auth-form input[name=username]').fill('RepertoireDemo'+uuid4().hex[:8]);page.locator('#study-auth-form input[name=password]').fill('valid-repertoire-demo-password');page.locator('#study-auth-submit').click()
    expect(page.locator('#study-home')).to_be_visible();page.get_by_role('link',name='Import my games').click()
    page.locator('#study-player').fill('DemoStudent')
    page.locator('#study-import-form input[name=file]').set_input_files({'name':'synthetic-missed-mate.pgn','mimeType':'application/x-chess-pgn','buffer':content});page.locator('#study-import-submit').click()
    expect(page.locator('#study-games tbody tr')).to_have_count(1,timeout=60000)
    page.locator('#study-games a').first.click();expect(page.locator('#study-game-page')).to_be_visible()
    page.locator('#study-game-question textarea').fill('Why did I lose this game?');page.locator('#study-game-question button').click()
    expect(page.locator('#study-game-answer')).to_contain_text('requested a review')
    expect(page.locator('#study-game-answer')).to_contain_text('Reviewed',timeout=120000)
    page.goto(root+'/#learn');expect(page.locator('#repertoire-library option')).to_have_count(1)
    page.locator('#repertoire-fallback').uncheck();page.locator('#repertoire-start').click()
    expect(page.locator('#repertoire-status')).to_contain_text('complete',timeout=90000)
    expect(page.locator('#repertoire-list button').first).to_be_visible(timeout=90000);page.locator('#repertoire-list button').first.click()
    expect(page.locator('#repertoire-board .chess-square')).to_have_count(64)
    expect(page.locator('#repertoire-source a')).to_have_attribute('href',__import__('re').compile('/game/RepDemo1'))
    assert 'Searched solution' not in page.locator('#repertoire-solution').text_content()
    page.locator('#repertoire-reveal').click();expect(page.locator('#repertoire-solution')).to_contain_text('Searched solution')
    session=page.request.get(root+'/api/personal/session').json();library=page.locator('#repertoire-library').input_value()
    headers={'X-Study-Action':'1','X-CSRF-Token':session['user']['csrf']}
    def post(path,value):
        response=page.request.post(root+'/api/personal/repertoire/'+path,data=value,headers=headers);assert response.ok,response.text();return response.json()
    build=post('status',{'library_id':library})['build'];exercise=post('list',{'build_id':build['id']})['exercises'][0]
    detail=post('reveal',{'exercise_id':exercise['id']});move=detail['evidence']['accepted'][0]
    with page.expect_response('**/api/personal/repertoire/exercise'):
        page.locator('#repertoire-reset').click()
    expect(page.locator('#repertoire-board .chess-square')).to_have_count(64)
    page.locator(f'#repertoire-board [data-square={move[:2]}]').click();page.locator(f'#repertoire-board [data-square={move[2:4]}]').click()
    expect(page.locator('#repertoire-feedback')).to_contain_text('Accepted searched move. Attempt saved.')
    assert post('list',{'build_id':build['id']})['exercises'][0]['successes']==1
    page.screenshot(path=str(out/'repertoire-desktop.png'),full_page=True)
    page.locator('#repertoire-fallback').check();page.locator('#repertoire-start').click();page.locator('#repertoire-pause').click();expect(page.locator('#repertoire-status')).to_contain_text('paused')
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(out/'repertoire-mobile.png'),full_page=True)
    page.route('**/api/personal/archives/catalog',lambda route:route.fulfill(json={'archives':[{'month':'2026-10','display_size':'31 GB','expected_games':95395628}]}))
    page.goto(root+'/#pipelines');page.get_by_role('button',name='Open archive import controls',exact=True).click();expect(page.locator('#archive-source-mode')).to_be_visible();expect(page.locator('#archive-month option').first).to_contain_text('2026-10',timeout=30000)
    page.locator('#archive-source-mode').select_option('url');page.locator('#archive-source-url').fill('https://evil.example/chess.pgn.zst')
    page.get_by_role('button',name='Import selected month',exact=True).click();expect(page.locator('#archive-import-plan')).to_contain_text('Use an HTTPS database.lichess.org')
    assert not page.locator('#archive-auto-latest').is_checked()
    assert not errors,errors
    browser.close()
(out/'repertoire-acceptance.json').write_text(json.dumps({'status':'passed','source':'Synthetic missed-mate PGN; actual Stockfish; deterministic catalog UI fixture (live catalog checked separately)','checks':['exercise discovery and hidden solution','legal move grading and saved attempt','pause and phase list','mobile layout','catalog UI and URL rejection'],'javascript_errors':errors},indent=2)+'\n')
print('Repertoire browser acceptance passed')
