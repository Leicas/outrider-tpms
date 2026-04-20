"""Live tire-pressure monitor for Garmin Outrider BLE TPMS sensors.

Connects to OutriderF and OutriderR, subscribes to pressure notifications,
decodes absolute-pressure values to gauge PSI, and logs everything to a
timestamped JSONL file.

Protocol summary (discovered 2026-04-19):
    - Each sensor exposes a custom GATT service with a notify characteristic
      that sends a little-endian uint16 every ~5 seconds.
    - The value is absolute pressure in PSI * 10.
    - Gauge PSI = (value / 10) - 14.7   (subtract 1 atm)

Usage:
    python monitor.py                          # monitor both sensors, log to auto-named file
    python monitor.py --duration 0             # run until Ctrl+C
    python monitor.py --log pressures.jsonl    # custom log path
    python monitor.py --atm 14.5              # override atmospheric pressure offset
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import os

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# ---------------------------------------------------------------------------
# Device configuration
# ---------------------------------------------------------------------------
# Set these env vars (or edit locally — but do NOT commit your real MACs):
#   OUTRIDER_FRONT_MAC=XX:XX:XX:XX:XX:XX
#   OUTRIDER_REAR_MAC=YY:YY:YY:YY:YY:YY
FRONT_MAC = os.environ.get("OUTRIDER_FRONT_MAC", "AA:AA:AA:AA:AA:AA").upper()
REAR_MAC = os.environ.get("OUTRIDER_REAR_MAC", "BB:BB:BB:BB:BB:BB").upper()

DEVICES = {
    FRONT_MAC: {"name": "OutriderF", "position": "front"},
    REAR_MAC: {"name": "OutriderR", "position": "rear"},
}

# Each sensor uses a slightly different service UUID (last two hex digits differ)
NOTIFY_CHARS = {
    FRONT_MAC: "efcd1401-6745-2301-efcd-ab8967451523",
    REAR_MAC: "efcd1401-6745-2301-efcd-ab8967451524",
}

ATM_PSI = 14.7  # 1 atmosphere in PSI (default offset)

# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def decode_pressure(raw: bytes, atm: float = ATM_PSI) -> dict:
    """Decode a 2-byte notification into pressure values.

    Returns dict with raw_u16, absolute_psi, gauge_psi.
    """
    if len(raw) < 2:
        return {"raw_hex": raw.hex(), "error": "payload too short"}
    value = struct.unpack_from("<H", raw)[0]
    abs_psi = value / 10.0
    gauge_psi = abs_psi - atm
    return {
        "raw_hex": raw.hex(),
        "raw_u16": value,
        "absolute_psi": round(abs_psi, 2),
        "gauge_psi": round(gauge_psi, 2),
        "gauge_bar": round(gauge_psi * 0.0689476, 4),
        "gauge_kpa": round(gauge_psi * 6.89476, 2),
    }


# ---------------------------------------------------------------------------
# Connection + notification handling
# ---------------------------------------------------------------------------

async def connect_and_monitor(
    device: BLEDevice,
    addr: str,
    atm: float,
    log_fh,
    stop_event: asyncio.Event,
) -> None:
    """Connect to a single sensor and stream notifications until stop_event."""
    info = DEVICES[addr]
    char_uuid = NOTIFY_CHARS[addr]
    print(f"[{info['name']}] Connecting...")

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                print(f"[{info['name']}] Connection failed.")
                return
            print(f"[{info['name']}] Connected. Subscribing to {char_uuid}")

            def on_notify(_char: BleakGATTCharacteristic, data: bytearray) -> None:
                ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                decoded = decode_pressure(bytes(data), atm)
                record = {
                    "ts": ts,
                    "address": addr,
                    "name": info["name"],
                    "position": info["position"],
                    **decoded,
                }
                line = json.dumps(record, ensure_ascii=False)
                log_fh.write(line + "\n")
                log_fh.flush()

                psi = decoded.get("gauge_psi", "?")
                kpa = decoded.get("gauge_kpa", "?")
                print(
                    f"[{info['name']}] {ts}  "
                    f"gauge={psi} PSI  ({kpa} kPa)  "
                    f"abs={decoded.get('absolute_psi', '?')} PSI  "
                    f"raw=0x{decoded.get('raw_hex', '??')}"
                )

            await client.start_notify(char_uuid, on_notify)
            try:
                await stop_event.wait()
            finally:
                try:
                    await client.stop_notify(char_uuid)
                except Exception:
                    pass
    except Exception as e:
        print(f"[{info['name']}] ERROR: {e}")


async def run(duration: float, log_path: Path, atm: float) -> None:
    targets = {a.upper() for a in DEVICES}
    found: dict[str, BLEDevice] = {}
    stop_event = asyncio.Event()
    lock = asyncio.Lock()

    log_fh = log_path.open("a", encoding="utf-8", buffering=1)
    print(f"Logging to {log_path}")
    print(f"Atmospheric offset: {atm} PSI")
    print(f"Watching for {sorted(targets)}. Wake the sensors now.\n")

    # --- scanner callback ---
    def on_detection(device: BLEDevice, _adv: AdvertisementData) -> None:
        addr = device.address.upper()
        if addr in targets and addr not in found:
            asyncio.create_task(_handle_found(device))

    async def _handle_found(device: BLEDevice) -> None:
        addr = device.address.upper()
        async with lock:
            if addr in found:
                return
            found[addr] = device
            info = DEVICES.get(addr, {})
            print(f"[{info.get('name', addr)}] Detected!")

    # --- scan phase ---
    scanner = BleakScanner(detection_callback=on_detection)
    await scanner.start()

    # Wait until both found or 120s
    for _ in range(240):
        if len(found) == len(targets):
            break
        await asyncio.sleep(0.5)
    await scanner.stop()

    missed = targets - {a.upper() for a in found}
    if missed:
        print(f"\nDid not find: {sorted(missed)}")
    if not found:
        log_fh.close()
        return

    # --- duration timer ---
    async def _timer() -> None:
        if duration > 0:
            await asyncio.sleep(duration)
            stop_event.set()

    timer_task = asyncio.create_task(_timer()) if duration > 0 else None

    # --- signal handler ---
    loop = asyncio.get_running_loop()

    def _stop(*_: object) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    try:
        signal.signal(signal.SIGINT, _stop)
    except (ValueError, AttributeError):
        pass

    # --- connect to all found devices in parallel ---
    tasks = [
        asyncio.create_task(connect_and_monitor(dev, addr, atm, log_fh, stop_event))
        for addr, dev in found.items()
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    if timer_task and not timer_task.done():
        timer_task.cancel()
    log_fh.close()
    print(f"\nDone. Log saved to {log_path}")


def main() -> None:
    p = argparse.ArgumentParser(description="Live Outrider TPMS monitor.")
    p.add_argument("--duration", type=float, default=300.0,
                   help="seconds to monitor (0 = until Ctrl+C). Default: 300")
    p.add_argument("--log", default=None,
                   help="JSONL output path (default: pressure-<timestamp>.jsonl)")
    p.add_argument("--atm", type=float, default=ATM_PSI,
                   help=f"atmospheric pressure in PSI to subtract (default: {ATM_PSI})")
    args = p.parse_args()

    log_path = Path(args.log) if args.log else Path(
        f"pressure-{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"
    )

    try:
        asyncio.run(run(args.duration, log_path, args.atm))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
