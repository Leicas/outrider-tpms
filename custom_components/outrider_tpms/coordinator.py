"""Coordinator that keeps a GATT connection to an Outrider sensor open and
streams pressure notifications into Home Assistant state."""

from __future__ import annotations

import asyncio
import logging
import struct
import time
from datetime import datetime
from typing import Any

# If we're connected but haven't received a notify in this many seconds, assume
# the sensor has silently gone to sleep and drop the link so it can fully idle.
NOTIFY_SILENCE_TIMEOUT_SEC = 60.0
WATCHDOG_POLL_SEC = 15.0

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak_retry_connector import establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    ATM_PSI,
    DOMAIN,
    OUTRIDER_NOTIFY_CHAR_PREFIX,
    OUTRIDER_SERVICE_PREFIX,
    PSI_TO_KPA,
)

_LOGGER = logging.getLogger(__name__)


class OutriderCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage the BLE connection to a single Outrider sensor and expose readings."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        local_name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {local_name} ({address})",
            update_interval=None,  # event-driven via BLE notifications
        )
        self.address = address.upper()
        self.local_name = local_name
        self._client: BleakClient | None = None
        self._connect_lock = asyncio.Lock()
        self._unsub_bt: Any = None
        self._unsub_unavailable: Any = None
        self._notify_char: BleakGATTCharacteristic | None = None
        self._last_rssi: int | None = None
        self._connected: bool = False
        self._last_notify_monotonic: float = 0.0
        self._watchdog_task: asyncio.Task | None = None
        self.data = {}

    @property
    def connected(self) -> bool:
        """True when we have an active GATT connection with notify subscribed."""
        return self._connected

    @property
    def position(self) -> str:
        """Return 'front' / 'rear' / 'unknown' from the advertised local name."""
        if self.local_name.endswith("F"):
            return "front"
        if self.local_name.endswith("R"):
            return "rear"
        return "unknown"

    @callback
    def async_start(self) -> None:
        """Begin listening for advertisements; connect whenever the sensor wakes."""
        self._unsub_bt = bluetooth.async_register_callback(
            self.hass,
            self._async_on_advertisement,
            BluetoothCallbackMatcher(address=self.address, connectable=True),
            BluetoothScanningMode.PASSIVE,
        )
        self._unsub_unavailable = bluetooth.async_track_unavailable(
            self.hass,
            self._async_on_unavailable,
            self.address,
            connectable=True,
        )
        # Trigger an immediate attempt in case the device is already advertising.
        service_info = bluetooth.async_last_service_info(
            self.hass, self.address, connectable=True
        )
        if service_info is not None:
            self._async_on_advertisement(service_info, BluetoothChange.ADVERTISEMENT)

    async def async_stop(self) -> None:
        """Tear down BT callback and close any open GATT connection."""
        if self._unsub_bt is not None:
            self._unsub_bt()
            self._unsub_bt = None
        if self._unsub_unavailable is not None:
            self._unsub_unavailable()
            self._unsub_unavailable = None
        await self._async_disconnect()

    @callback
    def _async_on_unavailable(self, _service_info: BluetoothServiceInfoBleak) -> None:
        """HA has stopped seeing advertisements.

        BLE peripherals pause advertising while connected, so this fires
        routinely during a healthy session — ignore it in that case. Only
        when we're NOT connected does it mean the sensor has actually slept.
        We keep last pressure values in `self.data` either way; HA's
        `last_updated` tells the user how fresh the reading is.
        """
        if self._connected:
            _LOGGER.debug("%s: unavailable callback during active session, ignoring", self.local_name)
            return
        _LOGGER.debug("%s: no advertisements seen recently, keeping last values", self.local_name)
        if self._client is not None:
            self.hass.async_create_task(self._async_disconnect())

    @callback
    def _async_on_advertisement(
        self,
        service_info: BluetoothServiceInfoBleak,
        _change: BluetoothChange,
    ) -> None:
        """Called whenever the target sensor advertises."""
        self._last_rssi = service_info.rssi
        if self._client is not None and self._client.is_connected:
            return
        self.hass.async_create_task(self._async_connect())

    async def _async_connect(self) -> None:
        """Establish a GATT connection and subscribe to pressure notifications."""
        if self._connect_lock.locked():
            return
        async with self._connect_lock:
            if self._client is not None and self._client.is_connected:
                return
            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if ble_device is None:
                _LOGGER.debug("%s: no BLEDevice for address, skipping connect", self.local_name)
                return
            _LOGGER.debug("%s: establishing GATT connection", self.local_name)
            try:
                client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self.local_name,
                    disconnected_callback=self._on_disconnect,
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("%s: connection failed: %s", self.local_name, err)
                return

            notify_char = self._find_notify_char(client)
            if notify_char is None:
                _LOGGER.warning(
                    "%s: no notify characteristic starting with %s found; disconnecting",
                    self.local_name,
                    OUTRIDER_NOTIFY_CHAR_PREFIX,
                )
                await client.disconnect()
                return

            try:
                await client.start_notify(notify_char, self._on_notify)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("%s: start_notify failed: %s", self.local_name, err)
                await client.disconnect()
                return

            self._client = client
            self._notify_char = notify_char
            self._connected = True
            self._last_notify_monotonic = time.monotonic()
            if self._watchdog_task is None or self._watchdog_task.done():
                self._watchdog_task = self.hass.async_create_task(self._async_watchdog())
            _LOGGER.info("%s: subscribed to pressure notifications", self.local_name)

    @staticmethod
    def _find_notify_char(client: BleakClient) -> BleakGATTCharacteristic | None:
        for service in client.services:
            if not service.uuid.lower().startswith(OUTRIDER_SERVICE_PREFIX):
                continue
            for char in service.characteristics:
                if char.uuid.lower().startswith(OUTRIDER_NOTIFY_CHAR_PREFIX) and (
                    "notify" in char.properties
                ):
                    return char
        return None

    @callback
    def _on_disconnect(self, _client: BleakClient) -> None:
        _LOGGER.debug("%s: disconnected (keeping last values)", self.local_name)
        self._client = None
        self._notify_char = None
        self._connected = False

    @callback
    def _on_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle a pressure notification: decode, store, push update."""
        if len(data) < 2:
            _LOGGER.debug("%s: short payload %s", self.local_name, data.hex())
            return
        self._last_notify_monotonic = time.monotonic()
        raw_u16 = struct.unpack_from("<H", data, 0)[0]
        absolute_psi = raw_u16 / 10.0
        gauge_psi = absolute_psi - ATM_PSI
        gauge_kpa = gauge_psi * PSI_TO_KPA
        now: datetime = dt_util.utcnow()
        _LOGGER.debug(
            "%s: notify raw=%s abs=%.2f PSI gauge=%.2f PSI (%.1f kPa)",
            self.local_name,
            data.hex(),
            absolute_psi,
            gauge_psi,
            gauge_kpa,
        )
        self.async_set_updated_data(
            {
                "raw_hex": data.hex(),
                "raw_u16": raw_u16,
                "absolute_psi": absolute_psi,
                "gauge_psi": gauge_psi,
                "gauge_kpa": gauge_kpa,
                "rssi": self._last_rssi,
                "last_update": now,
            }
        )

    async def _async_watchdog(self) -> None:
        """Drop the link if notifies go silent — lets the sensor sleep and saves battery."""
        try:
            while self._connected:
                await asyncio.sleep(WATCHDOG_POLL_SEC)
                if not self._connected:
                    return
                silence = time.monotonic() - self._last_notify_monotonic
                if silence > NOTIFY_SILENCE_TIMEOUT_SEC:
                    _LOGGER.info(
                        "%s: %0.0fs since last notify, disconnecting to allow sleep",
                        self.local_name,
                        silence,
                    )
                    await self._async_disconnect()
                    return
        except asyncio.CancelledError:
            raise

    async def _async_disconnect(self) -> None:
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None
        if self._client is None:
            return
        try:
            if self._notify_char is not None:
                try:
                    await self._client.stop_notify(self._notify_char)
                except Exception:  # noqa: BLE001
                    pass
            await self._client.disconnect()
        finally:
            self._client = None
            self._notify_char = None
            self._connected = False
