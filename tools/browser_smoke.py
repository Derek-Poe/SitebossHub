"""Exercise the served dashboard and fail on JS errors or external requests.

Run against a local server: python tools/browser_smoke.py [http://127.0.0.1:9747]
"""
import json
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

base = (sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:9747').rstrip('/')
origin = urlsplit(base).netloc
errors, external, failed_responses = [], [], []

with sync_playwright() as playwright:
    browser = playwright.chromium.launch()
    context = browser.new_context(viewport={'width': 1600, 'height': 1000})
    context.set_default_timeout(10000)
    context.on('request', lambda r: external.append(r.url) if urlsplit(r.url).netloc != origin else None)
    context.on('response', lambda r: failed_responses.append([r.url, r.status]) if r.status >= 400 else None)
    for route in ('/SitebossHub', '/EP_SitebossHub'):
        print('Checking ' + route, flush=True)
        page = context.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(base + route)
        page.wait_for_function("document.querySelectorAll('#table_testData td').length > 0")
        page.locator('#in_enableGraph').check()
        page.wait_for_function("document.querySelectorAll('.sparklineCell').length > 0")
        page.locator('#in_enableSyslog').check()
        page.wait_for_function("document.querySelector('#table_syslog').textContent.length > 0")
        page.locator('#in_enableSiteSpecificData').check()
        print('  graphs and alarms loaded; checking details', flush=True)
        page.locator('#table_testData tr').nth(1).locator('td').first.click()
        page.wait_for_function("document.querySelectorAll('#table_siteSpecificData td').length > 0")
        page.locator('#span_siteDataConClose').click()
        page.locator('#in_enableQuickLink').check()
        page.locator('#table_testData tr').nth(1).locator('td').nth(1).click()
        page.wait_for_function("document.querySelectorAll('#table_siteSpecificData td').length > 0")
        page.locator('#span_siteDataConClose').click()
        page.locator('#btn_tableToggle').click()
        print('  details loaded; checking generator runs and export', flush=True)
        page.wait_for_function("document.querySelectorAll('#table_genRun td').length > 0")
        with page.expect_download() as download:
            page.get_by_role('link', name='Export snapshot CSV').click()
        assert download.value.suggested_filename.endswith('.csv')
        page.close()
    page = context.new_page()
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(base + '/SitebossHistory')
    print('Checking history', flush=True)
    page.wait_for_function("document.querySelector('#hist_status').textContent.includes('recorded changes')")
    assert page.locator('#sel_site option').count() == 11
    page.locator('#in_date').fill('2000-01-01')
    page.locator('#btn_submit').click()
    page.wait_for_function("document.querySelector('#hist_status').textContent.includes('No changes')")
    browser.close()

report = {'javascript_errors': errors, 'external_requests': external, 'http_errors': failed_responses,
          'checked': ['both dashboards', 'sparklines', 'syslog', 'site details', 'local device links', 'generator runs', 'CSV download', 'history including empty results']}
print(json.dumps(report, indent=2))
assert not errors and not external and not failed_responses, report
