"""Wait for one or more BLE peripherals to advertise, then connect and dump GATT.

Start the script first, THEN wake the sensors. The moment a target MAC is detected
it's handed off to a connection task (other targets continue to be watched in the
background). Use when sensors have a short advertising window after wake-up.

Usage:
    python gatt_wait.py AA:AA:AA:AA:AA:AA BB:BB:BB:BB:BB:BB --timeout 120 --notify-seconds 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


async def dump_gatt(device: BLEDevice, notify_seconds: float, out_dir: Path) -> None:
    addr = device.address
    print(f"\n[{addr}] Connecting... (name={device.name!r})")
    result: dict = {
        "address": addr,
        "name": device.name,
        "services": [],
        "notifications": [],
        "connected_at": datetime.now().isoformat(timespec="milliseconds"),
    }

    try:
        async with BleakClient(device) as client:
            if not client.is_connected:
                print(f"[{addr}] Connect failed.")
                result["error"] = "connect_failed"
            else:
                print(f"[{addr}] Connected. Reading services...")
                notify_chars: list[BleakGATTCharacteristic] = []

                for service in client.services:
                    svc_entry = {"uuid": service.uuid, "description": service.description, "characteristics": []}
                    print(f"[{addr}] [SERVICE] {service.uuid}  {service.description}")
                    for char in service.characteristics:
                        props = list(char.properties)
                        char_entry = {
                            "uuid": char.uuid,
                            "description": char.description,
                            "handle": char.handle,
                            "properties": props,
                            "value_hex": None,
                            "value_ascii": None,
                            "error": None,
                            "descriptors": [],
                        }
                        print(f"[{addr}]   [CHAR] {char.uuid}  {char.description!r}  props={props}")
                        if "read" in props:
                            try:
                                data = await client.read_gatt_char(char)
                                char_entry["value_hex"] = data.hex()
                                char_entry["value_ascii"] = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                                print(f"[{addr}]     read: hex={char_entry['value_hex']}  ascii={char_entry['value_ascii']!r}")
                            except Exception as e:
                                char_entry["error"] = str(e)
                                print(f"[{addr}]     read FAILED: {e}")
                        for desc in char.descriptors:
                            desc_entry = {"uuid": desc.uuid, "handle": desc.handle, "value_hex": None}
                            try:
                                data = await client.read_gatt_descriptor(desc.handle)
                                desc_entry["value_hex"] = data.hex()
                            except Exception as e:
                                desc_entry["error"] = str(e)
                            char_entry["descriptors"].append(desc_entry)
                        if "notify" in props or "indicate" in props:
                            notify_chars.append(char)
                        svc_entry["characteristics"].append(char_entry)
                    result["services"].append(svc_entry)

                if notify_chars and notify_seconds > 0:
                    print(f"[{addr}] Subscribing to {len(notify_chars)} notify/indicate char(s) for {notify_seconds}s...")

                    def make_cb(char_uuid: str):
                        def _cb(_sender, data: bytearray) -> None:
                            ts = datetime.now().isoformat(timespec="milliseconds")
                            hx = bytes(data).hex()
                            ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                            print(f"[{addr}] [NOTIFY] {ts}  {char_uuid}  hex={hx}  ascii={ascii_str!r}")
                            result["notifications"].append({
                                "ts": ts, "char": char_uuid, "hex": hx, "ascii": ascii_str,
                            })
                        return _cb

                    started: list[BleakGATTCharacteristic] = []
                    for char in notify_chars:
                        try:
                            await client.start_notify(char, make_cb(char.uuid))
                            started.append(char)
                        except Exception as e:
                            print(f"[{addr}]   subscribe to {char.uuid} failed: {e}")

                    try:
                        await asyncio.sleep(notify_seconds)
                    finally:
                        for char in started:
                            try:
                                await client.stop_notify(char)
                            except Exception:
                                pass
    except Exception as e:
        print(f"[{addr}] ERROR: {e}")
        result["error"] = str(e)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = addr.replace(":", "").lower()
    path = out_dir / f"gatt-{safe}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[{addr}] Saved: {path}")


async def run(targets: set[str], timeout: float, notify_seconds: float, out_dir: Path) -> None:
    out_dir.mkdir(exist_ok=True)
    remaining = {a.upper() for a in targets}
    in_flight: dict[str, asyncio.Task] = {}
    found: dict[str, BLEDevice] = {}
    done_event = asyncio.Event()
    lock = asyncio.Lock()

    def on_detection(device: BLEDevice, adv: AdvertisementData) -> None:
        addr = device.address.upper()
        if addr not in remaining or addr in in_flight:
            return
        asyncio.create_task(_handoff(device))

    async def _handoff(device: BLEDevice) -> None:
        addr = device.address.upper()
        async with lock:
            if addr in in_flight or addr not in remaining:
                return
            remaining.discard(addr)
            found[addr] = device
            print(f"[{addr}] Detected (rssi={device.rssi if hasattr(device, 'rssi') else '?'}). Stopping scan to connect.")
            if not remaining:
                # All targets seen — stop scanner so we don't keep scanning during connect
                pass
            task = asyncio.create_task(dump_gatt(device, notify_seconds, out_dir))
            in_flight[addr] = task
            task.add_done_callback(lambda _t: done_event.set() if all(t.done() for t in in_flight.values()) and not remaining else None)

    scanner = BleakScanner(detection_callback=on_detection)
    await scanner.start()
    print(f"Watching for {sorted(remaining)} (timeout={timeout}s). Wake the sensors now.\n")
    try:
        # Wait until either timeout OR all targets found and all connects completed
        try:
            await asyncio.wait_for(_wait_for_all(remaining, in_flight, done_event), timeout=timeout)
        except asyncio.TimeoutError:
            print(f"\nTimeout after {timeout}s.")
    finally:
        await scanner.stop()

    # Wait for any in-flight connects to finish
    if in_flight:
        print("Waiting for connect tasks to finish...")
        await asyncio.gather(*in_flight.values(), return_exceptions=True)

    missed = sorted({a.upper() for a in targets} - set(found))
    if missed:
        print(f"\nDid not see: {missed}")


async def _wait_for_all(remaining: set[str], in_flight: dict[str, asyncio.Task], done_event: asyncio.Event) -> None:
    """Return when all targets have been detected AND all dump tasks have finished."""
    while True:
        await asyncio.sleep(0.5)
        if not remaining and in_flight and all(t.done() for t in in_flight.values()):
            return


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("addresses", nargs="+", help="BLE MAC address(es) to wait for")
    p.add_argument("--timeout", type=float, default=120.0, help="max seconds to wait before giving up")
    p.add_argument("--notify-seconds", type=float, default=20.0, help="how long to stay subscribed to notify chars per device")
    p.add_argument("--out-dir", default=".", help="output directory for JSON dumps")
    args = p.parse_args()
    try:
        asyncio.run(run(set(args.addresses), args.timeout, args.notify_seconds, Path(args.out_dir)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
