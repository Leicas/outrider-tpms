"""Dump every unique device + its full advertising profile from one or more JSONL logs."""

import argparse
import glob
import json
from collections import defaultdict


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="+")
    p.add_argument("--rssi-floor", type=int, default=-100)
    args = p.parse_args()

    devices: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "rssi_max": -999,
        "names": set(),
        "service_uuids": set(),
        "mfr": defaultdict(set),
        "svc": defaultdict(set),
    })
    for pattern in args.paths:
        for path in glob.glob(pattern):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    d = devices[r["address"]]
                    d["count"] += 1
                    if r["rssi"] and r["rssi"] > d["rssi_max"]:
                        d["rssi_max"] = r["rssi"]
                    for n in (r.get("name"), r.get("local_name")):
                        if n:
                            d["names"].add(n)
                    for u in (r.get("service_uuids") or []):
                        d["service_uuids"].add(u)
                    for cid, hx in (r.get("manufacturer_data") or {}).items():
                        d["mfr"][cid].add(hx)
                    for uuid, hx in (r.get("service_data") or {}).items():
                        d["svc"][uuid].add(hx)

    rows = sorted(devices.items(), key=lambda kv: kv[1]["rssi_max"], reverse=True)
    for addr, d in rows:
        if d["rssi_max"] < args.rssi_floor:
            continue
        names = sorted(d["names"]) or []
        print(f"{addr}  rssi={d['rssi_max']:>4}  count={d['count']:>4}  names={names}")
        if d["service_uuids"]:
            short = sorted({u[4:8] for u in d["service_uuids"] if u.startswith("0000")} | {u for u in d["service_uuids"] if not u.startswith("0000")})
            print(f"    svc_uuids: {short}")
        for cid, payloads in d["mfr"].items():
            tag = "static" if len(payloads) == 1 else f"{len(payloads)} variants"
            print(f"    mfr/{cid} ({tag}): {sorted(payloads)[0]}")
        for uuid, payloads in d["svc"].items():
            short = uuid[4:8] if uuid.startswith("0000") else uuid
            tag = "static" if len(payloads) == 1 else f"{len(payloads)} variants"
            print(f"    svc/{short} ({tag}): {sorted(payloads)[0]}")
        print()


if __name__ == "__main__":
    main()
