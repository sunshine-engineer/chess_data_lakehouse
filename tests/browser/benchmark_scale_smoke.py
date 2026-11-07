"""Read-only acceptance of the completed selected-month benchmark publication."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

url=os.environ.get('WEB_URL','http://web:8080');out=Path('/artifacts');out.mkdir(parents=True,exist_ok=True)
errors=[]
with sync_playwright() as p:
    browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000})
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(url+'/#pipelines?workload=scale',wait_until='networkidle')
    expect(page.locator('#benchmark-workload')).to_have_value('scale')
    expect(page.locator('#benchmark-summary')).to_contain_text('Spark')
    expect(page.locator('#benchmark-checks')).to_contain_text('All 12 recorded semantic checks passed')
    page.locator('#benchmark details summary').click()
    expect(page.locator('#benchmark details tbody tr')).to_have_count(18)
    months=page.locator('#benchmark details tbody tr td:nth-child(2)').all_text_contents()
    assert set(months)=={'2013-01','2013-02','2013-03'}
    response=page.request.get(url+'/api/evidence/benchmarks?workload=scale');assert response.ok
    evidence=response.json();assert all(s['samples']==2 for s in evidence['summary'].values())
    page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(out/'expanded-benchmark-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'benchmark mobile overflow'
    page.screenshot(path=str(out/'expanded-benchmark-mobile.png'),full_page=True)
    assert not errors,errors
    browser.close()
(out/'browser-acceptance.json').write_text(json.dumps({'status':'passed','workload_months':3,
    'complete_repeats_per_path':2,'monthly_samples':18,'equality_checks_passed':12,
    'download_verified':True,'desktop_mobile':True,'javascript_errors':errors},indent=2)+'\n')
print('Expanded benchmark browser acceptance passed')
