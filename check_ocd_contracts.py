#!/usr/bin/env python3
"""Check OCD contracts under 44-FZ for termination and unilateral refusal.

Primary retrieval uses the documented Ofdata mirror API because the public EIS
HTML interface does not expose a stable unauthenticated REST endpoint. The
script keeps EIS links returned by the API and can fetch each public EIS card
for a keyword scan. It never sends credentials to EIS and never submits forms.

Example:
  export OFDATA_API_KEY='...'
  python check_ocd_contracts.py --out data/ocd_44fz --fetch-eis

For a previously exported JSON file:
  python check_ocd_contracts.py --input contracts.json --out data/ocd_44fz
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

API_URL = "https://api.ofdata.ru/v2/contracts"
INN = "5021001371"
LAW = "44"
KEYWORDS = {
    "termination": [
        "расторг", "расторжение", "расторгнут", "прекращен", "прекращён",
        "соглашение о расторжении", "решение суда о расторжении",
    ],
    "unilateral_refusal": [
        "односторонн", "отказ заказчика", "отказ от исполнения",
        "односторонний отказ заказчика",
    ],
    "nonperformance": [
        "ненадлежащ", "неисполн", "существенное нарушение", "неустойк",
        "штраф", "пени", "уклонени",
    ],
}


def get_json(url: str, params: dict[str, Any] | None = None, timeout: int = 45) -> Any:
    response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "ocd-contract-audit/1.0"})
    response.raise_for_status()
    return response.json()


def fetch_all(api_key: str, sleep_seconds: float = 0.35) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    total_pages = None
    while True:
        payload = get_json(API_URL, {
            "key": api_key,
            "inn": INN,
            "law": LAW,
            "role": "supplier",
            "limit": 100,
            "page": page,
            "sort": "-date",
        })
        meta = payload.get("meta", {})
        if meta.get("status") == "error":
            raise RuntimeError(meta.get("message", "Ofdata API returned an error"))
        data = payload.get("data", {})
        if total_pages is None:
            total_pages = int(data.get("СтрВсего", 1) or 1)
            print(f"API reports {data.get('ЗапВсего', '?')} rows across {total_pages} pages", file=sys.stderr)
        rows.extend(data.get("Записи", []) or [])
        if page >= total_pages:
            break
        page += 1
        time.sleep(sleep_seconds)
    return rows


def load_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"].get("Записи", [])
    if not isinstance(payload, list):
        raise ValueError("Input JSON must be a list or an Ofdata response object")
    return payload


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    customer = row.get("Заказ") or {}
    objects = row.get("Объекты") or []
    suppliers = row.get("Постав") or []
    return {
        "contract_registry_number": row.get("РегНомер", ""),
        "eis_url": row.get("СтрЕИС", ""),
        "date_signed": row.get("Дата", ""),
        "execution_date": row.get("ДатаИсп", ""),
        "price_rub": row.get("Цена", ""),
        "region_code": row.get("РегионКод", ""),
        "customer_inn": customer.get("ИНН", ""),
        "customer_kpp": customer.get("КПП", ""),
        "customer_short_name": customer.get("НаимСокр", ""),
        "customer_full_name": customer.get("НаимПолн", ""),
        "suppliers": "; ".join(
            (item.get("НаимПолн") or item.get("НаимСокр") or item.get("ФИО") or "")
            for item in suppliers
        ),
        "objects": "; ".join((item.get("Наим") or "") for item in objects),
    }


def unique_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for row in rows:
        key = str(row.get("РегНомер") or row.get("contract_registry_number") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
    return cache_dir / f"{digest}.html"


def fetch_eis_text(url: str, cache_dir: Path, sleep_seconds: float = 0.6) -> str:
    if not url:
        return ""
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_path(cache_dir, url)
    if target.exists():
        return target.read_text(encoding="utf-8", errors="ignore")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc not in {"zakupki.gov.ru", "www.zakupki.gov.ru"}:
        return ""
    response = requests.get(url, timeout=45, headers={"User-Agent": "ocd-contract-audit/1.0"})
    response.raise_for_status()
    target.write_text(response.text, encoding="utf-8")
    time.sleep(sleep_seconds)
    return response.text


def keyword_hits(text: str) -> dict[str, list[str]]:
    lower = re.sub(r"\s+", " ", text.lower())
    hits: dict[str, list[str]] = {}
    for category, words in KEYWORDS.items():
        found = sorted({word for word in words if word in lower})
        if found:
            hits[category] = found
    return hits


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["contract_registry_number"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default=os.getenv("OFDATA_API_KEY"))
    parser.add_argument("--input", type=Path, help="Previously saved Ofdata JSON response")
    parser.add_argument("--out", type=Path, default=Path("data/ocd_44fz"))
    parser.add_argument("--fetch-eis", action="store_true", help="Fetch public EIS HTML cards and scan status keywords")
    args = parser.parse_args()

    if args.input:
        raw_rows = load_rows(args.input)
    else:
        if not args.api_key:
            parser.error("provide --api-key or OFDATA_API_KEY, or use --input")
        raw_rows = fetch_all(args.api_key)

    rows = [normalize(row) if "РегНомер" in row else row for row in unique_rows(raw_rows)]
    for row in rows:
        row.setdefault("status_keyword_hits", "")
        row.setdefault("eis_fetch_error", "")
        if args.fetch_eis and row.get("eis_url"):
            try:
                html = fetch_eis_text(row["eis_url"], args.out / "eis_cache")
                hits = keyword_hits(html)
                row["status_keyword_hits"] = json.dumps(hits, ensure_ascii=False)
            except requests.RequestException as exc:
                row["eis_fetch_error"] = str(exc)

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(rows, args.out / "contracts.csv")
    (args.out / "contracts.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "inn": INN,
        "law": LAW,
        "rows_received": len(raw_rows),
        "unique_rows_written": len(rows),
        "possible_status_rows": sum(bool(row.get("status_keyword_hits")) for row in rows),
        "note": "Keyword hits require manual verification in the EIS card and attachments; they are not legal findings.",
    }
    (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
