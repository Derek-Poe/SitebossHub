from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import csv
from io import StringIO

import pytest
from fastapi.testclient import TestClient

from sbh_demo.app import create_app
from sbh_demo.config import DemoConfig, load_config
from sbh_demo.sim import METRICS_ORDER, SimState


class Clock:
    elapsed = 0.0

    def now(self):
        return datetime(2026, 1, 1, 12) + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed


def simulation(**options):
    clock = Clock()
    state = SimState(DemoConfig(**options), clock=clock.now, monotonic=clock.monotonic)
    return state, clock


def test_seeded_simulations_match_with_controlled_clocks():
    first, first_clock = simulation(seed=17)
    second, second_clock = simulation(seed=17)
    for tick in range(1, 21):
        first_clock.elapsed = second_clock.elapsed = tick * 2
        assert first.live_data() == second.live_data()
        assert first.hist_all() == second.hist_all()


def test_ring_matches_live_snapshot_after_many_updates():
    state, clock = simulation(site_count=2, include_man_site=False)
    for tick in range(20):
        clock.elapsed = tick * 2
        live = state.live_data()
        history = state.hist_all()
        assert [r['CellSite'] for r in live] == [r['CellSite'] for r in history]
        for row, ring in zip(live, history):
            assert len(ring) == 1 + 7 * len(METRICS_ORDER)
            for metric in METRICS_ORDER:
                assert ring[f'{metric}7'] == row[metric]


def test_history_time_filter_and_reverse_order():
    state, clock = simulation()
    clock.elapsed = 2
    state.live_data()
    clock.elapsed = 4
    state.live_data()
    rows = state.hist2('1', 'propane', clock.now(), 2)
    assert len(rows) <= 2
    times = [datetime.strptime(r['ChangeTime'], '%m/%d/%Y %I:%M:%S %p') for r in rows]
    assert times == sorted(times, reverse=True)
    assert state.hist2('1', 'temp', datetime(2025, 1, 1), 25) == []


def test_idle_catchup_is_bounded_and_does_not_repeat():
    state, clock = simulation(site_count=1, include_man_site=False)
    before = int(state.live_data()[0]['Uptime'])
    clock.elapsed = 86400
    after = int(state.live_data()[0]['Uptime'])
    assert after - before == 300 * 200
    assert int(state.live_data()[0]['Uptime']) == after


def test_parallel_polling_keeps_bounded_complete_log():
    state, _ = simulation(snmp_call_log_size=30)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(state.poll_site, ['1', '2'] * 20))
    assert all(len(result) == len(METRICS_ORDER) for result in results)
    logs = state.snmp_calls(limit=100)
    assert len(logs) == 30
    assert all(row['primary_oid'].rsplit('.', 1)[-1] == row['fallback_oid'].rsplit('.', 1)[-1] for row in logs)


@pytest.mark.parametrize('path,status', [
    ('/demo/pollSite/unknown', 404), ('/demo/hist/unknown,temp', 404),
    ('/demo/hist/1,missing', 400), ('/demo/hist/1', 400),
    ('/demo/hist2/1,temp,bad,12:00:00,25', 400),
    ('/demo/hist2/1,temp,2026-01-01,12:00:00,0', 400),
    ('/demo/hist2/1,temp,2026-01-01,12:00:00,5001', 400),
    ('/demo/hist2/1,temp,2026-01-01,12:00:00+00:00,25', 400),
    ('/debug/snmp_calls?limit=0', 422), ('/arbitrary-token/all', 404),
])
def test_invalid_requests_do_not_mutate_site_inventory(path, status):
    client = TestClient(create_app())
    before = client.get('/debug/state').json()['site_count']
    assert client.get(path).status_code == status
    assert client.get('/debug/state').json()['site_count'] == before


def test_report_is_downloadable_csv_and_matches_live_columns():
    client = TestClient(create_app())
    response = client.get('/demo/report')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/csv')
    assert '.csv' in response.headers['content-disposition']
    rows = list(csv.DictReader(StringIO(response.text)))
    live = client.get('/demo/all').json()
    assert len(rows) == len(live)
    assert rows[0].keys() == live[0].keys()
    assert all(row['IP'].startswith('192.0.2.') for row in rows)


def test_config_reads_current_environment_and_rejects_unbounded_sizes(monkeypatch):
    monkeypatch.setenv('SBH_DEMO_SITE_COUNT', '3')
    monkeypatch.setenv('SBH_DEMO_INCLUDE_MAN_SITE', 'false')
    assert len(TestClient(create_app()).get('/demo/all').json()) == 3
    monkeypatch.setenv('SBH_DEMO_SITE_COUNT', '201')
    with pytest.raises(ValueError, match='site_count'):
        load_config()


def test_packaged_frontend_uses_local_demo_routes():
    client = TestClient(create_app())
    for route in ('/SitebossHub', '/EP_SitebossHub', '/SitebossHistory'):
        page = client.get(route)
        assert page.status_code == 200
        assert 'synthetic telemetry' in page.text
        assert 'https://' not in page.text and 'http://' not in page.text
        assert 'window.open' not in page.text
        assert 'Export snapshot CSV' in page.text
