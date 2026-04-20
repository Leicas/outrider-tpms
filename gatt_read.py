"""Connect to one or more BLE peripherals, enumerate GATT, read every readable
characteristic, and optionally subscribe to notify/indicate characteristics for a
short window.

Usage:
    python gatt_read.py AA:AA:AA:AA:AA:AA BB:BB:BB:BB:BB:BB --notify-seconds 10

Output is also written to gatt-<address>-<timestamp>.json for diffing later.
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


def _props(char: BleakGATTCharacteristic) -> list[str]:
    return list(char.properties)


async def dump_one(address: str, notify_seconds: float) -> dict:
    print(f"\n=== {address} ===")
    print("Scanning for device (up to 10s)...")
    device = await BleakScanner.find_device_by_address(address, timeout=10.0)
    if device is None:
        print(f"  NOT FOUND. Is the sensor awake and nearby?")
        return {"address": address, "found": False}

    print(f"  Found: name={device.name!r}")
    result: dict = {
        "address": address,
        "found": True,
        "name": device.name,
        "services": [],
        "notifications": [],
    }

    async with BleakClient(device) as client:
        if not client.is_connected:
            print("  Connect failed.")
            return result
        print(f"  Connected. Enumerating services...")

        notify_chars: list[BleakGATTCharacteristic] = []

        for service in client.services:
            svc_entry = {"uuid": service.uuid, "description": service.description, "characteristics": []}
            print(f"  [SERVICE] {service.uuid}  {service.description}")
            for char in service.characteristics:
                props = _props(char)
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
                print(f"    [CHAR] {char.uuid}  {char.description!r}  props={props}")
                if "read" in props:
                    try:
                        data = await client.read_gatt_char(char)
                        char_entry["value_hex"] = data.hex()
                        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                        char_entry["value_ascii"] = ascii_str
                        print(f"      read: hex={data.hex()}  ascii={ascii_str!r}")
                    except Exception as e:
                        char_entry["error"] = str(e)
                        print(f"      read FAILED: {e}")
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
            print(f"\n  Subscribing to {len(notify_chars)} notify/indicate characteristic(s) for {notify_seconds}s...")

            def make_cb(char_uuid: str):
                def _cb(_sender, data: bytearray) -> None:
                    ts = datetime.now().isoformat(timespec="milliseconds")
                    hx = bytes(data).hex()
                    ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
                    print(f"    [NOTIFY] {ts}  {char_uuid}  hex={hx}  ascii={ascii_str!r}")
                    result["notifications"].append({
                        "ts": ts,
                        "char": char_uuid,
                        "hex": hx,
                        "ascii": ascii_str,
                    })
                return _cb

            started: list[BleakGATTCharacteristic] = []
            for char in notify_chars:
                try:
                    await client.start_notify(char, make_cb(char.uuid))
                    started.append(char)
                except Exception as e:
                    print(f"    subscribe to {char.uuid} failed: {e}")

            try:
                await asyncio.sleep(notify_seconds)
            finally:
                for char in started:
                    try:
                        await client.stop_notify(char)
                    except Exception:
                        pass

    return result


async def main_async(addresses: list[str], notify_seconds: float, out_dir: Path) -> None:
    out_dir.mkdir(exist_ok=True)
    for addr in addresses:
        try:
            result = await dump_one(addr, notify_seconds)
        except Exception as e:
            print(f"\n{addr}: ERROR {e}")
            result = {"address": addr, "error": str(e)}
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe = addr.replace(":", "").lower()
        path = out_dir / f"gatt-{safe}-{stamp}.json"
        path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"  Saved: {path}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("addresses", nargs="+", help="BLE MAC address(es) to connect to")
    p.add_argument("--notify-seconds", type=float, default=10.0, help="how long to stay subscribed to notify/indicate chars")
    p.add_argument("--out-dir", default=".", help="output directory for JSON dumps")
    args = p.parse_args()

    try:
        asyncio.run(main_async(args.addresses, args.notify_seconds, Path(args.out_dir)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
