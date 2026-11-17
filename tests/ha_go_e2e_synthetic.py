"""Exercise HA, the real Go manager and a synthetic Netvue camera in isolation."""

import asyncio
import ipaddress
import json
import logging
import os
import shutil
import socket
import struct
import subprocess
import tempfile
from pathlib import Path

from aiohttp import ClientSession
from homeassistant import bootstrap, core, loader
from homeassistant.components import camera
from homeassistant.helpers import entity_registry as er

logging.basicConfig(level=logging.WARNING)


def packet(kind: int, body: bytes) -> bytes:
    return struct.pack("<I8xHH", len(body), 0, kind) + body


def command(unit: dict) -> bytes:
    return packet(200, json.dumps([unit], separators=(",", ":")).encode())


async def read_command(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(16)
    size, flag, kind = struct.unpack("<I8xHH", header)
    if flag or kind != 200 or not 0 < size < 1048576:
        raise RuntimeError("Unexpected synthetic camera command header")
    units = json.loads(await reader.readexactly(size))
    if not isinstance(units, list) or len(units) != 1:
        raise RuntimeError("Unexpected synthetic camera command")
    return units[0]


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        size, flag, kind = struct.unpack("<I8xHH", data[:16])
        units = json.loads(data[16:])
        if flag or kind != 200 or size != len(data) - 16 or units[0].get("Q") != 8:
            raise RuntimeError("Unexpected discovery request")
        self.transport.sendto(command({
            "A": 8, "V": 2, "C": 0,
            "P": ["synthetic", "synthetic", self.host, self.port, "synthetic-key"],
        }), address)


async def wait_port(port: int, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Local test process exited: {process.returncode}")
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise RuntimeError(f"Local test port {port} did not become ready")


async def main():
    component = Path(os.environ["NETVUE_TEST_COMPONENT"])
    mediamtx = Path(os.environ["NETVUE_TEST_MEDIAMTX"])
    config = Path(os.environ["NETVUE_TEST_MEDIAMTX_CONFIG"])
    bridge = Path(os.environ["NETVUE_TEST_GO_BRIDGE"])
    host = socket.gethostbyname(socket.gethostname())
    if not any(ipaddress.ip_address(host) in ipaddress.ip_network(network) for network in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
        raise RuntimeError("The synthetic camera must use this container's private IPv4")

    video = subprocess.run([
        "/usr/bin/ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "testsrc=size=64x64:rate=10", "-frames:v", "1", "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
        "-x264-params", "repeat-headers=1", "-f", "h264", "pipe:1",
    ], check=True, capture_output=True, timeout=20).stdout
    if not video:
        raise RuntimeError("Synthetic H.264 generation produced no frame")

    with tempfile.TemporaryDirectory(prefix="netvue-ha-go-") as directory:
        root = Path(directory)
        custom_components = root / "custom_components"
        custom_components.mkdir()
        shutil.copytree(component, custom_components / "netvue_local")
        server = subprocess.Popen([str(mediamtx), str(config)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE)
        bridge_process = None
        hass = None
        tcp = None
        udp = None
        writers = set()
        login_count = 0

        async def camera_handler(reader, writer):
            nonlocal login_count
            writers.add(writer)
            try:
                login = await read_command(reader)
                if login.get("Q") != 1:
                    raise RuntimeError("Expected synthetic login")
                login_count += 1
                rejected = login_count == 1
                writer.write(command({"A": 1, "V": 1, "C": 5 if rejected else 0}))
                await writer.drain()
                if rejected:
                    return
                live = await read_command(reader)
                if live.get("Q") != 3:
                    raise RuntimeError("Expected synthetic LIVE request")
                writer.write(command({"A": 3, "V": 2, "C": 0}))
                await writer.drain()
                timestamp = 0
                while True:
                    writer.write(packet(201, b"\x10" + struct.pack("<I", timestamp) + video))
                    await writer.drain()
                    timestamp += 100
                    await asyncio.sleep(0.1)
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writers.discard(writer)
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

        try:
            await wait_port(8554, server)
            tcp = await asyncio.start_server(camera_handler, host, 0)
            camera_port = tcp.sockets[0].getsockname()[1]
            udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: Discovery(host, camera_port), local_addr=(host, 36868)
            )
            bridge_process = subprocess.Popen(
                [str(bridge), "serve", "--source", "127.0.0.1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            await wait_port(8765, bridge_process)
            hass = core.HomeAssistant(str(root))
            loader.async_setup(hass)
            hass.config.skip_pip = True
            if await bootstrap.async_from_config_dict({"homeassistant": {}}, hass) is None:
                raise RuntimeError("Isolated Home Assistant bootstrap failed")
            await hass.async_start()
            flow = hass.config_entries.flow
            result = await flow.async_init("netvue_local", context={"source": "user"})
            result = await flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
            result = await flow.async_configure(
                result["flow_id"], {"name": "Synthetic Netvue", "host": host}
            )
            if result["type"] != "create_entry":
                raise RuntimeError("Synthetic Home Assistant entry was not created")
            entry = result["result"]
            await hass.async_block_till_done()
            registry = er.async_get(hass)

            def entity_id(domain):
                entities = [item.entity_id for item in er.async_entries_for_config_entry(
                    registry, entry.entry_id) if item.domain == domain]
                if len(entities) != 1:
                    raise RuntimeError(f"Expected one {domain} entity, got {entities}")
                return entities[0]

            session_sensor = entity_id("sensor")
            restart = entity_id("button")
            stream_sensor = entity_id("binary_sensor")
            camera_entity = entity_id("camera")

            async def wait_state(entity, wanted):
                for _ in range(100):
                    await entry.runtime_data.async_refresh()
                    await hass.async_block_till_done()
                    state = hass.states.get(entity)
                    if state is not None and state.state == wanted:
                        return state
                    await asyncio.sleep(0.1)
                raise RuntimeError(f"{entity} did not become {wanted}")

            failed = await wait_state(session_sensor, "failed")
            if failed.attributes.get("error") != "Camera authentication rejected":
                raise RuntimeError("Home Assistant did not show the Go manager diagnostic")
            if login_count != 1:
                raise RuntimeError("A failed session was retried without an explicit request")
            await hass.services.async_call("button", "press", {"entity_id": restart}, blocking=True)
            await wait_state(session_sensor, "supervising")
            await wait_state(stream_sensor, "on")
            if login_count != 2:
                raise RuntimeError("Explicit restart did not open exactly one new session")
            entity = camera.get_camera_from_entity_id(hass, camera_entity)
            entity.stream = await entity.async_create_stream()
            if entity.stream is None:
                raise RuntimeError("Home Assistant did not create its video stream")
            image = await asyncio.wait_for(entity.stream.async_get_image(
                wait_for_next_keyframe=True), 20)
            if not image or not image.startswith(b"\xff\xd8"):
                raise RuntimeError("Home Assistant did not decode a synthetic JPEG")
            if not await hass.config_entries.async_unload(entry.entry_id):
                raise RuntimeError("Synthetic Home Assistant entry failed to unload")
            async with ClientSession() as session:
                async with session.get(f"http://127.0.0.1:8765/api/v1/cameras/{host}") as response:
                    if response.status != 404:
                        raise RuntimeError("Go manager retained the unloaded camera")
            print("HA -> Go manager -> synthetic Netvue -> MediaMTX -> HA JPEG: "
                  "failure diagnostic, explicit restart and unload verified", flush=True)
        finally:
            if hass is not None:
                await hass.async_stop(force=True)
            for writer in writers.copy():
                writer.close()
            if udp is not None:
                udp.close()
            if tcp is not None:
                tcp.close()
                await tcp.wait_closed()
            for process in (bridge_process, server):
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        await asyncio.to_thread(process.wait, 10)
                    process.stderr.close()


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), 120))
