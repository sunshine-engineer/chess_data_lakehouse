"""Product acceptance against populated or empty Docker archive services."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from playwright.sync_api import sync_playwright,expect,Error

url=os.environ.get('WEB_URL','http://web:8080')
expected_games=int(os.environ.get('EXPECT_GAMES','121332'))
expect_models=os.environ.get('EXPECT_MODELS','1')=='1'
artifacts=Path(os.environ.get('BROWSER_ARTIFACTS','/artifacts'));artifacts.mkdir(parents=True,exist_ok=True)
errors=[];failures=[];checks=[];planned=False
with sync_playwright() as p:
    browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('response',lambda r:failures.append((r.status,r.url)) if r.status>=400 and '/api/' in r.url and not planned else None)
    page.goto(url+'/#overview',wait_until='networkidle')
    expect(page.locator('#metrics .metric strong').first).to_have_text(f'{expected_games:,}')
    expect(page.locator('#archive-context')).to_contain_text('Personal Study')
    page.locator('.skip').focus()
    page.keyboard.press('Tab')
    expect(page.locator('.brand')).to_be_focused()
    page.keyboard.press('Shift+Tab')
    expect(page.locator('.skip')).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.locator('#main')).to_be_focused()
    assert '#overview' in page.url
    page.screenshot(path=str(artifacts/'overview-desktop.png'),full_page=True)
    checks.append('actual archive overview, publication scope and chart data')
    if expected_games:
        page.locator('#openings a').first.click()
        expect(page.locator('#games tbody tr')).to_have_count(25)
        assert 'eco=' in page.url
        page.reload(wait_until='networkidle')
        assert page.locator('#filters input[name=eco]').input_value()
        expect(page.locator('#games tbody tr')).to_have_count(25)
        page.locator('#clear-filters').click()
        expect(page.locator('#game-count')).to_have_text(f'{expected_games:,} matching games')
        first=page.locator('#games tbody tr').first.locator('small').inner_text()
        page.locator('#next').click()
        expect(page.locator('#page-label')).to_contain_text('26–50')
        assert page.locator('#games tbody tr').first.locator('small').inner_text()!=first
        page.locator('#previous').click()
        expect(page.locator('#page-label')).to_contain_text('1–25')
        assert page.locator('#games tbody tr').first.locator('small').inner_text()==first
        page.locator('#game-search input').fill(first);page.locator('#game-search button[type=submit]').click()
        expect(page.locator('#game-count')).to_have_text('1 matching games')
        page.locator('#games a').first.click()
        expect(page.locator('#archive-game-detail')).to_contain_text('Game '+first)
        assert page.locator('#archive-game-detail a').filter(has_text='public Lichess').get_attribute('href')=='https://lichess.org/'+first
        page.screenshot(path=str(artifacts/'game-detail.png'),full_page=True)
        prefill=page.locator('#archive-game-detail a').filter(has_text='pre-game estimate')
        if expect_models and prefill.count():
            source_values=parse_qs(prefill.get_attribute('href').split('?',1)[1])
            prefill.click()
            for key in ('white_rating','black_rating','initial_seconds','increment_seconds'):
                expect(page.locator('#prediction input[name='+key+']')).to_have_value(source_values[key][0])
        checks.append('linked openings, shareable filters, two-way cursor pagination, ID search and game details')
    response=page.request.get(url+'/api/games.csv?export_limit=10')
    assert response.ok
    payload=response.body();assert hashlib.sha256(payload).hexdigest()==response.headers['x-content-sha256']
    assert len(list(csv.DictReader(io.StringIO(payload.decode()))))==min(10,expected_games)
    assert response.headers['x-export-truncated']==str(expected_games>10).lower()
    checks.append('bounded checksummed CSV with explicit truncation')
    if expected_games>10050:
        deep=page.request.get(url+'/api/games?offset=10000&limit=25&sort=id').json()
        following=page.request.get(url+'/api/games?limit=25&sort=id&cursor='+deep['next_cursor']).json()
        assert len(following['rows'])==25 and following['rows'][0]['game_id']>deep['rows'][-1]['game_id']
        assert not {r['game_id'] for r in deep['rows']}&{r['game_id'] for r in following['rows']}
        (artifacts/'deep-pagination.json').write_text(json.dumps({'status':'passed','source_games':expected_games,
            'first_keyset_row_after_legacy_boundary':10026,'returned_rows':25,'keyset_browsing_past_10000_verified':True,
            'private_study_accessed':False},indent=2)+'\n')
        checks.append('HTTP cursor continuation beyond the old 10000-row boundary')
    page.locator('[data-view=pipelines]').click()
    expect(page.locator('#benchmark > .table-wrap tbody tr')).to_have_count(3)
    expect(page.locator('#benchmark-checks')).to_contain_text('All 12')
    expect(page.locator('#benchmark-time .comparison-bar')).to_have_count(3)
    expect(page.locator('#benchmark-memory .comparison-bar')).to_have_count(3)
    report=page.request.get(url+'/api/evidence/benchmarks')
    assert report.ok and report.json()['benchmark_group']
    page.screenshot(path=str(artifacts/'pipeline-benchmarks.png'),full_page=True)
    checks.append('benchmark charts, equality checks, repeat-run details and evidence download')
    page.locator('[data-view=models]').click()
    if expect_models:
        expect(page.locator('#model-status')).to_contain_text('Current publication',timeout=30000)
        page.locator('#prediction button').click()
        expect(page.locator('#prediction-result')).to_contain_text('Estimated game length',timeout=30000)
        expect(page.locator('#prediction-result')).to_contain_text('White win')
        expect(page.locator('#prediction-result')).to_contain_text('comparable archive games')
        page.locator('#prediction-result summary').filter(has_text='simpler baselines').click()
        expect(page.locator('#prediction-result')).to_contain_text('Ratings only')
        page.screenshot(path=str(artifacts/'model-scenario.png'),full_page=True)
        page.locator('#prediction input[name=white_rating]').fill('1700')
        expect(page.locator('#prediction-result')).to_contain_text('Inputs changed')
        assert page.locator('#prediction-result .probabilities').count()==0
        page.locator('#prediction button').click()
        expect(page.locator('#prediction-result')).to_contain_text('Estimated game length',timeout=30000)
        page.goto(url+'/#models?white_rating=2100&black_rating=2000&initial_seconds=600&increment_seconds=5',wait_until='networkidle')
        expect(page.locator('#prediction input[name=white_rating]')).to_have_value('2100')
        expect(page.locator('#prediction-result')).to_contain_text('Inputs changed')
        assert page.locator('#prediction-result .probabilities').count()==0
        page.locator('[data-model-tab=evidence]').click()
        expect(page.locator('#model-results')).to_contain_text('Draw-class recall')
        page.screenshot(path=str(artifacts/'model-evidence.png'),full_page=True)
        page.locator('[data-model-tab=profiles]').click()
        expect(page.locator('#model-clusters .profile-card').first).to_be_visible()
        page.screenshot(path=str(artifacts/'player-profiles.png'),full_page=True)
        checks.append('scenario, comparisons and baselines; typed and linked inputs clear prior estimates; evidence and profiles')
        page.locator('[data-model-tab=scenario]').click()
        expect(page.locator('#prediction button')).to_be_enabled(timeout=30000)
        page.locator('#prediction button').click()
        expect(page.locator('#prediction-result')).to_contain_text('Estimated game length',timeout=30000)
        publication=page.request.get(url+'/api/models').json()
        def stale(route):route.fulfill(status=200,json={**publication,'fresh':False})
        page.route('**/api/models',stale)
        page.locator('#refresh-page').click()
        expect(page.locator('#model-status')).to_contain_text('estimates paused')
        expect(page.locator('#prediction-result')).to_contain_text('Estimates paused')
        assert page.locator('#prediction-result .probabilities').count()==0
        page.locator('[data-model-tab=scenario]').click()
        expect(page.locator('#prediction button')).to_be_disabled()
        page.unroute('**/api/models',stale);page.locator('#refresh-page').click()
        expect(page.locator('#prediction button')).to_be_enabled(timeout=30000)
        def missing_model(route):route.fulfill(status=200,json={'available':False,'message':'Synthetic unavailable publication'})
        page.route('**/api/models',missing_model);page.locator('#refresh-page').click()
        expect(page.locator('#model-status')).to_contain_text('No model publication')
        expect(page.locator('#prediction button')).to_be_disabled()
        page.unroute('**/api/models',missing_model)
        page.locator('[data-view=pipelines]').click();page.locator('[data-view=models]').click()
        expect(page.locator('#prediction button')).to_be_enabled(timeout=30000)
        expect(page.locator('#prediction-result')).to_contain_text('Ready to explore')
        checks.append('missing and stale publications clear and block estimates; returning publications recover correctly')
    else:
        expect(page.locator('#model-status')).to_contain_text('No model publication')
        expect(page.locator('#prediction button')).to_be_disabled()
        checks.append('intentional unavailable-model state without fake estimates')
    page.locator('[data-view=studies]').click()
    expect(page.locator('#case-study-list .story-card')).to_have_count(5)
    for identity in ('personal','pipelines','outcome','length','clusters'):
        response=page.request.get(url+'/case-studies/'+identity)
        assert response.ok and 'Main navigation' in response.text()
        assert '/case-studies/docs/' not in response.text()
    page.screenshot(path=str(artifacts/'case-studies.png'),full_page=True)
    checks.append('five practical case studies with shared navigation and resolved methodology links')
    page.locator('[data-view=overview]').click()
    expect(page.locator('#filters')).to_be_visible()
    page.locator('#clear-filters').click()
    expect(page.locator('#metrics .metric strong').first).to_have_text(f'{expected_games:,}')
    # A failed request hides stale values and offers a working retry.
    planned=True
    def unavailable(route):route.fulfill(status=503,json={'error':'Archive temporarily unavailable; retry.'})
    page.route('**/api/overview?**',unavailable)
    page.locator('[data-view=overview]').click()
    expect(page.locator('#notice')).to_contain_text('temporarily unavailable')
    assert page.locator('#metrics').is_hidden()
    page.unroute('**/api/overview?**',unavailable);page.locator('#retry-page').click()
    expect(page.locator('#metrics .metric strong').first).to_have_text(f'{expected_games:,}')
    planned=False
    checks.append('dependency error hides stale results and recovers through retry')
    # Hold an earlier filter response while a later selection completes.
    held=[]
    def delay(route):
        if 'speed=blitz' in route.request.url:held.append(route)
        else:route.continue_()
    page.route('**/api/overview?**',delay)
    page.select_option('#filters select[name=speed]','blitz');page.locator('#filters button[type=submit]').click()
    for _ in range(50):
        if held:break
        page.wait_for_timeout(100)
    assert held
    page.select_option('#filters select[name=speed]','rapid');page.locator('#filters button[type=submit]').click()
    expected_rapid=page.request.get(url+'/api/overview?speed=rapid').json()
    expect(page.locator('#metrics .metric strong').first).to_have_text(f"{expected_rapid['summary']['games']:,}")
    for route in held:
        try:route.fulfill(status=200,json={'summary':{'games':999999}})
        except Error:pass
    page.unroute('**/api/overview?**',delay);page.wait_for_timeout(200)
    expect(page.locator('#metrics .metric strong').first).to_have_text(f"{expected_rapid['summary']['games']:,}")
    assert page.locator('#filters select[name=speed]').input_value()=='rapid'
    checks.append('superseded filter responses cannot overwrite the active selection')
    page.locator('#clear-filters').click()
    expect(page.locator('#metrics .metric strong').first).to_have_text(f'{expected_games:,}')
    # Deep link an empty date range, then restore it with a genuine page reload.
    page.goto(url+'/#overview?from_date=2099-01-01&to_date=2099-12-31',wait_until='networkidle')
    expect(page.locator('#metrics .metric strong').first).to_have_text('0')
    page.reload(wait_until='networkidle');expect(page.locator('#metrics .metric strong').first).to_have_text('0')
    assert page.locator('#filters input[name=from_date]').input_value()=='2099-01-01'
    page.locator('#clear-filters').click();expect(page.locator('#metrics .metric strong').first).to_have_text(f'{expected_games:,}')
    checks.append('intentional empty selections and reloadable date filters')
    page.set_viewport_size({'width':390,'height':844})
    for view in ('overview','explorer','pipelines','models','studies'):
        page.locator('[data-view='+view+']').click()
        expect(page.locator('#'+view)).to_be_visible()
        expect(page.locator('#page-loading')).to_be_hidden(timeout=30000)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),view+' overflows'
        page.screenshot(path=str(artifacts/(view+'-mobile.png')),full_page=True)
    page.goto(url+'/case-studies/pipelines',wait_until='networkidle')
    expect(page.locator('nav[aria-label="Main navigation"]')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(artifacts/'case-detail-mobile.png'),full_page=True)
    checks.append('all five sections and case reader fit a 390px mobile viewport')
    assert not errors,errors
    assert not failures,failures
    browser.close()
(artifacts/'acceptance.json').write_text(json.dumps({'status':'passed','source_games':expected_games,'models_available':expect_models,
    'desktop_and_mobile':True,'checks':checks,'javascript_errors':errors,'unexpected_api_failures':failures},indent=2)+'\n')
print('Product browser acceptance passed:',len(checks),'scenarios.')
