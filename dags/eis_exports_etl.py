"""Airflow DAG for processing new EIS exports.

The DAG assumes that an external process places CSV/JSON/XML exports under
EIS_INPUT_DIR and that the database connection is supplied as an Airflow
Connection named ``eis_postgres``. It does not download data from EIS.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator

PROJECT_DIR = Path("/opt/klimovsk-open-data")
INPUT_DIR = PROJECT_DIR / "data/raw/eis"
MANIFEST = PROJECT_DIR / "data/state/eis_manifest.json"
SQL_FILE = PROJECT_DIR / "eis_etl_postgres.sql"
RUNNER = PROJECT_DIR / "run_eis_from_git.sh"

with DAG(
    dag_id="eis_exports_etl",
    start_date=datetime(2026, 1, 1),
    schedule="*/15 * * * *",
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=30),
    tags=["eis", "postgres", "procurement"],
    description="Process new EIS exports exactly once per checksum.",
) as dag:
    process_exports = BashOperator(
        task_id="process_new_exports",
        bash_command="bash {{ params.runner }}",

        env={"DATABASE_URL": "{{ conn.eis_postgres.get_uri() }}"},
        append_env=True,
        params={
            "runner": str(RUNNER),
        },
    )
