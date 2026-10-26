"""LAN discovery and user-approved Drop Air pairing.

Discovery packets intentionally contain no session keys or access codes. The
only way to receive a transfer URL is to ask the discovered host and have its
local user approve the request.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import socket
import sys
import threading
import time
import uuid
from collections import defaultdict, deque


DISCOVERY_SERVICE = "_dropair._tcp.local."
DISCOVERY_PROTOCOL = "1"
BLE_SERVICE_UUID = uuid.UUID("70d75f3a-8c65-4f34-91d2-e2e8842b9781")
PEER_TTL_SECONDS = 12
BLE_PEER_TTL_SECONDS = 10
PAIR_REQUEST_TTL_SECONDS = 120
PAIR_RATE_WINDOW_SECONDS = 60
PAIR_MAX_PER_IP_PER_WINDOW = 4
PAIR_MAX_PENDING = 24
_SAFE_ID = re.compile(r"^[0-9a-f]{16,32}$")


def clean_device_name(value: object, fallback: str = "Drop Air device") -> str:
    name = "".join(char for char in str(value or "") if char.isprintable()).strip()
    return name[:32] or fallback


def private_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and address.is_private and not address.is_loopback


def route_ipv4() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        address = probe.getsockname()[0]
        return address if private_ipv4(address) else "0.0.0.0"
    except OSError:
        return "0.0.0.0"
    finally:
        probe.close()


class PairingRequests:
    """Short-lived incoming requests gated by host-side approval."""

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._requests: dict[str, dict] = {}
        self._attempts: dict[str, deque[float]] = defaultdict(deque)

    def _prune_locked(self, now: float) -> None:
        for request_id, item in list(self._requests.items()):
            if item["expires_at"] <= now:
                del self._requests[request_id]
        for address, attempts in list(self._attempts.items()):
            while attempts and attempts[0] <= now - PAIR_RATE_WINDOW_SECONDS:
                attempts.popleft()
            if not attempts:
                del self._attempts[address]

    def create(self, address: str, payload: object) -> tuple[dict | None, str | None]:
        if not isinstance(payload, dict):
            return None, "invalid request"
        name = clean_device_name(payload.get("device_name"))
        device_id = str(payload.get("device_id", "")).lower()
        if not _SAFE_ID.fullmatch(device_id):
            return None, "invalid device"
        try:
            if not private_ipv4(address):
                return None, "pairing is only available on a private local network"
        except TypeError:
            return None, "invalid network address"

        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            attempts = self._attempts[address]
            if len(attempts) >= PAIR_MAX_PER_IP_PER_WINDOW:
                return None, "too many recent connection requests"
            if len(self._requests) >= PAIR_MAX_PENDING:
                return None, "the host has too many pending requests"
            if any(item["address"] == address and item["device_id"] == device_id for item in self._requests.values()):
                return None, "a request from this device is already waiting for approval"
            attempts.append(now)
            request_id = secrets.token_hex(24)
            item = {
                "id": request_id,
                "device_id": device_id,
                "device_name": name,
                "address": address,
                "created_at": now,
                "expires_at": now + PAIR_REQUEST_TTL_SECONDS,
                "decision": "pending",
                "share_url": "",
            }
            self._requests[request_id] = item
            return self._public(item), None

    @staticmethod
    def _public(item: dict) -> dict:
        return {
            "id": item["id"],
            "device_id": item["device_id"],
            "device_name": item["device_name"],
            "address": item["address"],
            "created_at": int(item["created_at"]),
            "expires_at": int(item["expires_at"]),
            "decision": item["decision"],
        }

    def pending(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            return [self._public(item) for item in self._requests.values() if item["decision"] == "pending"]

    def decide(self, request_id: str, accepted: bool, share_url: str = "") -> bool:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            item = self._requests.get(request_id)
            if not item or item["decision"] != "pending":
                return False
            item["decision"] = "accepted" if accepted else "declined"
            item["share_url"] = share_url if accepted else ""
            return True

    def status(self, address: str, request_id: str) -> dict | None:
        now = self._clock()
        with self._lock:
            self._prune_locked(now)
            item = self._requests.get(request_id)
            if not item or item["address"] != address:
                return None
            result = {"decision": item["decision"]}
            if item["decision"] == "accepted":
                result["share_url"] = item["share_url"]
            return result


class PeerDiscovery:
    """mDNS discovery for local Wi-Fi peers; never sends credentials."""

    def __init__(self, device_id: str, settings_getter):
        self.device_id = device_id
        self._settings_getter = settings_getter
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._port = 0
        self._peers: dict[str, dict] = {}
        self._ble_seen: dict[str, float] = {}
        self._bluetooth_advertising = False
        self._status = "Not started"

    def start(self, port: int) -> None:
        self._port = int(port)
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        with self._lock:
            self._status = "Searching local Wi-Fi" + (" and Bluetooth" if sys.platform == "win32" else "")
        self._thread = threading.Thread(target=self._run, name="DropAirDiscovery", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _accept_service(self, info) -> bool:
        try:
            properties = info.properties
            device_id = properties.get(b"id", b"").decode("ascii").lower()
            protocol = properties.get(b"protocol", b"").decode("ascii")
            name = properties.get(b"name", b"").decode("utf-8", errors="replace")
            addresses = [socket.inet_ntoa(address) for address in info.addresses if len(address) == 4]
            address = next((item for item in addresses if private_ipv4(item)), "")
            port = int(info.port)
            if (
                protocol != DISCOVERY_PROTOCOL
                or not _SAFE_ID.fullmatch(device_id)
                or device_id == self.device_id
                or not address
                or not 1024 <= port <= 65535
            ):
                return False
            peer = {
                "device_id": device_id,
                "device_name": clean_device_name(name),
                "address": address,
                "port": port,
                "transports": ["Wi-Fi", "Bluetooth"] if time.time() - self._ble_seen.get(device_id[:8], 0) <= BLE_PEER_TTL_SECONDS else ["Wi-Fi"],
                "last_seen": time.time(),
            }
        except (AttributeError, OSError, TypeError, ValueError):
            return False
        with self._lock:
            self._peers[device_id] = peer
        return True

    def add_service(self, zeroconf, service_type: str, name: str) -> None:
        info = zeroconf.get_service_info(service_type, name, timeout=750)
        if info:
            self._accept_service(info)

    def update_service(self, zeroconf, service_type: str, name: str) -> None:
        self.add_service(zeroconf, service_type, name)

    def remove_service(self, _zeroconf, _service_type: str, name: str) -> None:
        suffix = name.split(".", 1)[0]
        device_id = suffix.lower()
        if _SAFE_ID.fullmatch(device_id):
            with self._lock:
                self._peers.pop(device_id, None)

    def _mark_bluetooth_peer(self, short_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{8}", short_id) or short_id == self.device_id[:8]:
            return
        now = time.time()
        with self._lock:
            self._ble_seen[short_id] = now
            for device_id, peer in self._peers.items():
                if device_id.startswith(short_id) and "Bluetooth" not in peer["transports"]:
                    peer["transports"].append("Bluetooth")
                    peer["last_seen"] = now

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            for short_id, last_seen in list(self._ble_seen.items()):
                if now - last_seen > BLE_PEER_TTL_SECONDS:
                    del self._ble_seen[short_id]
            for device_id, peer in list(self._peers.items()):
                if now - peer["last_seen"] > PEER_TTL_SECONDS:
                    del self._peers[device_id]
                elif device_id[:8] not in self._ble_seen and "Bluetooth" in peer["transports"]:
                    peer["transports"].remove("Bluetooth")
            peers = [
                {key: value for key, value in peer.items() if key != "last_seen"}
                for peer in sorted(self._peers.values(), key=lambda item: item["device_name"].lower())
            ]
            status = self._status
            bluetooth_advertising = self._bluetooth_advertising
        return {
            "status": status,
            "discoverable": bool(self._settings_getter().get("discoverable")),
            "bluetooth_advertising": bluetooth_advertising,
            "peers": peers,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            zeroconf = None
            browser = None
            ble_publisher = None
            ble_watcher = None
            registered = None
            registered_signature = None
            try:
                from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, Zeroconf

                local_address = route_ipv4()
                if local_address != "0.0.0.0":
                    zeroconf = Zeroconf(ip_version=IPVersion.V4Only, interfaces=[local_address])
                else:
                    zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
                browser = ServiceBrowser(zeroconf, DISCOVERY_SERVICE, self)
                bluetooth_available = False
                if sys.platform == "win32":
                    try:
                        from winrt.windows.devices.bluetooth.advertisement import (
                            BluetoothLEAdvertisement,
                            BluetoothLEAdvertisementPublisher,
                            BluetoothLEAdvertisementWatcher,
                            BluetoothLEAdvertisementPublisherStatus,
                            BluetoothLEScanningMode,
                        )

                        advertisement = BluetoothLEAdvertisement()
                        advertisement.local_name = f"DA-{self.device_id[:8]}"
                        advertisement.service_uuids.append(BLE_SERVICE_UUID)
                        ble_publisher = BluetoothLEAdvertisementPublisher(advertisement)
                        ble_watcher = BluetoothLEAdvertisementWatcher()
                        ble_watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE

                        def on_ble_received(_sender, event_args):
                            try:
                                incoming = event_args.advertisement
                                if BLE_SERVICE_UUID not in incoming.service_uuids:
                                    return
                                match = re.fullmatch(r"DA-([0-9a-fA-F]{8})", incoming.local_name or "")
                                if match:
                                    self._mark_bluetooth_peer(match.group(1).lower())
                            except (AttributeError, OSError, TypeError):
                                return

                        ble_watcher.add_received(on_ble_received)
                        ble_watcher.start()
                        bluetooth_available = True
                    except Exception:
                        ble_watcher = None
                        ble_publisher = None
                with self._lock:
                    self._status = "Searching local Wi-Fi" + (" and Bluetooth" if bluetooth_available else "")
                while not self._stop.is_set():
                    settings = self._settings_getter()
                    local_address = route_ipv4()
                    name = clean_device_name(settings.get("device_name"))
                    should_advertise = bool(settings.get("discoverable")) and local_address != "0.0.0.0"
                    signature = (name, local_address, self._port) if should_advertise else None
                    if ble_publisher is not None:
                        try:
                            if should_advertise and ble_publisher.status in {
                                BluetoothLEAdvertisementPublisherStatus.CREATED,
                                BluetoothLEAdvertisementPublisherStatus.STOPPED,
                            }:
                                ble_publisher.start()
                            elif not should_advertise and ble_publisher.status in {
                                BluetoothLEAdvertisementPublisherStatus.STARTED,
                                BluetoothLEAdvertisementPublisherStatus.WAITING,
                            }:
                                ble_publisher.stop()
                        except Exception:
                            pass
                        try:
                            from winrt.windows.devices.bluetooth.advertisement import BluetoothLEAdvertisementPublisherStatus

                            bluetooth_advertising = (
                                ble_publisher.status == BluetoothLEAdvertisementPublisherStatus.STARTED
                            )
                        except Exception:
                            bluetooth_advertising = False
                        with self._lock:
                            self._bluetooth_advertising = bluetooth_advertising
                    if signature != registered_signature:
                        if registered is not None:
                            try:
                                zeroconf.unregister_service(registered)
                            except OSError:
                                pass
                            registered = None
                        registered_signature = signature
                        if signature:
                            name, local_address, port = signature
                            service_name = f"{self.device_id}._dropair._tcp.local."
                            registered = ServiceInfo(
                                DISCOVERY_SERVICE,
                                service_name,
                                addresses=[socket.inet_aton(local_address)],
                                port=port,
                                properties={
                                    b"protocol": DISCOVERY_PROTOCOL.encode("ascii"),
                                    b"id": self.device_id.encode("ascii"),
                                    b"name": name.encode("utf-8"),
                                },
                                server=f"dropair-{self.device_id[:8]}.local.",
                            )
                            zeroconf.register_service(registered)
                    self._stop.wait(1)
            except Exception as exc:
                with self._lock:
                    self._status = f"Wi-Fi discovery unavailable: {str(exc)[:140]}"
                self._stop.wait(5)
            finally:
                with self._lock:
                    self._bluetooth_advertising = False
                if ble_publisher:
                    try:
                        ble_publisher.stop()
                    except Exception:
                        pass
                if ble_watcher:
                    try:
                        ble_watcher.stop()
                    except Exception:
                        pass
                if zeroconf:
                    try:
                        if registered:
                            zeroconf.unregister_service(registered)
                        zeroconf.close()
                    except Exception:
                        pass
        with self._lock:
            self._status = "Stopped"
