from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from .config import DemoConfig, load_config
from .sim import SimState


def create_app(cfg: DemoConfig | None = None, *, sim: SimState | None = None) -> FastAPI:
    cfg = cfg or load_config()
    sim = sim or SimState(cfg)
    assets = Path(__file__).resolve().parent / "assets"
    app = FastAPI(
        title="SitebossHub synthetic monitoring demo", version="0.2.0",
        description="In-memory sensor simulation with legacy dashboard contracts. No device access or authentication.",
    )

    @app.exception_handler(KeyError)
    async def unknown_resource(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": "Unknown synthetic site."}, status_code=404)

    @app.exception_handler(ValueError)
    async def bad_input(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/SitebossHub")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(assets / "logo.svg", media_type="image/svg+xml")

    @app.get("/SitebossHub", include_in_schema=False)
    def overview() -> FileResponse:
        return FileResponse(assets / "dataOverviewPage.html", media_type="text/html")

    @app.get("/EP_SitebossHub", include_in_schema=False)
    def compact_overview() -> FileResponse:
        return FileResponse(assets / "ep_dataOverviewPage.html", media_type="text/html")

    @app.get("/SitebossHistory", include_in_schema=False)
    def history_page() -> FileResponse:
        return FileResponse(assets / "historyViewPage.html", media_type="text/html")

    @app.get("/demo/all")
    def all_data() -> list:
        return sim.live_data()

    @app.get("/demo/histAll")
    def hist_all() -> list:
        return sim.hist_all()

    @app.get("/demo/hist/{site_and_metric}")
    def hist(site_and_metric: str) -> dict:
        parts = site_and_metric.split(",")
        if len(parts) != 2 or not all(parts):
            raise HTTPException(400, "Expected '{site},{metric}'.")
        return sim.hist_single(*parts)

    @app.get("/demo/hist2/{query}")
    def history(query: str) -> list:
        """Legacy comma-delimited query: site,metric,YYYY-MM-DD,HH:MM:SS,count."""
        parts = query.split(",")
        if len(parts) != 5:
            raise HTTPException(400, "Expected '{site},{metric},{date},{time},{count}'.")
        site, metric, date, time, count = parts
        try:
            when = datetime.fromisoformat(f"{date}T{time}")
            limit = int(count)
        except ValueError:
            raise HTTPException(400, "Use an ISO date, local time and integer count.") from None
        if when.tzinfo is not None or not 1 <= limit <= 5000:
            raise HTTPException(400, "Use local time without timezone and count between 1 and 5000.")
        return sim.hist2(site, metric, when, limit)

    @app.get("/demo/sysGet")
    def syslog() -> list:
        return sim.syslog(limit=25)

    @app.get("/demo/genRunsGet", response_class=PlainTextResponse)
    def generator_runs() -> str:
        return sim.gen_runs_payload()

    @app.get("/demo/pollSite/{site}")
    def poll_site(site: str) -> list:
        return sim.poll_site(site)

    @app.get("/demo/report")
    def report() -> Response:
        rows = sim.live_data()
        output = StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        return Response(output.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": 'attachment; filename="sitebosshub-synthetic-snapshot.csv"',
        })

    @app.get("/debug/snmp_calls")
    def snmp_calls(site: str | None = None, limit: int = Query(250, ge=1, le=5000)) -> list:
        return sim.snmp_calls(site=site, limit=limit)

    @app.get("/debug/state")
    def state() -> dict:
        return sim.debug_state()

    @app.get("/healthz", include_in_schema=False, response_class=PlainTextResponse)
    def health() -> str:
        return "ok"

    return app
