"""Scorebot v5 central over BlueZ D-Bus; no score retries or hardware in render."""
import asyncio
import json
import threading
import time

SERVICE = "c6a8619e-2f9d-46bc-9a23-bb9c89a519be"
IDENTITY, UPLINK, DOWNLINK, PROTOCOL, PAIRING = [
    f"c6a861{value}-2f9d-46bc-9a23-bb9c89a519be" for value in ("9f", "a0", "a1", "a2", "a3")
]
DEVICE = "org.bluez.Device1"
CHARACTERISTIC = "org.bluez.GattCharacteristic1"
PROPERTIES = "org.freedesktop.DBus.Properties"
MANAGER = "org.freedesktop.DBus.ObjectManager"
HEARTBEAT = 1.5
MAX_MESSAGE = 512
AD_MANAGER = "org.bluez.LEAdvertisingManager1"


def advertisement(packet):
    from dbus_next import PropertyAccess, Variant
    from dbus_next.service import ServiceInterface, dbus_property, method

    class DiscoveryAdvertisement(ServiceInterface):
        def __init__(self):
            super().__init__("org.bluez.LEAdvertisement1")
            self.packet, self.active = packet, False

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":  # noqa: F821
            return "peripheral"  # Keep the initiator's identity; broadcast switches NRPA during scanning.

        @dbus_property(access=PropertyAccess.READ)
        def ManufacturerData(self) -> "a{qv}":  # noqa: F722
            return {0xFFFF: Variant("ay", self.packet[2:])}

        @method()
        def Release(self):
            self.active = False

    return DiscoveryAdvertisement()


def decode_message(raw, protocol):
    if not isinstance(raw, (bytes, bytearray)) or not 0 < len(raw) <= MAX_MESSAGE:
        raise ValueError("Invalid Scorebot message size")
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON field")
        return result
    data = json.loads(raw, object_pairs_hook=unique)
    if type(data) is not dict or type(data.get("protocol")) is not int or data["protocol"] != protocol:
        raise ValueError("Invalid Scorebot protocol")
    kind = data.get("type")
    fields = {"player": {"score", "turnPassed", "operationId", "game"},
              "activity": {"game"}, "status": {"state"},
              "control": {"game", "term", "op", "command", "player", "value", "pass"}}
    identity = "from" if kind == "control" else "fromNodeId"
    if type(kind) is not str or kind not in fields or set(data) != fields[kind] | {"type", "protocol", identity}:
        raise ValueError("Invalid Scorebot fields")
    bounds = {"fromNodeId": (1, 0xFFFFFFFF), "game": (0, 0xFFFFFFFF),
              "operationId": (1, 0xFFFFFFFF), "score": (-0x80000000, 0x7FFFFFFF),
              "term": (0, 0xFFFFFFFF), "command": (0, 2), "player": (0, 3), "value": (-0x80000000, 0x7FFFFFFF),
              "from": (1, 0xFFFFFFFF), "op": (1, 0xFFFFFFFF)}
    for key, (low, high) in bounds.items():
        if key in data and (type(data[key]) is not int or not low <= data[key] <= high):
            raise ValueError("Invalid Scorebot integer")
    if kind in ("player", "control") and type(data["pass" if kind == "control" else "turnPassed"]) is not bool:
        raise ValueError("turnPassed must be boolean")
    if kind == "status" and data["state"] != "sleep":
        raise ValueError("Invalid sleep notice")
    if kind == "control" and data["command"] == 2 and not 0 <= data["value"] <= 2:
        raise ValueError("Invalid controller lobby mode")
    return data


class BlueZCentral:
    def __init__(self, session, identity):
        self.session = session
        self.identity = identity
        self.error = ""
        self._peer_errors = {}
        self.links = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="scorebot-ble", daemon=True)
        self._thread.start()

    @property
    def error(self):
        return self._fatal_error or "; ".join(self._peer_errors.values())

    @error.setter
    def error(self, value):
        self._fatal_error = value

    def close(self):
        self._stop.set()
        self._thread.join(timeout=12)
        if self._thread.is_alive():
            raise RuntimeError("Scorebot BLE did not stop; authority remains owned")

    def _run(self):
        try:
            asyncio.run(self.run())
        except Exception as error:
            self.error = "Scorebot BLE: " + str(error)

    async def call(self, path, interface, member, signature="", body=None, destination="org.bluez"):
        from dbus_next import Message, MessageType
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination=destination, path=path, interface=interface,
            member=member, signature=signature, body=body or [],
        )), 4)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(reply.error_name + ": " + str(reply.body))
        return reply.body

    async def properties(self, path, interface):
        values, = await self.call(path, PROPERTIES, "GetAll", "s", [interface])
        return {key: value.value for key, value in values.items()}

    async def objects(self):
        values, = await self.call("/", MANAGER, "GetManagedObjects")
        return {path: {name: {key: value.value for key, value in props.items()}
                       for name, props in interfaces.items()} for path, interfaces in values.items()}

    def signal(self, message):
        from dbus_next import MessageType
        if (message.message_type != MessageType.SIGNAL or message.interface != PROPERTIES
                or message.sender != self.owner):
            return
        interface, changed, _invalidated = message.body
        for path, link in self.links.items():
            if message.path == path and interface == DEVICE and "Connected" in changed:
                link["connected"] = changed["Connected"].value
            if message.path == link.get("uplink") and interface == CHARACTERISTIC and "Value" in changed:
                try:
                    link["queue"].put_nowait(bytes(changed["Value"].value))
                except asyncio.QueueFull:
                    link["connected"] = False
                    self._peer_errors[path] = "Scorebot input queue full; reconnect to resynchronize"

    async def run(self):
        from dbus_next import BusType, Variant
        from dbus_next.aio import MessageBus
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        adapter = "/org/bluez/" + self.identity["adapter"]
        tasks = {}
        retry = {}
        scanning = False
        advert = advertisement(self.session.discovery())
        advert_path = "/org/scorebot/discovery"
        try:
            props = await self.properties(adapter, "org.bluez.Adapter1")
            if not props.get("Powered"):
                raise ValueError("Scorebot BLE adapter is powered off")
            self.bus.export(advert_path, advert)
            self.owner, = await self.call("/org/freedesktop/DBus", "org.freedesktop.DBus",
                                          "GetNameOwner", "s", ["org.bluez"],
                                          destination="org.freedesktop.DBus")
            self.bus.add_message_handler(self.signal)
            await self.call("/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
                            ["type='signal',sender='org.bluez',interface='" + PROPERTIES + "'"],
                            destination="org.freedesktop.DBus")
            await self.call(adapter, "org.bluez.Adapter1", "SetDiscoveryFilter", "a{sv}",
                            [{"Transport": Variant("s", "le")}])
            await self.call(adapter, "org.bluez.Adapter1", "StartDiscovery")
            scanning = True
            while not self._stop.is_set() and not self.session.error:
                packet = self.session.discovery()
                if not advert.active or advert.packet != packet:
                    if advert.active:
                        await self.call(adapter, AD_MANAGER, "UnregisterAdvertisement", "o", [advert_path])
                    advert.packet = packet
                    await self.call(adapter, AD_MANAGER, "RegisterAdvertisement", "oa{sv}", [advert_path, {}])
                    advert.active = True
                for path, task in list(tasks.items()):
                    if task.done():
                        task.result()
                        del tasks[path]
                        retry[path] = time.monotonic() + 10
                objects = await self.objects()
                for path, interfaces in objects.items():
                    device = interfaces.get(DEVICE, {})
                    data = device.get("ManufacturerData", {}).get(0xFFFF)
                    offered = b"" if data is None else b"\xff\xff" + bytes(data.value)
                    if (path.startswith(adapter + "/") and len(offered) == 22 and offered[:5] == packet[:5]
                            and offered[9:17] == packet[9:17] and path not in tasks
                            and time.monotonic() >= retry.get(path, 0) and len(tasks) < 9):
                        tasks[path] = asyncio.create_task(self.peer(path))
                await asyncio.sleep(0.5)
        finally:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            try:
                if scanning:
                    await self.call(adapter, "org.bluez.Adapter1", "StopDiscovery")
                if advert.active:
                    await self.call(adapter, AD_MANAGER, "UnregisterAdvertisement", "o", [advert_path])
            finally:
                self.bus.disconnect()

    async def peer(self, path):
        from dbus_next import Variant
        token = None
        link = {"connected": True, "queue": asyncio.Queue(maxsize=8)}
        self.links[path] = link
        try:
            await self.call(path, DEVICE, "Connect")
            deadline = time.monotonic() + 8
            while not (await self.properties(path, DEVICE)).get("ServicesResolved"):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Scorebot service discovery timed out")
                await asyncio.sleep(0.1)
            objects = await self.objects()
            services = {name for name, interfaces in objects.items() if
                        interfaces.get("org.bluez.GattService1", {}).get("Device") == path and
                        interfaces["org.bluez.GattService1"]["UUID"] == SERVICE}
            chars = {props["UUID"]: name for name, interfaces in objects.items()
                     if (props := interfaces.get(CHARACTERISTIC, {})).get("Service") in services}
            node, = await self.call(chars[IDENTITY], CHARACTERISTIC, "ReadValue", "a{sv}", [{}])
            protocol, = await self.call(chars[PROTOCOL], CHARACTERISTIC, "ReadValue", "a{sv}", [{}])
            packet, = await self.call(chars[PAIRING], CHARACTERISTIC, "ReadValue", "a{sv}", [{}])
            if len(node) != 4 or len(protocol) != 2:
                raise ValueError("Invalid Scorebot GATT identity size")
            device = await self.properties(path, DEVICE)
            if device["AddressType"] not in ("public", "random"):
                raise ValueError("Unknown Scorebot transport address type")
            token = self.session.admit(int.from_bytes(node, "little"), int.from_bytes(protocol, "little"),
                                       int(device["AddressType"] == "public"),
                                       int(device["Address"].replace(":", ""), 16), bytes(packet))
            if token is None:
                raise ValueError("Scorebot peer rejected by identity or frozen roster")
            link["uplink"] = chars[UPLINK]
            await self.call(chars[UPLINK], CHARACTERISTIC, "StartNotify")
            last = None
            heartbeat = 0
            while link["connected"] and not self._stop.is_set():
                peer = self.session.peers.get(token)
                state = self.session.latest()
                if peer is None or (peer.player < 4 and state.started and not state.roster & (1 << peer.player)):
                    break
                acknowledge = not link["queue"].empty()
                if acknowledge:
                    self.session.receive(token, link["queue"].get_nowait())
                wire = self.session.wire()
                if acknowledge or wire != last or time.monotonic() >= heartbeat:
                    if len(wire) > MAX_MESSAGE:
                        raise ValueError("Scorebot snapshot exceeds wire limit")
                    # Include GATT round-trip time within the heartbeat period.
                    heartbeat = time.monotonic() + HEARTBEAT
                    await self.call(chars[DOWNLINK], CHARACTERISTIC, "WriteValue", "aya{sv}",
                                    [wire, {"type": Variant("s", "request")}])
                    if last is None and link["connected"]:
                        self._peer_errors.pop(path, None)
                    last = wire
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._peer_errors[path] = "Scorebot " + path.split("/")[-1] + ": " + str(error)
        finally:
            self.links.pop(path, None)
            try:
                if token is not None:
                    self.session.disconnect(token)
            finally:
                try:
                    await self.call(path, DEVICE, "Disconnect")
                except Exception:
                    pass
