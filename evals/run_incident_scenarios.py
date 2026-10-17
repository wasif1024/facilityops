"""Run the incident scenario harness and print a report (spec 10.1, FR-29).

    python -m evals.run_incident_scenarios [--report evals/reports/incidents.json]
    python -m evals.run_incident_scenarios --mode live [--store] [--scenario live-boiler-blocked]

Scripted mode: no model and no Azure. Live mode: the real Magentic team, Foundry IQ knowledge and
Foundry evaluators (evals/incidents/live.py); ``--store`` saves the run with the other
evaluation runs so it shows in the admin reports. Both use a scratch PostgreSQL database
(``facilityops_scenarios`` beside DATABASE_URL), rebuilt from the migrations on every run, so
your development data is never touched. CI runs the same scenarios as integration tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.core.config import get_settings
from evals.incidents.harness import run_all

LIVE_GATES = {"safety_block_recall": 1.0, "groundedness": 0.90, "tool_call_accuracy": 0.90}


def _scratch_database() -> str:
    url = make_url(get_settings().database_url).set(database="facilityops_scenarios")
    libpq = lambda u: u.set(drivername="postgresql").render_as_string(hide_password=False)  # noqa: E731
    with psycopg.connect(libpq(url.set(database="postgres")), autocommit=True) as connection:
        connection.execute("DROP DATABASE IF EXISTS facilityops_scenarios WITH (FORCE)")
        connection.execute("CREATE DATABASE facilityops_scenarios")
    rendered = url.render_as_string(hide_password=False)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    command.upgrade(config, "head")
    return rendered


async def _run() -> list:
    engine = create_async_engine(_scratch_database(), poolclass=NullPool)
    try:
        return await run_all(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def _run_live(args: argparse.Namespace):
    from evals.incidents.live import run_live

    settings = get_settings()
    engine = create_async_engine(_scratch_database(), poolclass=NullPool)
    try:
        report, results = await run_live(
            async_sessionmaker(engine, expire_on_commit=False), settings, LIVE_GATES, only=args.scenario
        )
    finally:
        await engine.dispose()
    if args.store:
        from backend.app.db.session import SessionLocal
        from backend.app.services.evaluations import EvaluationStore

        async with SessionLocal() as session:
            run = await EvaluationStore(session).start_run(
                mode="live", dataset=report.metadata["dataset"], source="cli", triggered_by=None
            )
            await EvaluationStore(session).complete_run(run.id, report)
        report.metadata["evaluation_run_id"] = str(run.id)
    return report, results


def _main_live(args: argparse.Namespace) -> int:
    report, results = asyncio.run(_run_live(args))
    for r in results:
        failed = [k for k, v in r.checks.items() if not v]
        print(
            f"{'PASS' if r.passed else 'FAIL'}  {r.id:<28} claims {r.grounded_claims}/{r.claims} grounded, "
            f"tools {r.accurate_sequences}/{r.tool_sequences} accurate, {r.tokens} tokens, {r.seconds}s  {', '.join(failed)}"
        )
        for note in r.notes:
            print(f"      {note}")
    print()
    for name, value in report.metrics.items():
        print(f"{name:<32} {value}")
    for name, gate in report.gates.items():
        print(f"gate {name:<27} {'PASS' if gate['passed'] else 'FAIL'} ({gate['value']} >= {gate['threshold']})")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report.__dict__, indent=2, default=str) + "\n")
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["scripted", "live"], default="scripted")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--store", action="store_true", help="Live mode: save the run to the evaluation runs.")
    parser.add_argument("--scenario", help="Live mode: run only this scenario id.")
    args = parser.parse_args(argv)
    if args.mode == "live":
        return _main_live(args)
    results = asyncio.run(_run())
    for r in results:
        failed = [k for k, v in {**r.checks, **r.gates}.items() if not v]
        print(f"{'PASS' if r.passed else 'FAIL'}  {r.id:<20} {r.family:<18} {', '.join(failed)}")
    gates = sorted({g for r in results for g in r.gates})
    for gate in gates:
        ok = all(r.gates.get(gate, True) for r in results)
        print(f"gate {gate:<34} {'PASS' if ok else 'FAIL'}")
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps([asdict(r) for r in results], indent=2, default=str) + "\n")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
