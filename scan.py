"""BLE advertisement logger for reverse-engineering the Outrider tire pressure sensor.

Prints a live per-device summary to stdout and writes every raw advertisement to a
JSONL file so the stream can be diffed across runs (e.g. before/after a pressure change).

Usage:
    python scan.py                       # scan everything for 60s
    python scan.py --duration 0          # scan until Ctrl+C
    python scan.py --filter outrider     # only keep devices whose name/address contains the substring (case-insensitive)
    python scan.py --address AA:BB:...   # only log a specific MAC once identified
    python scan.py --log run2.jsonl      # custom log path (default: adv-YYYYmmdd-HHMMSS.jsonl)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


def _hexify_mfr(mfr: dict[int, bytes]) -> dict[str, str]:
    return {f"0x{cid:04x}": data.hex() for cid, data in mfr.items()}


def _hexify_svc(svc: dict[str, bytes]) -> dict[str, str]:
    return {uuid: data.hex() for uuid, data in svc.items()}


def _matches(device: BLEDevice, adv: AdvertisementData, needle: str | None, address: str | None) -> bool:
    if address and device.address.lower() != address.lower():
        return False
    if needle:
        hay = " ".join(filter(None, [device.address, device.name or "", adv.local_name or ""])).lower()
        if needle.lower() not in hay:
            return False
    return True


async def run(duration: float, log_path: Path, needle: str | None, address: str | None) -> None:
    seen: dict[str, dict] = {}
    count = 0

    log_fh = log_path.open("a", encoding="utf-8", buffering=1)
    print(f"Logging to {log_path}")
    print(f"Filter: name/addr contains '{needle}'" if needle else "Filter: none (all devices)")
    if address:
        print(f"Address lock: {address}")
    print("Press Ctrl+C to stop.\n")

    def on_detection(device: BLEDevice, adv: AdvertisementData) -> None:
        nonlocal count
        if not _matches(device, adv, needle, address):
            return
        count += 1
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        record = {
            "ts": now,
            "address": device.address,
            "rssi": adv.rssi,
            "name": device.name,
            "local_name": adv.local_name,
            "tx_power": adv.tx_power,
            "service_uuids": adv.service_uuids,
            "service_data": _hexify_svc(adv.service_data),
            "manufacturer_data": _hexify_mfr(adv.manufacturer_data),
        }
        log_fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        key = device.address
        prev = seen.get(key)
        seen[key] = record
        changed = prev is None or (
            prev["manufacturer_data"] != record["manufacturer_data"]
            or prev["service_data"] != record["service_data"]
        )
        if changed:
            tag = "NEW" if prev is None else "CHG"
            label = adv.local_name or device.name or "?"
            mfr = ",".join(record["manufacturer_data"].keys()) or "-"
            svc = ",".join(record["service_data"].keys()) or "-"
            print(
                f"[{tag}] {device.address}  rssi={adv.rssi:>4}  "
                f"name={label!r:30}  mfr={mfr}  svc={svc}"
            )

    scanner = BleakScanner(detection_callback=on_detection)
    await scanner.start()
    try:
        if duration > 0:
            await asyncio.sleep(duration)
        else:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()

            def _stop(*_: object) -> None:
                loop.call_soon_threadsafe(stop.set)

            try:
                signal.signal(signal.SIGINT, _stop)
            except (ValueError, AttributeError):
                pass
            await stop.wait()
    finally:
        await scanner.stop()
        log_fh.close()

    print(f"\nCaptured {count} advertisements from {len(seen)} device(s).")
    if seen:
        print("\nUnique devices seen:")
        for addr, rec in sorted(seen.items(), key=lambda kv: (kv[1]["rssi"] or -999), reverse=True):
            label = rec["local_name"] or rec["name"] or ""
            print(f"  {addr}  rssi={rec['rssi']:>4}  {label}")


def main() -> None:
    p = argparse.ArgumentParser(description="BLE advertisement logger (Outrider reverse-engineering).")
    p.add_argument("--duration", type=float, default=60.0, help="seconds to scan (0 = until Ctrl+C). Default: 60")
    p.add_argument("--filter", dest="needle", default=None, help="case-insensitive substring match on name/address")
    p.add_argument("--address", default=None, help="lock onto a specific MAC address")
    p.add_argument("--log", default=None, help="JSONL output path (default: adv-<timestamp>.jsonl)")
    args = p.parse_args()

    log_path = Path(args.log) if args.log else Path(f"adv-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")

    try:
        asyncio.run(run(args.duration, log_path, args.needle, args.address))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
