"""Exercise a real isolated Home Assistant config flow and synthetic camera."""

import asyncio
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

from homeassistant import bootstrap, core, loader
from homeassistant.components import camera
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

logging.basicConfig(level=logging.WARNING)
if os.environ.get("NETVUE_TEST_DEBUG"):
    logging.getLogger("homeassistant.components.stream").setLevel(logging.DEBUG)


async def main():
    component = Path(os.environ["NETVUE_TEST_COMPONENT"])
    mediamtx = Path(os.environ["NETVUE_TEST_MEDIAMTX"])
    template = Path(os.environ["NETVUE_TEST_MEDIAMTX_CONFIG"])
    ffmpeg = Path(os.environ["NETVUE_TEST_FFMPEG"])
    root = Path(tempfile.mkdtemp(prefix="netvue-ha-synthetic-"))
    os.environ["PATH"] = f"{ffmpeg.parent}{os.pathsep}{os.environ['PATH']}"
    custom_components = root / "custom_components"
    custom_components.mkdir()
    shutil.copytree(component, custom_components / "netvue_local")
    port = 8554
    api_port = _free_port()
    config = root / "mediamtx.yml"
    stream_name = "netvue_192_168_10_2"
    shutil.copyfile(template, config)
    url = f"rtsp://127.0.0.1:{port}/{stream_name}"
    server = subprocess.Popen([str(mediamtx), str(config)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE)
    publisher = None
    hass = None
    api = None

    manager_cameras = {}
    start_requests = []
    reject_start = False

    async def bridge_handler(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        method, path, _ = request.split(b"\r\n", 1)[0].split(b" ", 2)
        body = b""
        for header in request.split(b"\r\n")[1:]:
            if header.lower().startswith(b"content-length:"):
                body = await reader.readexactly(int(header.split(b":", 1)[1]))
        parts = path.decode().split("/")
        host = parts[4] if len(parts) > 4 else ""
        if method == b"POST" and path == b"/api/v1/discovery":
            if body:
                raise RuntimeError("Discovery request must have no body")
            status = b"200 OK"
            body = json.dumps({"cameras": [
                {"host": "192.168.10.2"}, {"host": "192.168.10.3"},
            ]}).encode()
        elif method == b"POST" and path.endswith(b"/start"):
            start_requests.append(host)
            if reject_start:
                status = b"503 Service Unavailable"
                body = b""
            elif host in manager_cameras:
                manager_cameras[host]["state"] = "supervising"
                manager_cameras[host].pop("error", None)
                status = b"202 Accepted"
                body = json.dumps(manager_cameras[host]).encode()
            else:
                status = b"404 Not Found"
                body = b""
        elif method == b"POST":
            data = json.loads(body)
            host = data["host"]
            manager_cameras[host] = {
                **data, "rtsp_url": f"rtsp://127.0.0.1:8554/{data['stream_name']}",
                "state": "supervising",
            }
            status = b"202 Accepted"
            body = json.dumps(manager_cameras[host]).encode()
        elif method == b"GET" and host in manager_cameras:
            status = b"200 OK"
            body = json.dumps(manager_cameras[host]).encode()
        elif method == b"DELETE" and manager_cameras.pop(host, None) is not None:
            status = b"200 OK"
            body = b""
        else:
            status = b"404 Not Found"
            body = b""
        writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Type: application/json\r\n"
                     + f"Content-Length: {len(body)}\r\n".encode()
                     + b"Connection: close\r\n\r\n" + body)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    try:
        api = await asyncio.start_server(bridge_handler, "127.0.0.1", api_port)
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                if server.poll() is not None:
                    raise RuntimeError("Synthetic MediaMTX failed to start")
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Synthetic MediaMTX did not become ready")
        publisher = subprocess.Popen([
            str(ffmpeg), "-v", "error", "-re", "-f", "lavfi", "-i",
            "testsrc=size=64x64:rate=10", "-pix_fmt", "yuv420p", "-c:v", "libx264",
            "-preset", "ultrafast", "-tune", "zerolatency", "-g", "10",
            "-f", "rtsp", "-rtsp_transport", "tcp", url,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        hass = core.HomeAssistant(str(root))
        loader.async_setup(hass)
        hass.config.skip_pip = True
        if await bootstrap.async_from_config_dict({"homeassistant": {}}, hass) is None:
            raise RuntimeError("Isolated Home Assistant bootstrap failed")
        await hass.async_start()
        from custom_components.netvue_local import bridge_api, rtsp

        async_probe_stream = rtsp.async_probe_stream
        bridge_api.API_URL = f"http://127.0.0.1:{api_port}/api/v1"
        for _ in range(100):
            if publisher.poll() is not None:
                raise RuntimeError("Synthetic publisher failed to start")
            if await async_probe_stream(url):
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Synthetic H.264 RTSP stream unavailable")
        if await async_probe_stream(f"rtsp://127.0.0.1:{port}/missing"):
            raise RuntimeError("Unpublished synthetic path was reported available")
        manager = hass.config_entries.flow
        result = await manager.async_init("netvue_local", context={"source": "user"})
        if result["type"] != "menu" or result["step_id"] != "user":
            raise RuntimeError("Setup menu missing")
        flow_id = result["flow_id"]
        result = await manager.async_configure(flow_id, {"next_step_id": "discovery"})
        if result["type"] != "form" or result["step_id"] != "select" or manager_cameras:
            raise RuntimeError("Discovery did not offer cameras without starting a session")
        result = await manager.async_configure(flow_id, {
            "name": "Synthetic Netvue", "host": "192.168.10.2", "manual": False,
        })
        if result["type"] != "create_entry":
            raise RuntimeError("Config entry not created")
        entry = result["result"]
        await hass.async_block_till_done()
        registry = er.async_get(hass)

        def entity_id(config_entry, domain):
            suffix = {"sensor": "session", "binary_sensor": "rtsp"}.get(domain)
            entries = [
                entity.entity_id for entity in er.async_entries_for_config_entry(
                    registry, config_entry.entry_id
                ) if entity.domain == domain and (
                    suffix is None or entity.unique_id == f"{config_entry.entry_id}_{suffix}"
                )
            ]
            if len(entries) != 1:
                raise RuntimeError(f"Unexpected {domain} entities: {entries}")
            return entries[0]

        cameras = [entity_id(entry, "camera")]
        sensors = [entity_id(entry, "binary_sensor")]
        session_sensor = entity_id(entry, "sensor")
        restart_button = entity_id(entry, "button")
        if hass.states.get(sensors[0]).state != "on":
            raise RuntimeError("Synthetic stream availability was not detected")
        entity = camera.get_camera_from_entity_id(hass, cameras[0])
        print(f"camera features={entity.supported_features} stream_stills={entity.use_stream_for_stills} "
              f"state={hass.states.get(cameras[0]).state}", flush=True)
        if entity.camera_entity_capabilities.two_way_audio or entity.camera_capabilities.two_way_audio:
            raise RuntimeError("Synthetic camera incorrectly advertises talkback")
        entity.stream = await entity.async_create_stream()
        if entity.stream is None:
            raise RuntimeError("Home Assistant did not create a Stream")
        image = await asyncio.wait_for(
            entity.stream.async_get_image(wait_for_next_keyframe=True), 20
        )
        if image is None or not image.startswith(b"\xff\xd8"):
            print(f"Home Assistant stream diagnostics: {entity.stream.get_diagnostics()}",
                  flush=True)
            print(f"Synthetic publisher exit code: {publisher.poll()}", flush=True)
            if publisher.poll() is not None:
                print(publisher.stderr.read().decode(errors="replace"), flush=True)
            probe = subprocess.run(
                [str(ffmpeg), "-v", "error", "-rtsp_transport", "tcp", "-i", url,
                 "-frames:v", "1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=15,
            )
            print(f"Independent FFmpeg decode exit code: {probe.returncode}; "
                  f"stderr={probe.stderr.strip()}", flush=True)
            raise RuntimeError("Home Assistant camera preview did not produce a JPEG")

        result = await manager.async_init("netvue_local", context={"source": "user"})
        result = await manager.async_configure(result["flow_id"], {"next_step_id": "manual"})
        result = await manager.async_configure(result["flow_id"], {
            "name": "Second synthetic Netvue", "host": "192.168.10.3",
        })
        if result["type"] != "create_entry":
            raise RuntimeError("Second synthetic entry not created")
        second_entry = result["result"]
        await hass.async_block_till_done()
        second_sensor = entity_id(second_entry, "sensor")
        second_before = dict(manager_cameras["192.168.10.3"])
        manager_cameras["192.168.10.2"].update(
            state="failed", error="Camera authentication rejected"
        )
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        state = hass.states.get(session_sensor)
        if state.state != "failed" or state.attributes.get("error") != "Camera authentication rejected":
            raise RuntimeError("Failed session diagnostic was not displayed")
        if start_requests or hass.states.get(restart_button).state == "unavailable":
            raise RuntimeError("Failed session was restarted automatically or button is unavailable")
        reject_start = True
        try:
            await hass.services.async_call(
                "button", "press", {"entity_id": restart_button}, blocking=True
            )
        except HomeAssistantError:
            pass
        else:
            raise RuntimeError("Failed restart was not reported by Home Assistant")
        if manager_cameras["192.168.10.2"]["state"] != "failed":
            raise RuntimeError("Failed restart changed the session")
        reject_start = False
        await hass.services.async_call(
            "button", "press", {"entity_id": restart_button}, blocking=True
        )
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if start_requests != ["192.168.10.2", "192.168.10.2"]:
            raise RuntimeError("Restart did not target only the failed camera")
        if (hass.states.get(session_sensor).state != "supervising"
                or "error" in hass.states.get(session_sensor).attributes):
            raise RuntimeError("Restarted session diagnostic was not refreshed")
        if (manager_cameras["192.168.10.3"] != second_before
                or hass.states.get(second_sensor).state != "supervising"):
            raise RuntimeError("Restart changed the second camera session")

        api.close()
        await api.wait_closed()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if (hass.states.get(session_sensor).state != "unavailable"
                or hass.states.get(restart_button).state != "unavailable"
                or hass.states.get(sensors[0]).state != "on"
                or hass.states.get(cameras[0]).state == "unavailable"):
            raise RuntimeError("Manager outage incorrectly affected RTSP or session availability")
        api = await asyncio.start_server(bridge_handler, "127.0.0.1", api_port)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if hass.states.get(session_sensor).state != "supervising":
            raise RuntimeError("Session diagnostic did not recover after manager outage")

        options = hass.config_entries.options
        result = await options.async_init(entry.entry_id)
        if result["step_id"] != "init":
            raise RuntimeError("Options form missing")
        flow_id = result["flow_id"]
        result = await options.async_configure(flow_id, {"host": "192.168.10.2"})
        if result["type"] != "create_entry":
            raise RuntimeError("Options not saved")
        await hass.async_block_till_done()
        publisher.terminate()
        await asyncio.to_thread(publisher.wait, 5)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if hass.states.get(sensors[0]).state != "off" or hass.states.get(cameras[0]).state != "unavailable":
            raise RuntimeError("Stopped synthetic stream did not become unavailable")
        manager_cameras["192.168.10.2"].update(
            state="failed", error="Camera authentication rejected"
        )
        original_unload = type(hass.config_entries).async_forward_entry_unload

        async def fail_one_platform(entries, config_entry, platform):
            if platform == Platform.BINARY_SENSOR:
                return False
            return await original_unload(entries, config_entry, platform)

        with patch.object(type(hass.config_entries), "async_forward_entry_unload",
                          new=fail_one_platform):
            if await hass.config_entries.async_unload(entry.entry_id):
                raise RuntimeError("Partially failed platform unload was reported as successful")
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if (manager_cameras["192.168.10.2"]["state"] != "failed"
                or entry.state != ConfigEntryState.FAILED_UNLOAD
                or any(hass.states.get(entity_id(entry, domain)) is None
                       for domain in ("camera", "binary_sensor", "sensor", "button"))
                or hass.states.get(session_sensor).state != "failed"
                or start_requests != ["192.168.10.2", "192.168.10.2"]):
            raise RuntimeError("Partial unload lost the failed session or camera entity")

        from custom_components.netvue_local.bridge_api import BridgeError
        with patch("custom_components.netvue_local.async_stop_camera",
                   side_effect=BridgeError("synthetic manager failure")):
            if await hass.config_entries.async_unload(second_entry.entry_id):
                raise RuntimeError("Failed session delete was reported as successful")
        await hass.async_block_till_done()
        if (manager_cameras["192.168.10.3"] != second_before
                or second_entry.state != ConfigEntryState.FAILED_UNLOAD
                or any(hass.states.get(entity_id(second_entry, domain)) is None
                       for domain in ("camera", "binary_sensor", "sensor", "button"))):
            raise RuntimeError("Failed delete lost the session or restored entities")
        print("Isolated HA: config and options flows, entities, RTSP availability, Stream JPEG, "
              "failed-session diagnosis, isolated restart, offline transition and failed unload recovery verified",
              flush=True)
    finally:
        if hass is not None:
            await hass.async_stop(force=True)
        if publisher is not None:
            if publisher.poll() is None:
                publisher.terminate()
                await asyncio.to_thread(publisher.wait, 5)
            publisher.stderr.close()
        if server.poll() is None:
            server.terminate()
            await asyncio.to_thread(server.wait, 5)
        server.stderr.close()
        if api is not None:
            api.close()
            await api.wait_closed()
        shutil.rmtree(root)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    asyncio.run(main())
