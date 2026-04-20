"""Analyze BLE advertisement logs for candidate Outrider tire-pressure encodings.

Given a ground truth (front_psi, rear_psi), scan every device's manufacturer_data and
service_data for byte patterns that encode those values under the usual TPMS scalings.
Also surface which bytes within a device's payload are *volatile* vs static, since
sensor readings change between advertisements while identifiers do not.

Usage:
    python analyze.py adv-*.jsonl --front 71.6 --rear 58.9
"""

from __future__ import annotations

import argparse
import glob
import json
import struct
from collections import defaultdict
from pathlib import Path


def candidates(psi: float) -> dict[str, str]:
    """Return {label: hex} for common TPMS scalings of this PSI value."""
    kpa = psi * 6.89476
    out: dict[str, list[int]] = {
        "psi_int_u8": [round(psi)],
        "psi_x10_u16le": [round(psi * 10)],
        "psi_x10_u16be": [round(psi * 10)],
        "psi_x100_u16le": [round(psi * 100)],
        "psi_x100_u16be": [round(psi * 100)],
        "kpa_int_u16le": [round(kpa)],
        "kpa_int_u16be": [round(kpa)],
        "kpa_x10_u16le": [round(kpa * 10)],
        "kpa_x10_u16be": [round(kpa * 10)],
        "bar_x100_u16le": [round(kpa / 10)],  # 1 bar = 100 kPa
        "bar_x100_u16be": [round(kpa / 10)],
    }
    result: dict[str, str] = {}
    for label, (value,) in ((k, v) for k, v in out.items()):
        if "u8" in label:
            if 0 <= value <= 0xFF:
                result[label] = f"{value:02x}"
        elif "u16le" in label:
            if 0 <= value <= 0xFFFF:
                result[label] = struct.pack("<H", value).hex()
        elif "u16be" in label:
            if 0 <= value <= 0xFFFF:
                result[label] = struct.pack(">H", value).hex()
    # Also add neighbors (±1) for the dominant scalings in case of rounding drift.
    for delta in (-1, 1):
        v10 = round(psi * 10) + delta
        result[f"psi_x10_u16le{delta:+d}"] = struct.pack("<H", v10).hex()
        vkpa = round(kpa) + delta
        result[f"kpa_int_u16le{delta:+d}"] = struct.pack("<H", vkpa).hex()
    return result


def load(paths: list[str]) -> list[dict]:
    records: list[dict] = []
    for pattern in paths:
        for p in glob.glob(pattern):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
    return records


def device_payloads(records: list[dict]) -> dict[str, dict]:
    """Group records by address, collecting payload variations."""
    out: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "rssi_max": -999,
        "names": set(),
        "mfr": defaultdict(set),   # cid -> set of hex payloads
        "svc": defaultdict(set),   # uuid -> set of hex payloads
    })
    for r in records:
        d = out[r["address"]]
        d["count"] += 1
        if r["rssi"] is not None and r["rssi"] > d["rssi_max"]:
            d["rssi_max"] = r["rssi"]
        for n in (r.get("name"), r.get("local_name")):
            if n:
                d["names"].add(n)
        for cid, hx in (r.get("manufacturer_data") or {}).items():
            d["mfr"][cid].add(hx)
        for uuid, hx in (r.get("service_data") or {}).items():
            d["svc"][uuid].add(hx)
    return out


def volatile_byte_mask(hex_payloads: set[str]) -> tuple[str, list[int]]:
    """Given multiple hex payloads of (mostly) same length, return the first payload
    and indices (byte offsets) where the bytes are not identical across all payloads."""
    if not hex_payloads:
        return "", []
    payloads = sorted(hex_payloads)
    ref = payloads[0]
    ref_bytes = bytes.fromhex(ref)
    volatile: list[int] = []
    for i, b in enumerate(ref_bytes):
        for other in payloads[1:]:
            ob = bytes.fromhex(other)
            if i < len(ob) and ob[i] != b:
                volatile.append(i)
                break
    return ref, volatile


def search_device(d: dict, needles: dict[str, dict[str, str]]) -> list[str]:
    """Return list of match descriptions for this device.
    needles: {wheel_label: {encoding_label: hex_needle}}"""
    hits: list[str] = []
    sources: list[tuple[str, str, str]] = []  # (origin, key, hex_payload)
    for cid, payloads in d["mfr"].items():
        for hx in payloads:
            sources.append(("mfr", cid, hx))
    for uuid, payloads in d["svc"].items():
        for hx in payloads:
            sources.append(("svc", uuid, hx))

    for wheel, enc_map in needles.items():
        for enc_label, needle in enc_map.items():
            for origin, key, hx in sources:
                idx = hx.find(needle)
                if idx >= 0:
                    # byte offset in payload = idx/2
                    hits.append(f"{wheel}[{enc_label}={needle}] in {origin}/{key} @byte {idx//2}  (payload={hx})")
    return hits


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="+", help="JSONL log files or globs")
    p.add_argument("--front", type=float, required=True, help="front tire PSI ground truth")
    p.add_argument("--rear", type=float, required=True, help="rear tire PSI ground truth")
    p.add_argument("--rssi-floor", type=int, default=-90, help="ignore devices weaker than this (default -90)")
    args = p.parse_args()

    records = load(args.paths)
    devices = device_payloads(records)
    print(f"Loaded {len(records)} advertisements from {len(devices)} unique devices.\n")

    needles = {
        "FRONT": candidates(args.front),
        "REAR": candidates(args.rear),
    }
    print("Searching for these byte patterns:")
    for wheel, encs in needles.items():
        print(f"  {wheel} ({args.front if wheel == 'FRONT' else args.rear} PSI):")
        for label, hx in encs.items():
            print(f"    {label:24} {hx}")
    print()

    matches: list[tuple[str, dict, list[str]]] = []
    for addr, d in devices.items():
        if d["rssi_max"] < args.rssi_floor:
            continue
        hits = search_device(d, needles)
        if hits:
            matches.append((addr, d, hits))

    if not matches:
        print("=== NO DIRECT HITS ===")
        print("Falling back to volatile-byte analysis for top-RSSI devices:\n")
    else:
        print(f"=== {len(matches)} DEVICES WITH MATCHING BYTES ===\n")
        for addr, d, hits in matches:
            print(f"[MATCH] {addr}  rssi_max={d['rssi_max']}  names={sorted(d['names']) or '-'}  count={d['count']}")
            for h in hits:
                print(f"    {h}")
            print()

    print("\n=== VOLATILE-BYTE ANALYSIS (bytes that change between advertisements) ===\n")
    ranked = sorted(devices.items(), key=lambda kv: kv[1]["rssi_max"], reverse=True)
    for addr, d in ranked:
        if d["rssi_max"] < args.rssi_floor:
            continue
        variable_mfr = {k: v for k, v in d["mfr"].items() if len(v) > 1}
        variable_svc = {k: v for k, v in d["svc"].items() if len(v) > 1}
        if not variable_mfr and not variable_svc:
            continue
        print(f"{addr}  rssi_max={d['rssi_max']}  names={sorted(d['names']) or '-'}  count={d['count']}")
        for cid, payloads in variable_mfr.items():
            ref, vol = volatile_byte_mask(payloads)
            print(f"  mfr/{cid}: {len(payloads)} variants, volatile bytes at {vol}")
            for hx in sorted(payloads)[:4]:
                print(f"    {hx}")
            if len(payloads) > 4:
                print(f"    ... ({len(payloads) - 4} more)")
        for uuid, payloads in variable_svc.items():
            ref, vol = volatile_byte_mask(payloads)
            print(f"  svc/{uuid}: {len(payloads)} variants, volatile bytes at {vol}")
            for hx in sorted(payloads)[:4]:
                print(f"    {hx}")
            if len(payloads) > 4:
                print(f"    ... ({len(payloads) - 4} more)")
        print()


if __name__ == "__main__":
    main()
