"""Official statistical guidance as *baselines*: NBM and GFS MOS, as-of.

A learned per-station bias on a multi-model consensus is Model Output
Statistics (Glahn & Lowry, 1972) — and the NWS publishes exactly that for
these stations: the National Blend of Models (NBM) and GFS MOS. A weather
trader's first question about our baseline is therefore "is it better than
what the market already reads?". This module makes that a runnable
comparison: fetch the archived bulletins for a window, store them, and let
`weather/replay.py::collect_rows(guidance_source=...)` price every strike
from them with the same decision rule, so the ablation
(docs/EXPERIMENTS.md, E5) is apples to apples.

Source: the Iowa Environmental Mesonet MOS archive
(`/api/1/mos.json`), which keeps every bulletin as issued — so a value is
what a trader could have read at that run time, never a reanalysis.

Timing (leak-free): for target day d we read the run issued at 12Z on d-1,
about a day ahead, matching the consensus' lead-1 guidance and preceding
both decision snapshots (local midnight for lows, late morning for highs).
In that run, the NBM `txn` / MOS `n_x` row at ftime d+1 00Z is the maximum
for local day d, and the row at ftime d 12Z is the minimum for day d
(the standard N/X convention of the bulletins).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from .stations import Station
from .verification import VerificationStore, prior_sigma

IEM_MOS = "https://mesonet.agron.iastate.edu/api/1/mos.json"

# IEM model code -> (our source name, field carrying the daily extreme)
SOURCES = {
    "NBS": ("nbm", "txn"),   # National Blend of Models, short range (hourly/3-hourly)
    "GFS": ("gfsmos", "n_x"),  # GFS MOS short range (the MAV bulletin)
}
RUN_HOUR_UTC = 12  # the d-1 12Z cycle, see module docstring


def _parse_bulletin(rows: list[dict], field: str, target: date) -> dict[str, float]:
    """{"high": °F, "low": °F} for `target` from one run's rows, where present."""
    want_max = f"{(target + timedelta(days=1)).isoformat()} 00:00"
    want_min = f"{target.isoformat()} 12:00"
    out: dict[str, float] = {}
    for r in rows:
        value = r.get(field)
        if value in (None, ""):
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        ftime = str(r.get("ftime", ""))[:16]
        if ftime == want_max:
            out["high"] = value
        elif ftime == want_min:
            out["low"] = value
    return out


class MosStore:
    """Append-only JSONL of as-of official guidance, one source per instance.

    Rows: {"source", "station", "kind", "target_date", "runtime", "value"}.
    Bias/sigma for the source come from the SAME settlements the consensus is
    verified against (the VerificationStore), with the same shrinkage — so a
    source is scored on its own record, out-of-window, exactly like ours.
    """

    def __init__(self, path: Path | str, source: str, verification: VerificationStore,
                 http: httpx.Client | None = None):
        if source not in {name for name, _ in SOURCES.values()}:
            raise ValueError(f"unknown MOS source {source!r}; use one of "
                             f"{sorted(name for name, _ in SOURCES.values())}")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.name = source
        self.verification = verification
        self.http = http or httpx.Client(timeout=60)
        self._model = next(code for code, (name, _) in SOURCES.items() if name == source)
        self._field = SOURCES[self._model][1]

    # --- storage ------------------------------------------------------------------
    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines()
                if line.strip()]

    def _values(self, station: str, kind: str) -> dict[str, float]:
        """target_date -> °F for this source (last write wins)."""
        out: dict[str, float] = {}
        for r in self._rows():
            if r["source"] == self.name and r["station"] == station and r["kind"] == kind:
                out[r["target_date"]] = r["value"]
        return out

    def record(self, station: str, kind: str, target: date, runtime: str, value: float) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps({"source": self.name, "station": station, "kind": kind,
                                "target_date": target.isoformat(), "runtime": runtime,
                                "value": value}) + "\n")

    # --- the guidance-source protocol used by collect_rows ---------------------------
    def guidance(self, station: str, kind: str, target: date) -> float | None:
        return self._values(station, kind).get(target.isoformat())

    def errors(self, station: str, kind: str, before: str | None = None) -> list[float]:
        settled: dict[str, float] = {}
        for r in self.verification._rows():
            if (r["type"] == "settlement" and r["station"] == station and r["kind"] == kind
                    and (before is None or r["target_date"] < before)):
                settled[r["target_date"]] = r["value"]
        guidance = {d: v for d, v in self._values(station, kind).items()
                    if before is None or d < before}
        return [settled[d] - guidance[d] for d in guidance.keys() & settled.keys()]

    def stats(self, station: str, kind: str, before: str | None = None,
              shrink: float = 10.0) -> tuple[float, float, int]:
        """(bias, sigma, n) with the verification store's shrinkage rule."""
        errs = self.errors(station, kind, before)
        n = len(errs)
        prior = prior_sigma(1)
        bias = sum(errs) / (n + shrink) if n else 0.0
        var = (sum((e - bias) ** 2 for e in errs) + shrink * prior * prior) / (n + shrink)
        return bias, math.sqrt(var), n

    # --- fetching -------------------------------------------------------------------
    def fetch_run(self, station: Station, runtime: datetime) -> list[dict]:
        resp = self.http.get(IEM_MOS, params={
            "station": station.obs_id, "model": self._model,
            "runtime": runtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        resp.raise_for_status()
        return resp.json().get("data", []) or []

    def load_station(self, station: Station, start: date, end: date) -> int:
        """Fetch every target day in [start, end] not yet stored. Idempotent."""
        have = {(kind, d) for kind in ("high", "low")
                for d in self._values(station.key, kind)}
        added = 0
        day = start
        while day <= end:
            missing = [k for k in ("high", "low") if (k, day.isoformat()) not in have]
            if missing:
                run = datetime(day.year, day.month, day.day, RUN_HOUR_UTC) - timedelta(days=1)
                try:
                    rows = self.fetch_run(station, run)
                except httpx.HTTPError:
                    day += timedelta(days=1)
                    continue
                extremes = _parse_bulletin(rows, self._field, day)
                for kind in missing:
                    if kind in extremes:
                        self.record(station.key, kind, day, run.isoformat(), extremes[kind])
                        added += 1
            day += timedelta(days=1)
        return added
