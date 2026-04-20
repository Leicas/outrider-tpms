# Garmin Outrider BLE TPMS — Protocol Reference

Reverse-engineered 2026-04-19. Two sensors, one per wheel.

## Devices

| Label | BLE Name | MAC Address | Position | Custom Service UUID | Notify Char UUID |
|-------|----------|-------------|----------|---------------------|------------------|
| OutriderF | `OutriderF` | `AA:AA:AA:AA:AA:AA` | Front | `efcd1400-6745-2301-efcd-ab8967451523` | `efcd1401-6745-2301-efcd-ab8967451523` |
| OutriderR | `OutriderR` | `BB:BB:BB:BB:BB:BB` | Rear | `efcd1400-6745-2301-efcd-ab8967451524` | `efcd1401-6745-2301-efcd-ab8967451524` |

Note: the service/char UUIDs differ only in the last two hex digits (`23` vs `24`).

## Behavior

- Sensors are in deep sleep by default. They must be woken by wheel motion (accelerometer).
- Once awake they advertise with local name `OutriderF` / `OutriderR`.
- After BLE connection, the notify characteristic (`efcd1401`) sends a 2-byte payload every ~5 seconds.
- The advertising window after wake-up is short. Use `gatt_wait.py` (scan-then-connect) to catch them.

## Pressure Encoding

The 2-byte notification payload is a **little-endian uint16** representing **absolute pressure in PSI × 10**.

```
raw_u16 = struct.unpack("<H", payload)[0]
absolute_psi = raw_u16 / 10.0
gauge_psi = absolute_psi - 14.7    # subtract 1 atmosphere
```

### Verified readings (2026-04-19)

| Sensor | Raw hex | raw_u16 | Absolute PSI | Gauge PSI (actual) |
|--------|---------|---------|-------------|-------------------|
| OutriderF | `5003` | 848 | 84.8 | **70.1** |
| OutriderR | `db02` | 731 | 73.1 | **58.4** |

### Unit conversions from gauge PSI

```
kPa  = gauge_psi × 6.89476
bar  = gauge_psi × 0.0689476
```

## GATT Service Map

Both devices expose three standard services and one custom service. Full GATT dumps are in `gatt-*.json`.

### Standard services

| Service | UUID |
|---------|------|
| Generic Access Profile | `00001800-...` |
| Generic Attribute Profile | `00001801-...` |

### Custom service (`efcd1400-...`)

| Char suffix | Properties | Description |
|-------------|-----------|-------------|
| `1401` | notify | Pressure reading (2 bytes, LE u16, absolute PSI × 10) |
| `1402` | notify, read | Unknown (reads `0x00`) |
| `1403` | read, write | Unknown (reads `0x0000`) — possibly config |
| `1404` | read, write | Unknown (reads `0x0000`) — possibly config |
| `1405` | indicate, write | Unknown — possibly command/response channel |

## Files

| File | Purpose |
|------|---------|
| `monitor.py` | Live pressure monitor — connects, decodes, prints + logs JSONL |
| `scan.py` | BLE advertisement scanner/logger |
| `gatt_wait.py` | Scan-then-connect GATT dumper (handles short advertising windows) |
| `gatt_read.py` | Direct-connect GATT dumper (needs device already advertising) |
| `analyze.py` | Byte-pattern search across advertisement logs |
| `list_devices.py` | Summarize unique devices from JSONL logs |
| `gatt-*.json` | Full GATT service dumps per device |
| `adv-*.jsonl` | Raw advertisement logs |
| `pressure-*.jsonl` | Decoded pressure logs from `monitor.py` |
