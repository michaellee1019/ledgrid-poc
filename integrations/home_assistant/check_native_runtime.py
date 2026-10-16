"""Exercise the native integration in actual HA 2026.10.1, on loopback only.

uv run --python 3.14 --with homeassistant==2026.10.1 --with pyyaml python \
    integrations/home_assistant/check_native_runtime.py

The default protocol fixture isolates HA lifecycle/error behavior. --real-api-root
adds the real wall HTTP handlers and isolated fake controller from the API tests.
"""
import argparse
import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

from aiohttp import web
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol
from homeassistant.helpers import device_registry as dr, entity_registry as er

DOMAIN = "led_grid_wall"


class LoopbackWall:
    def __init__(self):
        self.writes = []
        self.reads = 0
        self.mode = "ok"
        self.identity = "test-installation"
        self.state = {
            "schema": "ledgrid.automation-status", "schema_version": 1,
            "installation_id": self.identity, "server_session_id": "server-session",
            "fresh": True, "controller_session_id": "controller-session",
            "controller_state_revision": 1, "observed_at": time.time(),
            "settings": {"power": True, "brightness": 255, "pace": 1, "animation_speed_scale": .3},
            "requested_scene": {"scene_digest": "retained"},
        }

    async def capabilities(self, request):
        return web.json_response({"schema": "ledgrid.automation-capabilities", "schema_version": 1,
                                  "installation_id": self.identity, "configuration_url": "/composer",
                                  "commands": ["set_device_state"]})

    async def status(self, request):
        self.reads += 1
        if self.mode == "unavailable":
            raise web.HTTPServiceUnavailable()
        self.state["observed_at"] = time.time() - (30 if self.mode == "stale" else 0)
        if self.mode == "malformed":
            return web.json_response({**self.state, "settings": None})
        self.state["installation_id"] = self.identity
        return web.json_response(self.state)

    async def command(self, request):
        payload = await request.json()
        self.writes.append(payload)
        assert payload["expected_server_session_id"] == self.state["server_session_id"]
        assert payload["expected_controller_session_id"] == self.state["controller_session_id"]
        assert payload["expected_controller_state_revision"] == self.state["controller_state_revision"]
        assert time.time() < payload["expires_at"] <= time.time() + 10
        if self.mode == "reject":
            raise web.HTTPConflict()
        if self.mode != "never_ack":
            for field in ("brightness", "power", "pace"):
                if field in payload:
                    self.state["settings"][field] = payload[field]
                    if field == "pace":
                        self.state["settings"]["animation_speed_scale"] = payload[field] * .3
            self.state["controller_state_revision"] += 1
        return web.json_response({"schema": "ledgrid.automation-command", "schema_version": 1,
                                  "request_id": "12345678-abcd-4123-8123-123456789abc", "command_id": len(self.writes), "state": "accepted"}, status=202)

    async def receipt(self, request):
        if self.mode == "never_ack":
            await asyncio.sleep(2)
        return web.json_response({"schema": "ledgrid.automation-command", "schema_version": 1,
                                  "request_id": "12345678-abcd-4123-8123-123456789abc", "command_id": len(self.writes),
                                  "state": "accepted" if self.mode == "never_ack" else "completed",
                                  "controller_session_id": self.state["controller_session_id"],
                                  "controller_state_revision": self.state["controller_state_revision"]})

    def app(self):
        app = web.Application()
        app.router.add_get('/api/v1/automation/capabilities', self.capabilities)
        app.router.add_get('/api/v1/automation/status', self.status)
        app.router.add_post('/api/v1/automation/commands', self.command)
        app.router.add_get('/api/v1/automation/commands/{request_id}', self.receipt)
        return app


async def serve(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"


async def native_hass(config_dir):
    shutil.copytree(Path(__file__).with_name("custom_components"), Path(config_dir) / "custom_components", ignore=shutil.ignore_patterns("__pycache__"))
    hass = HomeAssistant(config_dir)
    loader.async_setup(hass)
    result = await bootstrap.async_from_config_dict({"homeassistant": {
        "name": "Isolated native wall", "latitude": 40.7, "longitude": -74.,
        "elevation": 0, "unit_system": "metric", "time_zone": "America/New_York",
    }}, hass)
    assert result is hass
    await hass.async_start()
    return hass


async def setup_flow(hass, url):
    form = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert form["type"] == "form", form
    result = await hass.config_entries.flow.async_configure(form["flow_id"], {"url": url})
    await hass.async_block_till_done()
    return result


async def run_fake():
    wall = LoopbackWall()
    runner, url = await serve(wall.app())
    alternate, url2 = await serve(wall.app())
    with tempfile.TemporaryDirectory(prefix="ledgrid-native-ha-") as config_dir:
        hass = await native_hass(config_dir)
        try:
            invalid = await setup_flow(hass, "file:///tmp/wall")
            assert invalid["errors"] == {"url": "invalid_url"}, invalid
            setup = await setup_flow(hass, url)
            assert setup["type"] == "create_entry", setup
            entry = setup["result"]
            assert wall.writes == []
            # Every platform subscribes to the same two-second update, never its own poll.
            reads = wall.reads
            await asyncio.sleep(2.2)
            await hass.async_block_till_done()
            assert wall.reads == reads + 1, (reads, wall.reads)
            duplicate = await setup_flow(hass, url2)
            assert duplicate["type"] == "abort" and duplicate["reason"] == "already_configured", duplicate
            wall.identity = "second-installation"
            second = await setup_flow(hass, url2)
            assert second["type"] == "abort" and second["reason"] == "single_instance_allowed", second
            wall.identity = "test-installation"
            entities = er.async_get(hass)
            devices = dr.async_get(hass)
            grouped = er.async_entries_for_config_entry(entities, entry.entry_id)
            assert len(grouped) == 3, grouped
            assert len({item.device_id for item in grouped}) == 1
            device = devices.async_get(grouped[0].device_id)
            assert device.identifiers == {(DOMAIN, wall.identity)}
            assert device.configuration_url.startswith(url)
            light = hass.states.get("light.led_grid_wall")
            assert light.state == "on" and light.attributes["brightness"] == 255, light
            assert hass.states.get("number.led_grid_wall_pace").state == "1.0"
            coord = entry.runtime_data
            reads = wall.reads
            await coord.async_refresh()
            assert wall.reads == reads + 1, (reads, wall.reads)
            await hass.services.async_call("light", "turn_on", {"entity_id": "light.led_grid_wall", "brightness_pct": 10}, blocking=True)
            assert wall.writes[-1]["brightness"] == 26 and wall.writes[-1]["power"] is True
            assert hass.states.get("light.led_grid_wall").attributes["brightness"] == 26
            await hass.services.async_call("light", "turn_on", {"entity_id": "light.led_grid_wall", "brightness": 0}, blocking=True)
            assert wall.state["settings"]["power"] is False
            result = await hass.services.async_call(DOMAIN, "apply", {"entity_id": "light.led_grid_wall", "brightness": 0, "power": True}, blocking=True, return_response=True)
            assert result["success"] and wall.state["settings"]["power"] and wall.state["settings"]["brightness"] == 0, result
            await hass.services.async_call("number", "set_value", {"entity_id": "number.led_grid_wall_pace", "value": 1.25}, blocking=True)
            assert wall.state["settings"]["animation_speed_scale"] == .375
            assert hass.states.get("number.led_grid_wall_pace").state == "1.25"
            count = len(wall.writes)
            wall.state["settings"]["brightness"] = 17
            await coord.async_refresh()
            assert hass.states.get("light.led_grid_wall").attributes["brightness"] == 17
            for mode in ("stale", "unavailable", "malformed"):
                wall.mode = mode
                await coord.async_refresh()
                assert all(hass.states.get(item.entity_id).state == "unavailable" for item in grouped)
            wall.mode = "ok"
            await coord.async_refresh()
            assert len(wall.writes) == count
            for invalid_input in ({}, {"brightness": True}, {"brightness": -1}, {"power": "true"},
                                  {"multiplier": float("nan")}, {"multiplier": True},
                                  {"entity_id": "light.unrelated", "power": True}):
                try:
                    await hass.services.async_call(DOMAIN, "apply", invalid_input, blocking=True, return_response=True)
                except (HomeAssistantError, vol.Invalid):
                    pass
                else:
                    raise AssertionError(f"Accepted invalid action input: {invalid_input}")
                assert len(wall.writes) == count
            wall.mode = "reject"
            result = await hass.services.async_call(DOMAIN, "apply", {"brightness": 26}, blocking=True, return_response=True)
            assert result["success"] is False and wall.state["settings"]["brightness"] == 17, result
            wall.mode = "never_ack"
            started = time.monotonic()
            pending = asyncio.create_task(hass.services.async_call(DOMAIN, "apply", {"brightness": 26}, blocking=True, return_response=True))
            await asyncio.sleep(.3)
            assert hass.states.get("light.led_grid_wall").attributes["brightness"] == 17
            writes_before_busy = len(wall.writes)
            busy = await hass.services.async_call(DOMAIN, "apply", {"brightness": 30}, blocking=True, return_response=True)
            assert busy["success"] is False and len(wall.writes) == writes_before_busy
            result = await pending
            assert result["success"] is False and time.monotonic() - started < 14, result
            assert wall.state["settings"]["brightness"] == 17
            count = len(wall.writes)
            wall.mode = "ok"
            wall.identity = "different-wall"
            reconfigure = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "reconfigure", "entry_id": entry.entry_id})
            wrong = await hass.config_entries.flow.async_configure(reconfigure["flow_id"], {"url": url2})
            assert wrong["errors"] == {"base": "wrong_wall"}, wrong
            wall.identity = "test-installation"
            changed = await hass.config_entries.flow.async_configure(reconfigure["flow_id"], {"url": url2})
            assert changed["type"] == "abort" and changed["reason"] == "reconfigure_successful", changed
            await hass.async_block_till_done()
            assert entry.data["url"] == url2
            assert devices.async_get(grouped[0].device_id).configuration_url == url2 + "/composer"
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert not hass.services.has_service(DOMAIN, "apply")
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            assert len(er.async_entries_for_config_entry(entities, entry.entry_id)) == 3
            assert len(dr.async_entries_for_config_entry(devices, entry.entry_id)) == 1
            assert len(wall.writes) == count
            print("native HA 2026.10.1: flow, duplicate, reconfigure, device grouping, shared poll, light/pace, zero, failures, unload/reload, no replay passed", flush=True)
        finally:
            await hass.async_stop()
    await runner.cleanup()
    await alternate.cleanup()


async def run_real(api_root):
    """HA → actual HTTP routes → file IPC → actual controller → correlated receipt."""
    sys.path.insert(0, str(api_root))
    from animation.core.manager import AnimationManager, PreviewLEDController
    from ipc.control_channel import FileControlChannel
    from scripts.start_server import controller_status_payload, handle_command, process_control_command
    from web.app import AnimationWebInterface
    from web.starter_looks import get_starter

    with tempfile.TemporaryDirectory(prefix="ledgrid-native-real-api-") as fixture_dir:
        fixture = Path(fixture_dir)
        os.environ["LEDGRID_DATA_DIR"] = str(fixture / "personal_data")
        manager = AnimationManager(PreviewLEDController(33, 138), auto_start=False)
        manager._start_loop = lambda: None
        assert handle_command(manager, "start_scene", {"scene": get_starter("aurora")["scene"]})
        manager.set_output_brightness(91)
        channel = FileControlChannel(fixture / "control.json", fixture / "status.json")
        channel.write_status(controller_status_payload(manager))
        interface = AnimationWebInterface(channel, manager, project_root=fixture)
        interface.composer_presets.root = api_root
        commands = []
        pump_running = True

        async def pump():
            last_command_id = last_applied_command_id = 0
            while pump_running:
                for queued in channel.poll_commands():
                    commands.append(queued)
                    result = process_control_command(channel, manager, queued, last_applied_command_id=last_applied_command_id)
                    last_command_id = queued["command_id"]
                    if result["state"] == "completed":
                        last_applied_command_id = last_command_id
                channel.write_status({**controller_status_payload(manager), "last_command_id": last_command_id,
                                      "last_applied_command_id": last_applied_command_id})
                await asyncio.sleep(.025)

        async def proxy(request):
            body = await request.read()
            def dispatch():
                with interface.app.test_client() as client:
                    response = client.open(request.path_qs, method=request.method, data=body, content_type=request.content_type)
                    return response.status_code, response.get_data(), response.content_type
            status, content, content_type = await asyncio.to_thread(dispatch)
            return web.Response(status=status, body=content, headers={"Content-Type": content_type})

        app = web.Application()
        app.router.add_route("*", "/api/v1/automation/{path:.*}", proxy)
        runner, url = await serve(app)
        controller = asyncio.create_task(pump())
        with tempfile.TemporaryDirectory(prefix="ledgrid-native-real-ha-") as config_dir:
            hass = await native_hass(config_dir)
            try:
                setup = await setup_flow(hass, url)
                assert setup["type"] == "create_entry", setup
                entry = setup["result"]
                assert commands == []
                assert hass.states.get("light.led_grid_wall").attributes["brightness"] == 91
                original_scene = manager.get_scene_state()
                await hass.services.async_call("light", "turn_off", {"entity_id": "light.led_grid_wall"}, blocking=True)
                assert not manager.is_running
                events = []
                old_brightness = manager.set_output_brightness
                old_start = manager.start_scene
                def brightness(value):
                    events.append(("brightness", value))
                    return old_brightness(value)
                def start(*args, **kwargs):
                    events.append(("start", manager.output_brightness))
                    return old_start(*args, **kwargs)
                manager.set_output_brightness = brightness
                manager.start_scene = start
                result = await hass.services.async_call(DOMAIN, "apply", {"entity_id": "light.led_grid_wall", "brightness": 0, "power": True, "multiplier": 1.25}, blocking=True, return_response=True)
                assert result["success"], result
                assert manager.is_running and manager.output_brightness == 0
                assert manager.animation_speed_scale == .375
                assert events[0] == ("brightness", 0) and ("start", 0) in events, events
                assert manager.get_scene_state() == original_scene
                assert hass.states.get("light.led_grid_wall").attributes["brightness"] == 0
                assert hass.states.get("number.led_grid_wall_pace").state == "1.25"
                observation = hass.states.get("sensor.led_grid_wall_observation")
                assert observation.attributes["last_applied_command_id"] == result["command_id"]
                assert observation.attributes["active_identity"]["scene_digest"]
                count = len(commands)
                await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                assert len(commands) == count
                assert len({item.device_id for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)}) == 1
                assert all(item["data"].get("_controller_guard") for item in commands)
                print("native HA + real wall API/file IPC/controller: acknowledgement, brightness-before-start, retained scene, zero, pace, reload/no replay passed", flush=True)
            finally:
                await hass.async_stop()
        pump_running = False
        await controller
        await runner.cleanup()
        manager.stop_animation()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-api-root", type=Path)
    args = parser.parse_args()
    asyncio.run(run_real(args.real_api_root.resolve()) if args.real_api_root else run_fake())
