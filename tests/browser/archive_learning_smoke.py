"""Read-only live archive/teaching acceptance; no account or private mutations."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

url=os.environ.get('WEB_URL','http://web:8080');out=Path('/artifacts');out.mkdir(parents=True,exist_ok=True)
expected=int(os.environ.get('EXPECT_ARCHIVE_ALL','403928'));sample=int(os.environ.get('EXPECT_TEACHING','1800'))
errors=[];checks=[]
with sync_playwright() as p:
    browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(url+'/#overview?engine=python_mp&month=all',wait_until='networkidle')
    expect(page.locator('#metrics .metric strong').first).to_have_text(f'{expected:,}')
    expect(page.locator('#archive-scope-detail')).to_contain_text('3 months')
    assert set(page.locator('#month option').evaluate_all('(nodes)=>nodes.map(n=>n.value)'))=={'all','2013-01','2013-02','2013-03'}
    page.locator('[data-view=explorer]').click();expect(page.locator('#game-count')).to_contain_text(f'{expected:,}')
    expect(page.locator('#games tbody tr')).to_have_count(25);page.locator('#next').click()
    expect(page.locator('#page-label')).to_contain_text('26–50')
    checks.append('403928-game cumulative view and cursor pagination over three actual publications')
    page.locator('[data-view=learn]').click();expect(page.locator('#lesson-menu button')).to_have_count(7)
    expect(page.locator('#learn-source-note')).to_contain_text('2013-01: 600')
    expect(page.locator('#learn-source-note')).to_contain_text('2013-02: 600')
    expect(page.locator('#learn-source-note')).to_contain_text('2013-03: 600')
    expect(page.locator('#learn-example')).to_contain_text('Archive game')
    data=page.request.get(url+'/api/learn/examples').json();assert data['returned_games']==sample and not data['truncated']
    for lesson in ('opening-development','middlegame-plan','endgame-mate'):
        page.locator('[data-lesson='+lesson+']').click();expect(page.locator('#learn-example')).to_contain_text('Archive game')
        expect(page.locator('#learn-example .chess-square')).to_have_count(64)
        page.locator('#learn-example').get_by_role('button',name='Reveal the recorded move').click()
        expect(page.locator('#learn-example')).to_contain_text('Recorded:')
    page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(out/'historical-lessons-desktop.png'),full_page=True)
    checks.append('1800 real historical teaching games, ISO month labels, all three phases and recorded-move replay')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'learning mobile overflow'
    page.screenshot(path=str(out/'historical-lessons-mobile.png'),full_page=True)
    assert not errors,errors
    browser.close()
(out/'acceptance.json').write_text(json.dumps({'status':'passed','archive_games':expected,'teaching_games':sample,
    'checks':checks,'desktop_mobile':True,'private_mutations':False,'javascript_errors':errors},indent=2)+'\n')
print('Live archive/learning browser acceptance passed')
