"""Smoke run (T-020): add a set of portals to a running Watchtower, follow them live, report what was delivered.

Talks to Watchtower only through its public API, the way a judge would, so the dashboard of the same service shows
the run as it happens. Adding a portal twice returns the same id without a second attach, so running this again just
reports again (documents keep downloading after a portal reaches `serving`).

    uv run python -m mockportals                                        # for the `mock` set, on :8081
    DATA_DIR=./data uv run --env-file .env python -m watchtower         # the service, dashboard on :8080
    uv run python testportals/smoke.py mock                             # or dev, blocked, unverified, unseen
    uv run python testportals/smoke.py dev --out smoke-dev.json

Exit code 0 when every portal settled (`serving` or `failed`) and, for mock portals, matched what the mock expects.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).parent
SETTLED = ("serving", "failed")
OFFSET = re.compile(r"[+-]\d\d:\d\d$|Z$")


@dataclass
class Target:
    name: str
    url: str
    expect_state: str | None = None  # mock portals: the state a correct system reports
    expected: list[dict] | None = None  # mock portals: exact tenders; real portals: TED notices (approximate)
    exact: bool = False


@dataclass
class Report:
    name: str
    url: str
    id: str = ""
    state: str = ""
    reason: str = ""
    tenders: int = 0
    with_documents: int = 0
    files: int = 0
    deadlines_without_offset: int = 0
    expected_state: str | None = None
    matched: str = ""  # mock: "23/23 exact"; real: "41 of ours match a TED notice; 12/89 TED notices found"
    problems: list[str] = field(default_factory=list)
    seconds: int = 0

    @property
    def ok(self) -> bool:
        return self.state in SETTLED and not self.problems


def targets(which: str, mock_base: str) -> list[Target]:
    if which == "mock":
        from mockportals.app import ENTRY, EXPECTED_STATE
        from mockportals.data import expected_tenders

        return [
            Target(
                k,
                mock_base + ENTRY[k],
                EXPECTED_STATE[k],
                expected_tenders(k) if EXPECTED_STATE[k] == "serving" else [],
                True,
            )
            for k in ENTRY
        ]
    portals = json.loads((HERE / "portals.json").read_text(encoding="utf-8"))["portals"]
    out = []
    for p in portals:
        if p["use"] != which:
            continue
        fixture = HERE / "fixtures" / f"{p['key']}.json"
        ted = json.loads(fixture.read_text(encoding="utf-8"))["expectedTenders"] if fixture.exists() else None
        out.append(Target(p["key"], p["baseUrl"], "failed" if which == "blocked" else None, ted))
    return out


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def compare_mock(got: list[dict], want: list[dict]) -> tuple[str, list[str]]:
    keys = ("title", "buyerName", "publicationDate", "submissionDeadline", "status", "referenceNumber")
    by_id = {t["id"]: t for t in got}
    problems = []
    missing = [w["id"] for w in want if w["id"] not in by_id]
    extra = sorted(set(by_id) - {w["id"] for w in want})
    if missing:
        problems.append(f"missing {len(missing)} tenders: {', '.join(missing[:5])}")
    if extra:
        problems.append(f"{len(extra)} tenders the portal doesn't have: {', '.join(extra[:5])}")
    exact = 0
    for w in want:
        t = by_id.get(w["id"])
        if t is None:
            continue
        wrong = [k for k in keys if k in w and t.get(k) != w[k]]
        docs = sorted(d["fileName"] for d in t.get("documents", []))
        if docs != w["documents"]:
            wrong.append(f"documents ({len(docs)}/{len(w['documents'])})")
        if wrong:
            problems.append(f"{w['id']}: wrong {', '.join(wrong)}")
        else:
            exact += 1
    return f"{exact}/{len(want)} exact", problems[:8]


def compare_ted(got: list[dict], ted: list[dict]) -> str:
    """Approximate: TED covers a 30-day window, some of it closed since; match on reference number or title."""
    refs = {norm(t.get("referenceNumber")) for t in ted} - {""}
    titles = {norm(t.get("title")) for t in ted} - {""}
    ours = sum(1 for t in got if norm(t.get("referenceNumber")) in refs or norm(t.get("title")) in titles)
    our_refs = {norm(t.get("referenceNumber")) for t in got} - {""}
    our_titles = {norm(t.get("title")) for t in got} - {""}
    found = sum(1 for t in ted if norm(t.get("referenceNumber")) in our_refs or norm(t.get("title")) in our_titles)
    return f"{ours}/{len(got)} of ours match a TED notice; {found}/{len(ted)} TED notices found"


def report(api: httpx.Client, t: Target, r: Report) -> None:
    p = api.get(f"/portals/{r.id}").json()
    r.state, r.reason, r.tenders = p["state"], p["reason"], p["tenderCount"]
    r.problems = []
    if t.expect_state and r.state in SETTLED and r.state != t.expect_state:
        r.problems.append(f"expected {t.expect_state}, got {r.state}")
    tenders = api.get(f"/portals/{r.id}/tenders").json()
    r.with_documents = sum(1 for x in tenders if x.get("documents"))
    r.files = sum(len(x.get("documents") or []) for x in tenders)
    r.deadlines_without_offset = sum(
        1 for x in tenders if x.get("submissionDeadline") and not OFFSET.search(x["submissionDeadline"])
    )
    if r.deadlines_without_offset:
        r.problems.append(f"{r.deadlines_without_offset} deadlines without a UTC offset")
    if t.expected is None:
        r.matched = "no fixture"
    elif t.exact:
        if r.state == "serving":
            r.matched, problems = compare_mock(tenders, t.expected)
            r.problems += problems
    elif tenders:
        r.matched = compare_ted(tenders, t.expected)


def run(args: argparse.Namespace) -> int:
    chosen = targets(args.set, args.mock_base.rstrip("/"))
    if args.only:
        chosen = [t for t in chosen if t.name in args.only]
    if not chosen:
        print(f"no portals in set {args.set!r}", file=sys.stderr)
        return 2
    api = httpx.Client(base_url=args.api.rstrip("/"), timeout=30)
    reports = {t.name: Report(t.name, t.url, expected_state=t.expect_state) for t in chosen}
    start = time.monotonic()
    for t in chosen:
        res = api.post("/portals", json={"url": t.url})
        res.raise_for_status()
        reports[t.name].id = res.json()["id"]
        print(f"added {t.name:32} {t.url}  → {reports[t.name].id}")
    print(f"\nfollowing {len(chosen)} portals (dashboard: {args.api.rstrip('/')}/) …\n")

    last: dict[str, str] = {}
    deadline = start + args.timeout * 60
    while True:
        portals = {p["id"]: p for p in api.get("/portals").json()}
        for r in reports.values():
            p = portals.get(r.id)
            if p is None:
                continue
            line = f"{p['state']}: {p['reason']}"
            if last.get(r.name) != line:
                last[r.name] = line
                stamp = datetime.now().strftime("%H:%M:%S")
                print(f"{stamp}  {r.name:32} {p['state']:12} tenders={p['tenderCount']:<5} {p['reason'][:110]}")
                if p["state"] in SETTLED and not r.seconds:
                    r.seconds = int(time.monotonic() - start)
            if p["state"] not in SETTLED:
                r.seconds = 0
        if all(portals.get(r.id, {}).get("state") in SETTLED for r in reports.values()):
            break
        if time.monotonic() > deadline:
            print(f"\ntimeout after {args.timeout} min; reporting as is")
            break
        time.sleep(args.poll)

    for t in chosen:
        report(api, t, reports[t.name])
    print_table(list(reports.values()))
    if args.out:
        out = {"set": args.set, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
               "portals": [asdict(r) for r in reports.values()]}  # fmt: skip
        Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0 if all(r.ok for r in reports.values()) else 1


def print_table(rows: list[Report]) -> None:
    print("\n| Portal | State | Tenders | With docs | Files | Match | Time | Reason / problems |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        mark = "" if r.ok else " ⚠"
        notes = "; ".join(r.problems) or r.reason
        mins = f"{r.seconds // 60}m{r.seconds % 60:02d}s" if r.seconds else "–"
        cells = [r.name, r.state + mark, r.tenders, r.with_documents, r.files, r.matched, mins, notes]
        print("| " + " | ".join(str(c) for c in cells) + " |")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", choices=["mock", "dev", "blocked", "unverified", "unseen"])
    ap.add_argument(
        "--only", nargs="+", metavar="NAME", help="only these portals of the set (mock kind or registry key)"
    )
    ap.add_argument("--api", default="http://localhost:8080", help="the running Watchtower")
    ap.add_argument("--mock-base", default="http://localhost:8081", help="where the mock portals run")
    ap.add_argument("--timeout", type=float, default=60, help="minutes to wait for every portal to settle")
    ap.add_argument("--poll", type=float, default=5, help="seconds between status polls (our own API, not the portals)")
    ap.add_argument("--out", help="also write the results as JSON here (keep it out of git)")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
