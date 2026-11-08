"""A player's recent-games workflow, from import to exact-cohort questions and trials."""
import json
import os
import re
from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

root=os.environ.get('WEB_URL','http://web:8080');out=Path('/artifacts');out.mkdir(parents=True,exist_ok=True)
fixture=Path('/fixtures/personal-demo.pgn').read_text();games=[]
# This fixed synthetic fixture has one Event header per game. The application
# remains responsible for full PGN parsing and legality validation.
for record in re.split(r'(?=^\[Event )',fixture,flags=re.M):
    if not record.strip():continue
    index=len(games)
    record=re.sub(r'^\[Date "[^"]+"\]$',f'[Date "2026.10.30"]\n[UTCDate "2026.10.30"]\n[UTCTime "{index:02}:00:00"]',record,count=1,flags=re.M)
    games.append(record.strip())
content=('\n\n'.join(games)+'\n').encode();errors=[];checks=[]

with sync_playwright() as p:
    browser=p.chromium.launch();context=browser.new_context(viewport={'width':1440,'height':1100});page=context.new_page()
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(root+'/#personal',wait_until='networkidle');page.locator('#study-signup-tab').click()
    page.locator('#study-auth-form input[name=username]').fill('RecentDemo'+uuid4().hex[:8]);page.locator('#study-auth-form input[name=password]').fill('a-valid-recent-demo-password');page.locator('#study-auth-submit').click()
    expect(page.locator('#study-home')).to_be_visible();page.get_by_role('link',name='Import my games').click()
    page.locator('#study-import-form input[name=file]').set_input_files({'name':'synthetic-timed-games.pgn','mimeType':'application/x-chess-pgn','buffer':content})
    page.locator('#study-import-submit').click();expect(page.locator('#study-games tbody tr')).to_have_count(10,timeout=60000)
    expect(page.locator('#study-alert')).to_contain_text('Added 24 new games.')
    assert '/games' in page.url
    expect(page.locator('#study-games tbody tr').first).to_contain_text('23:00:00')
    page.screenshot(path=str(out/'recent-table-desktop.png'),full_page=True)
    checks.append('import lands in timestamp-ordered games table with clear added-game message')
    page.locator('#study-recent-shortcuts').get_by_role('button',name='Latest 3',exact=True).click()
    expect(page.locator('#study-games tbody tr')).to_have_count(3)
    page.locator('[data-study-tab=questions]').click()
    page.locator('#study-question textarea').fill('Summarize my last 3 games?');page.locator('#study-question button').click()
    expect(page.locator('#study-answer')).to_contain_text('latest 3 matching games')
    expect(page.locator('#study-answer tbody tr')).to_have_count(3)
    expect(page.locator('#study-answer tbody tr').first).to_contain_text('23:00:00')
    page.screenshot(path=str(out/'recent-answer-desktop.png'),full_page=True)
    page.locator('#study-answer a').filter(has_text='Open editor').first.click()
    expect(page.locator('#study-game-page')).to_be_visible();expect(page.locator('#study-board .chess-square')).to_have_count(64)
    page.locator('#study-game-drawer summary').click();expect(page.locator('#study-game-switcher tbody tr')).to_have_count(3)
    current=page.locator('#study-game-id').text_content();page.locator('#study-next-game').click();expect(page.locator('#study-game-id')).not_to_have_text(current)
    page.locator('#study-game-prompts').get_by_role('button',name='Summarize this game',exact=True).click();page.locator('#study-game-question button').click()
    expect(page.locator('#study-game-answer')).to_contain_text('You played')
    expect(page.locator('#study-game-answer tbody tr')).to_have_count(1)
    checks.append('last-three answer lists its exact games; editor switches within that selection and questions stay game-scoped')
    # Start from the actual initial board and explore a correction.
    page.locator('#study-start').click()
    held=[]
    def delay_trial(route):
        if route.request.post_data_json.get('moves'):held.append(route)
        else:route.continue_()
    page.route('**/api/personal/board/position',delay_trial)
    page.locator('#study-board [data-square=e2]').click();page.locator('#study-board [data-square=e4]').click()
    page.wait_for_function('document.querySelector("#study-position-analyze").disabled')
    page.locator('#study-position-profile').select_option('quick')
    assert held
    held[0].continue_();page.unroute('**/api/personal/board/position',delay_trial)
    expect(page.locator('#study-question-context')).to_contain_text('Current trial: e4')
    page.locator('#study-position-analyze').click()
    expect(page.locator('#study-position-engine')).to_contain_text('Position search: complete',timeout=60000)
    page.locator('#study-game-question textarea').fill('What is the best move here?');page.locator('#study-game-question button').click()
    expect(page.locator('#study-game-answer')).to_contain_text('For this trial position, Stockfish suggests')
    page.locator('#study-trial-reset').click()
    expect(page.locator('#study-game-answer')).to_contain_text('For this trial position, Stockfish suggests')
    expect(page.locator('#study-position-engine')).to_contain_text('Saved result for an earlier position')
    page.locator('#study-position-engine').get_by_role('button',name='Replay line',exact=True).click()
    expect(page.locator('#study-position-engine')).to_contain_text('Position search: complete')
    page.locator('#study-position-engine').get_by_role('button',name='Next →',exact=True).click()
    expect(page.locator('#study-position-label')).to_contain_text('ENGINE VARIATION')
    page.locator('#study-position-engine').get_by_role('button',name='Return to evaluated position',exact=True).click()
    expect(page.locator('#study-trial-line')).to_contain_text('e4')
    page.locator('#study-trial-reset').click()
    page.locator('#study-note textarea').fill('Synthetic correction: develop and compare the reply.');page.locator('#study-note button').click()
    expect(page.locator('#study-alert')).to_contain_text('Practice note saved')
    page.screenshot(path=str(out/'recent-editor-desktop.png'),full_page=True)
    checks.append('legal correction, exact-position engine question retained with original context, and practice note')
    page.locator('#study-game-back').click();expect(page.locator('#study-games tbody tr')).to_have_count(3)
    expect(page.locator('#study-filters select[name=last_games]')).to_have_value('3')
    page.reload(wait_until='networkidle');expect(page.locator('#study-games tbody tr')).to_have_count(3)
    page.locator('#study-reset-filters').click();expect(page.locator('#study-games tbody tr')).to_have_count(10)
    page.locator('#study-filters select[name=result]').select_option('loss');page.locator('#study-filters input[name=opponent]').fill('Synthetic');page.locator('#study-filters button[type=submit]').click()
    expect(page.locator('#study-games tbody tr')).to_have_count(10)
    expect(page.locator('#study-games tbody tr td:nth-child(4)')).to_have_text(['loss']*10)
    page.locator('#study-next').click();expect(page.locator('#study-page-label')).to_contain_text('11')
    page.locator('#study-games a').first.click();expect(page.locator('#study-game-page')).to_be_visible();page.locator('#study-game-back').click()
    expect(page.locator('#study-games-section')).to_be_visible()
    expect(page.locator('#study-page-label')).to_contain_text('11')
    expect(page.locator('#study-filters select[name=result]')).to_have_value('loss')
    checks.append('recent selection, result/opponent filters and page survive editor navigation and reload')
    page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(out/'recent-table-mobile.png'),full_page=True)
    page.locator('#study-games a').first.click();expect(page.locator('#study-game-page')).to_have_attribute('aria-busy','false')
    page.locator('#study-game-question textarea').fill('Summarize this game');page.locator('#study-game-question button').click()
    expect(page.locator('#study-game-answer tbody tr')).to_have_count(1)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(out/'recent-editor-mobile.png'),full_page=True)
    assert not errors,errors
    browser.close()
(out/'recent-acceptance.json').write_text(json.dumps(dict(status='passed',source='Synthetic timed DemoStudent PGNs; no real player data',checks=checks,desktop_mobile=True,javascript_errors=errors),indent=2)+'\n')
print('Recent-game browser acceptance passed:',len(checks),'scenario groups')
