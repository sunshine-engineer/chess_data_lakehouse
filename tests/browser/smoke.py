"""Desktop/mobile interaction acceptance against the real Docker web service."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

url=os.environ.get('WEB_URL','http://web:8080')
expected_games=int(os.environ.get('EXPECT_GAMES','121332'))
expect_models=os.environ.get('EXPECT_MODELS','1')=='1'
artifacts=Path(os.environ.get('BROWSER_ARTIFACTS','/artifacts'));artifacts.mkdir(parents=True,exist_ok=True)
errors=[];failed=[]
with sync_playwright() as p:
    browser=p.chromium.launch()
    page=browser.new_page(viewport={'width':1536,'height':1050},device_scale_factor=1)
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('response',lambda response:failed.append((response.status,response.url)) if response.status>=400 and '/api/' in response.url else None)
    page.goto(url+'/#overview',wait_until='networkidle')
    expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text(f'{expected_games:,}')
    page.screenshot(path=str(artifacts/'overview-desktop.png'),full_page=True)
    if expected_games:
        page.select_option('select[name=speed]','blitz');page.get_by_role('button',name='Apply filters').click()
        expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text('46,284')
        page.fill('input[name=min_rating]','2000')
        page.get_by_role('button',name='Apply filters').click()
        filtered=page.request.get(f'{url}/api/overview?speed=blitz&min_rating=2000').json()['summary']['games']
        assert 0<filtered<46284
        expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text(f'{filtered:,}')
        page.fill('input[name=min_rating]','0');page.get_by_role('button',name='Apply filters').click()
        expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text('46,284')
    page.locator('[data-view=explorer]').click()
    if expected_games:
        expect(page.locator('#games tbody tr')).to_have_count(25)
        first=page.locator('#games tbody tr').first.locator('td').first.inner_text()
        page.locator('#next').click();expect(page.locator('#page-label')).to_have_text('Rows 26–50')
        assert first!=page.locator('#games tbody tr').first.locator('td').first.inner_text()
    else:
        expect(page.locator('#page-label')).to_have_text('No matching rows')
    page.locator('[data-view=pipelines]').click()
    expect(page.locator('#benchmark tbody tr')).to_have_count(3)
    expect(page.locator('#benchmark')).to_contain_text('12/12')
    page.screenshot(path=str(artifacts/'pipeline-benchmarks.png'),full_page=True)
    page.locator('[data-view=models]').click()
    if expect_models:
        expect(page.locator('#model-results')).to_contain_text('Current publication',timeout=30000)
        page.locator('#prediction button').click()
        expect(page.locator('#prediction-result')).to_contain_text('Estimated game length',timeout=30000)
        expect(page.locator('#prediction-result')).to_contain_text('White win')
    else:
        expect(page.locator('#model-results')).to_contain_text('Train the documented models')
        expect(page.locator('#prediction button')).to_be_disabled()
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(artifacts/'ml-laboratory.png'),full_page=True)
    page.locator('[data-view=studies]').click()
    for name in ('outcome','length','clusters'):
        response=page.request.get(f'{url}/case-studies/{name}')
        assert response.status==200 and 'Case study:' in response.text()
    page.locator('[data-view=overview]').click()
    page.select_option('select[name=speed]','');page.get_by_role('button',name='Apply filters').click()
    expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text(f'{expected_games:,}')
    for engine in ('python_mp','spark','python'):
        page.select_option('select[name=engine]',engine)
        with page.expect_response(lambda response:'/api/overview?' in response.url and f'engine={engine}' in response.url):
            page.get_by_role('button',name='Apply filters').click()
        expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text(f'{expected_games:,}')
    # Preserve an explicitly requested empty month even when others are published.
    page.evaluate("const option=new Option('2099-12','2099-12');document.querySelector('#month').add(option);document.querySelector('#month').value='2099-12'")
    page.get_by_role('button',name='Apply filters').click()
    expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text('0')
    expect(page.locator('#month')).to_have_value('2099-12')
    page.locator('[data-view=explorer]').click()
    expect(page.locator('#page-label')).to_have_text('No matching rows')
    # A fresh page restores the default even if January has never been published.
    page.goto(url+'/#overview',wait_until='networkidle')
    # Hash navigation is same-document navigation; force a genuinely fresh load.
    page.reload(wait_until='networkidle')
    expect(page.locator('#metrics .metric').first.locator('strong')).to_have_text(f'{expected_games:,}')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile horizontal overflow'
    page.screenshot(path=str(artifacts/'overview-mobile.png'),full_page=True)
    assert not errors,errors
    assert not failed,failed
    browser.close()
(artifacts/'acceptance.json').write_text(json.dumps({'status':'passed','source_games':expected_games,
    'models_available':expect_models,'desktop_and_mobile':True,'filters':True,'pagination':bool(expected_games),
    'pipeline_evidence':True,'case_studies':3,'javascript_errors':errors,'api_failures':failed},indent=2)+'\n')
print('Browser acceptance passed: desktop, mobile, filters, exploration, benchmarks, models, case studies')
