#!/usr/bin/env python3
"""Detect new EIS export files and run the PostgreSQL ETL once per checksum.

The script is intentionally deterministic: it does not download data, does not
execute SQL from the input file, and only passes a fixed SQL script to psql.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_SUFFIXES = {".csv", ".json", ".xml"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"files": {}}
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("files", {}), dict):
        raise ValueError(f"Invalid manifest format: {path}")
    return payload


def save_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def run_psql(sql_file: Path, database_url: str, batch_id: int | None) -> None:
    env = os.environ.copy()
    env["PGDATABASE_URL"] = database_url
    if batch_id is not None:
        env["EIS_BATCH_ID"] = str(batch_id)
    # The SQL file is fixed by the operator; the input export is loaded into
    # staging separately. No shell=True and no interpolation of file names.
    command = ["psql", database_url, "--set", "ON_ERROR_STOP=1", "--file", str(sql_file)]
    subprocess.run(command, check=True, env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sql", type=Path, required=True)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.database_url and not args.dry_run:
        parser.error("--database-url or DATABASE_URL is required unless --dry-run is used")
    if not args.sql.is_file():
        parser.error(f"SQL file not found: {args.sql}")
    if not args.input_dir.is_dir():
        parser.error(f"Input directory not found: {args.input_dir}")

    manifest = load_manifest(args.manifest)
    files = manifest.setdefault("files", {})
    candidates = sorted(
        p for p in args.input_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIXES
    )
    new_files: list[tuple[Path, str]] = []
    for path in candidates:
        digest = sha256(path)
        key = str(path.relative_to(args.input_dir))
        previous = files.get(key, {})
        if previous.get("sha256") != digest or previous.get("status") != "processed":
            new_files.append((path, digest))

    if not new_files:
        print("No new or changed EIS exports.")
        return 0

    print(f"Detected {len(new_files)} new or changed export(s):")
    for path, digest in new_files:
        print(f"  {path} sha256={digest}")

    if args.dry_run:
        return 0

    # The ETL SQL expects staging tables to be loaded first. The production
    # loader should map each file to its staging table using COPY/JSON/XML
    # parsing, then execute this fixed SQL script.
    run_psql(args.sql, args.database_url, batch_id=None)
    now = datetime.now(timezone.utc).isoformat()
    for path, digest in new_files:
        key = str(path.relative_to(args.input_dir))
        files[key] = {
            "sha256": digest,
            "status": "processed",
            "processed_at": now,
        }
    save_manifest(args.manifest, manifest)
    print(f"Processed {len(new_files)} export(s).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"ETL failed with exit code {exc.returncode}; manifest was not advanced.", file=sys.stderr)
        raise SystemExit(exc.returncode)
