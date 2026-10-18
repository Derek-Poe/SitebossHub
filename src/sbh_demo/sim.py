from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import deque
from datetime import datetime, timedelta
import json
import random
import re
import time
from functools import wraps
from threading import RLock
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from .config import DemoConfig


def synchronized(method):
    """Keep each request's state transition and returned snapshot atomic."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


def _fmt_dt(dt: datetime) -> str:
    # Match the legacy PowerShell formatting used in logs / CSVs.
    return dt.strftime("%m/%d/%Y %I:%M:%S %p")


def _site_sort_key(site: str) -> Tuple[int, str]:
    m = re.match(r"^(\d+)", site)
    if not m:
        return (10**9, site)
    return (int(m.group(1)), site)


METRICS_ORDER: List[str] = [
    "Temp",
    "Humid",
    "Door",
    "Battery",
    "Propane",
    "Generator",
    "Ping",
    "LightBeacon",
    "LightObstruction",
    "Photocell",
]


TYPE_MAP: Dict[str, str] = {
    "temp": "Temp",
    "humid": "Humid",
    "humidity": "Humid",
    "door": "Door",
    "battery": "Battery",
    "propane": "Propane",
    "generator": "Generator",
    "ping": "Ping",
    "lightbeacon": "LightBeacon",
    "lightobstruction": "LightObstruction",
    "photocell": "Photocell",
}


@dataclass(frozen=True)
class SnmpCall:
    ts: str
    site: str
    sensor: str
    primary_oid: str
    fallback_oid: str
    returned_value: str


@dataclass
class SensorState:
    name: str
    oid: str
    value: str
    last_poll: Optional[datetime] = None


@dataclass
class GenRunState:
    gen_on_time: Optional[datetime] = None
    gen_on_propane: Optional[float] = None
    gen_off_time: Optional[datetime] = None
    gen_off_propane: Optional[float] = None


@dataclass
class SiteState:
    cell_site: str
    ip: str

    # Raw underlying state.
    temp_f: float
    humid_pct: int
    propane_gal: float
    door_closed: bool
    battery_charging: bool
    generator_on: bool
    ping_reachable: bool
    light_beacon_normal: bool
    light_obstruction_normal: bool
    photocell_normal: bool
    uptime_cs: int
    last_updated: datetime

    ping_down_steps: int = 0
    door_open_steps: int = 0

    sensors: List[SensorState] = None  # populated in __post_init__
    gen_run: GenRunState = None  # populated in __post_init__

    # Metric history (for histAll/hist).
    history_ring: Dict[str, Deque[str]] = None  # populated in __post_init__

    # Timeseries history (for hist2).
    history_events: Dict[str, Deque[Tuple[datetime, str]]] = None  # populated in __post_init__

    def __post_init__(self) -> None:
        if self.sensors is None:
            self.sensors = []
        if self.gen_run is None:
            self.gen_run = GenRunState()
        if self.history_ring is None:
            self.history_ring = {}
        if self.history_events is None:
            self.history_events = {}

    def metric_str(self, metric: str) -> str:
        if metric == "Temp":
            return str(int(round(self.temp_f)))
        if metric == "Humid":
            return str(int(self.humid_pct))
        if metric == "Propane":
            # The UI treats this as raw gallons in most sites.
            return str(int(round(self.propane_gal)))
        if metric == "Door":
            return "Door Closed" if self.door_closed else "Door Open"
        if metric == "Battery":
            return "Battery Charging" if self.battery_charging else "Site On Battery Power"
        if metric == "Generator":
            return "Generator ON" if self.generator_on else "Generator OFF"
        if metric == "Ping":
            # Live table expects Unreachable for grey-out logic.
            return "Reachable" if self.ping_reachable else "Unreachable"
        if metric == "LightBeacon":
            return "Normal" if self.light_beacon_normal else "Alarm"
        if metric == "LightObstruction":
            return "Normal" if self.light_obstruction_normal else "Alarm"
        if metric == "Photocell":
            return "Normal" if self.photocell_normal else "Alarm"
        raise KeyError(metric)

    def hist_bool_str(self, metric: str) -> str:
        # histAll and hist (ring buffers) use slightly different normal strings for Ping:
        # sparkline graph treats "Reachable" as 0 (normal).
        if metric == "Ping":
            return "Reachable" if self.ping_reachable else "Unreachable"
        return self.metric_str(metric)

    def live_row(self) -> Dict[str, str]:
        # Key order matters: the UI uses Object.keys(allData[0]) to build headers.
        return {
            "CellSite": self.cell_site,
            "IP": self.ip,
            "Ping": self.metric_str("Ping"),
            "Temp": self.metric_str("Temp"),
            "Humid": self.metric_str("Humid"),
            "Door": self.metric_str("Door"),
            "Battery": self.metric_str("Battery"),
            "Propane": self.metric_str("Propane"),
            "Generator": self.metric_str("Generator"),
            "LightBeacon": self.metric_str("LightBeacon"),
            "LightObstruction": self.metric_str("LightObstruction"),
            "Photocell": self.metric_str("Photocell"),
            "Uptime": str(self.uptime_cs),
            "LastUpdated": _fmt_dt(self.last_updated),
        }

    def hist_all_row(self) -> Dict[str, str]:
        row: Dict[str, str] = {"CellSite": self.cell_site}
        for metric in METRICS_ORDER:
            ring = self.history_ring.get(metric)
            if not ring:
                continue
            values = list(ring)
            for i, v in enumerate(values, start=1):
                row[f"{metric}{i}"] = v
        return row


class SimState:
    def __init__(self, cfg: DemoConfig, *, clock=datetime.now, monotonic=time.monotonic):
        self._lock = RLock()
        self._clock = clock
        self._monotonic = monotonic
        self._cfg = cfg
        self._rng = random.Random(cfg.seed)
        self._snmp_calls: Deque[SnmpCall] = deque(maxlen=cfg.snmp_call_log_size)
        self._sites: Dict[str, SiteState] = {}

        # Step logic.
        self._step_interval_s = 2.0
        self._last_step_monotonic = self._monotonic()

        self._init_sites()

    @property
    def cfg(self) -> DemoConfig:
        return self._cfg

    def _init_sites(self) -> None:
        # Create an initial set of sites. The UI is happier with numeric-ish CellSite values.
        for i in range(1, max(1, self._cfg.site_count) + 1):
            site = str(i)
            self._sites[site] = self._new_site(site)

        if self._cfg.include_man_site:
            # Demonstrate the UI's special-case MAN coloring/logic.
            man_site = "5_MAN"
            if man_site not in self._sites:
                self._sites[man_site] = self._new_site(man_site, seed_bias=99)

    def _new_site(self, site: str, seed_bias: int = 0) -> SiteState:
        # Deterministic-ish but varied per site.
        i = _site_sort_key(site)[0]
        base_temp = 68.0 + (i % 7) + (seed_bias * 0.01)
        base_humid = 35 + (i % 25)
        base_propane = 550.0 + ((i * 13) % 250)
        now = self._clock()

        st = SiteState(
            cell_site=site,
            ip=f"192.0.2.{i}",
            temp_f=base_temp,
            humid_pct=base_humid,
            propane_gal=base_propane,
            door_closed=True,
            battery_charging=True,
            generator_on=False,
            ping_reachable=True,
            light_beacon_normal=True,
            light_obstruction_normal=True,
            photocell_normal=True,
            uptime_cs=int((i * 1234) % (7 * 24 * 3600) * 100),
            last_updated=now,
        )

        # Populate sensors with OIDs that match the legacy indexing trick.
        sensors: List[SensorState] = []
        for idx, metric in enumerate(METRICS_ORDER, start=1):
            oid = f".1.3.6.1.4.1.3052.10.1.1.1.1.10.1.2.{idx}"
            sensors.append(
                SensorState(
                    name=metric,
                    oid=oid,
                    value=st.metric_str(metric) if metric != "Ping" else "Reachable",
                    last_poll=None,
                )
            )
        st.sensors = sensors

        # Seed history.
        st.history_ring = {}
        st.history_events = {}
        for metric in METRICS_ORDER:
            ring: Deque[str] = deque(maxlen=7)
            v = st.hist_bool_str(metric)
            for _ in range(7):
                ring.append(v)
            st.history_ring[metric] = ring
            st.history_events[metric] = deque(maxlen=5000)
            st.history_events[metric].append((now, v))

        # Seed some generator run data so the Gen Runs table isn't empty.
        st.gen_run = GenRunState(
            gen_on_time=now - timedelta(hours=6, minutes=(i % 40)),
            gen_on_propane=st.propane_gal + 2.0,
            gen_off_time=now - timedelta(hours=5, minutes=(i % 35)),
            gen_off_propane=st.propane_gal + 1.5,
        )

        return st

    def ensure_site(self, site: str) -> SiteState:
        return self._sites[site]

    def step(self) -> None:
        now_m = self._monotonic()
        dt = now_m - self._last_step_monotonic
        if dt < self._step_interval_s:
            return
        elapsed_steps = int(dt / self._step_interval_s)
        # Resume after idle without replaying unbounded days of simulated ticks.
        steps = min(elapsed_steps, 300)
        for _ in range(steps):
            self._step_once()
        self._last_step_monotonic += elapsed_steps * self._step_interval_s

    def _step_once(self) -> None:
        now = self._clock()
        for st in self._sites.values():
            # Ping flaps.
            if st.ping_down_steps > 0:
                st.ping_down_steps -= 1
                st.ping_reachable = False
            else:
                # Small chance to go down for a short burst.
                if self._rng.random() < 0.01:
                    st.ping_down_steps = self._rng.randint(2, 6)
                    st.ping_reachable = False
                else:
                    st.ping_reachable = True

            # Door opens briefly.
            if st.door_open_steps > 0:
                st.door_open_steps -= 1
                st.door_closed = False
            else:
                if self._rng.random() < 0.01:
                    st.door_open_steps = self._rng.randint(1, 3)
                    st.door_closed = False
                else:
                    st.door_closed = True

            # Temperature / humidity drift.
            st.temp_f += self._rng.uniform(-0.6, 0.6)
            st.temp_f = max(40.0, min(110.0, st.temp_f))
            st.humid_pct += self._rng.randint(-2, 2)
            st.humid_pct = max(5, min(95, st.humid_pct))

            # Generator events. When ping is down, we sometimes flip to generator power.
            if st.generator_on:
                # chance to turn off
                if self._rng.random() < 0.15:
                    st.generator_on = False
                    st.gen_run.gen_off_time = now
                    st.gen_run.gen_off_propane = st.propane_gal
            else:
                if (not st.ping_reachable and self._rng.random() < 0.35) or self._rng.random() < 0.02:
                    st.generator_on = True
                    st.gen_run.gen_on_time = now
                    st.gen_run.gen_on_propane = st.propane_gal

            # Propane consumption.
            consume = 0.05 + (0.35 if st.generator_on else 0.0)
            st.propane_gal = max(0.0, st.propane_gal - consume)

            # Battery charging status.
            st.battery_charging = st.ping_reachable and (not st.generator_on)

            # Lights mostly normal with rare alarms.
            st.light_beacon_normal = self._rng.random() > 0.005
            st.light_obstruction_normal = self._rng.random() > 0.005
            st.photocell_normal = self._rng.random() > 0.005

            # Uptime (centiseconds).
            st.uptime_cs += int(self._step_interval_s * 100)
            st.last_updated = now

            # Update sensors snapshot.
            for s in st.sensors:
                if s.name == "Ping":
                    s.value = "Reachable" if st.ping_reachable else "Unreachable"
                else:
                    s.value = st.metric_str(s.name)

            # Update history rings and events.
            for metric in METRICS_ORDER:
                v = st.hist_bool_str(metric)
                ring = st.history_ring[metric]
                ring.append(v)
                ev = st.history_events[metric]
                if not ev or ev[-1][1] != v:
                    ev.append((now, v))

    @synchronized
    def live_data(self) -> List[Dict[str, str]]:
        self.step()
        sites = sorted(self._sites.values(), key=lambda s: _site_sort_key(s.cell_site))
        return [s.live_row() for s in sites]

    @synchronized
    def hist_all(self) -> List[Dict[str, str]]:
        self.step()
        sites = sorted(self._sites.values(), key=lambda s: _site_sort_key(s.cell_site))
        return [s.hist_all_row() for s in sites]

    @synchronized
    def hist_single(self, site: str, metric: str) -> Dict[str, str]:
        self.step()
        st = self.ensure_site(site)
        metric_norm = self._normalize_metric(metric)
        ring = list(st.history_ring[metric_norm])
        # Return only metric columns like the original Access query.
        return {f"{metric_norm}{i}": v for i, v in enumerate(ring, start=1)}

    @synchronized
    def hist2(
        self, site: str, metric_type: str, when: Optional[datetime], hist_len: int
    ) -> List[Dict[str, str]]:
        self.step()
        st = self.ensure_site(site)
        metric_norm = self._normalize_metric(metric_type)
        events = list(st.history_events[metric_norm])
        if when is not None:
            events = [e for e in events if e[0] <= when]
        # Newest first to match the legacy SQL ORDER BY [ID] DESC
        events = list(reversed(events))[: max(1, hist_len)]
        out: List[Dict[str, str]] = []
        for ts, val in events:
            out.append(
                {
                    "CellSite": st.cell_site,
                    metric_norm: val,
                    "ChangeTime": _fmt_dt(ts),
                }
            )
        return out

    @synchronized
    def syslog(self, limit: int = 25) -> List[Dict[str, str]]:
        self.step()
        now = self._clock()
        rows: List[Dict[str, str]] = []
        sites = sorted(self._sites.values(), key=lambda s: _site_sort_key(s.cell_site))
        for st in sites:
            if not st.ping_reachable:
                rows.append(
                    {
                        "Date": _fmt_dt(now),
                        "Site": st.cell_site,
                        "Alarm": "Ping",
                        "Severity": "Critical",
                        "Status": "Unreachable",
                    }
                )
            if int(round(st.propane_gal)) <= 120:
                rows.append(
                    {
                        "Date": _fmt_dt(now),
                        "Site": st.cell_site,
                        "Alarm": "Propane Level",
                        "Severity": "Critical",
                        "Status": "Very Low (Reminder)",
                    }
                )
            if not st.battery_charging:
                rows.append(
                    {
                        "Date": _fmt_dt(now),
                        "Site": st.cell_site,
                        "Alarm": "UPS BATT ON DISCHARGE",
                        "Severity": "Info",
                        "Status": "Site On Battery Power",
                    }
                )
            if not st.door_closed:
                rows.append(
                    {
                        "Date": _fmt_dt(now),
                        "Site": st.cell_site,
                        "Alarm": "Door",
                        "Severity": "Warning",
                        "Status": "Door Open",
                    }
                )

        # newest first, like a syslog view
        rows = rows[:limit]
        return rows

    @synchronized
    def gen_runs_payload(self) -> str:
        self.step()
        now = self._clock()
        rows: List[Dict[str, str]] = []
        sites = sorted(self._sites.values(), key=lambda s: _site_sort_key(s.cell_site))
        for st in sites:
            gr = st.gen_run
            on_t = _fmt_dt(gr.gen_on_time) if gr.gen_on_time else "_NP"
            on_p = f"{gr.gen_on_propane:.1f} Gallons" if gr.gen_on_propane is not None else "_NP"
            off_t = _fmt_dt(gr.gen_off_time) if gr.gen_off_time else "_NP"
            off_p = f"{gr.gen_off_propane:.1f} Gallons" if gr.gen_off_propane is not None else "_NP"

            lost: str
            if gr.gen_on_propane is None or gr.gen_off_propane is None:
                lost = "_NP"
            else:
                diff = gr.gen_on_propane - gr.gen_off_propane
                if abs(diff) < 0.1:
                    lost = "<0.1 Gallons"
                else:
                    lost = f"{diff:.3f} Gallons"

            rows.append(
                {
                    "Site": st.cell_site,
                    "GenON_Time": on_t,
                    "GenON_Propane": on_p,
                    "GenOFF_Time": off_t,
                    "GenOFF_Propane": off_p,
                    "PropaneLost": lost,
                }
            )

        payload = json.dumps(rows, separators=(",", ":"))
        return f"{payload}<~~~~~>{_fmt_dt(now)}"

    @synchronized
    def poll_site(self, site: str) -> List[Dict[str, str]]:
        self.step()
        st = self.ensure_site(site)
        now = self._clock()
        out: List[Dict[str, str]] = []
        for sensor in st.sensors:
            sensor.last_poll = now
            # Record the "would-have-polled" SNMP OIDs like the legacy poller.
            idx = self._oid_index(sensor.oid)
            primary = f".1.3.6.1.4.1.3052.10.1.1.1.1.10.1.2.{idx}"
            fallback = f".1.3.6.1.4.1.3052.10.1.1.1.1.7.1.2.{idx}"
            self._snmp_calls.append(
                SnmpCall(
                    ts=_fmt_dt(now),
                    site=st.cell_site,
                    sensor=sensor.name,
                    primary_oid=primary,
                    fallback_oid=fallback,
                    returned_value=sensor.value,
                )
            )
            out.append(
                {
                    "SensorName": sensor.name,
                    "PollValue": sensor.value,
                    "LastPoll": _fmt_dt(now),
                }
            )
        return out

    @synchronized
    def snmp_calls(self, site: Optional[str] = None, limit: int = 250) -> List[Dict[str, str]]:
        calls = list(self._snmp_calls)
        if site:
            calls = [c for c in calls if c.site == site]
        calls = list(reversed(calls))[: max(1, limit)]
        return [asdict(c) for c in calls]

    @synchronized
    def debug_state(self) -> Dict[str, object]:
        self.step()
        sites = sorted(self._sites.values(), key=lambda s: _site_sort_key(s.cell_site))
        return {
            "site_count": len(sites),
            "sites": [s.live_row() for s in sites],
            "snmp_call_count": len(self._snmp_calls),
        }

    def _normalize_metric(self, raw: str) -> str:
        if raw in METRICS_ORDER:
            return raw
        key = raw.strip().lower()
        if key in TYPE_MAP:
            return TYPE_MAP[key]
        # Best-effort: title-case and hope it matches.
        guess = raw.strip()
        guess = guess[:1].upper() + guess[1:]
        if guess in METRICS_ORDER:
            return guess
        raise ValueError("Unknown sensor metric.")

    @staticmethod
    def _oid_index(oid: str) -> str:
        parts = oid.split(".")
        # Match the legacy code: sensor.OID.Split(".")[16]
        if len(parts) <= 16:
            return "0"
        return parts[16] or "0"
