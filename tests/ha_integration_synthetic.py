"""Exercise a real isolated Home Assistant config flow and synthetic camera."""

import asyncio
import importlib
import logging
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from homeassistant import bootstrap, core, loader
from homeassistant.components import camera

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
    port = _free_port()
    api_port = _free_port()
    config = root / "mediamtx.yml"
    config_text = template.read_text(encoding="utf-8")
    stream_name = "netvue_192_168_10_2"
    config_text = config_text.replace("127.0.0.1:8554", f"127.0.0.1:{port}")
    config_text = config_text.replace("netvue2:", f"{stream_name}:")
    config.write_text(config_text, encoding="utf-8")
    url = f"rtsp://127.0.0.1:{port}/{stream_name}"
    server = subprocess.Popen([str(mediamtx), str(config)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE)
    publisher = None
    hass = None
    api = None

    async def bridge_handler(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        method = request.split(b" ", 1)[0]
        for header in request.split(b"\r\n")[1:]:
            if header.lower().startswith(b"content-length:"):
                await reader.readexactly(int(header.split(b":", 1)[1]))
        status = b"200 OK" if method == b"DELETE" else b"202 Accepted"
        writer.write(b"HTTP/1.1 " + status + b"\r\nContent-Length: 2\r\n"
                     b"Connection: close\r\n\r\n{}")
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
        from custom_components.netvue_local import bridge_api, config_flow, rtsp
        integration = importlib.import_module("custom_components.netvue_local")

        def test_stream_url(name):
            return f"rtsp://127.0.0.1:{port}/{name}"

        # Production uses 8554; the synthetic server binds an available port.
        rtsp.stream_url_from_name = test_stream_url
        config_flow.stream_url_from_name = test_stream_url
        integration.stream_url_from_name = test_stream_url

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
        if result["type"] != "menu" or set(result["menu_options"]) != {"manual", "discover"}:
            raise RuntimeError("Expected configuration menu missing")
        flow_id = result["flow_id"]
        result = await manager.async_configure(flow_id, {"next_step_id": "manual"})
        if result["step_id"] != "manual":
            raise RuntimeError("Manual setup missing")
        result = await manager.async_configure(flow_id, {
            "name": "Synthetic Netvue", "host": "192.168.10.2",
        })
        if result["type"] != "create_entry":
            raise RuntimeError("Config entry not created")
        entry = result["result"]
        await hass.async_block_till_done()
        cameras = hass.states.async_entity_ids("camera")
        sensors = hass.states.async_entity_ids("binary_sensor")
        if len(cameras) != 1 or len(sensors) != 1:
            raise RuntimeError(f"Unexpected synthetic entities: {len(cameras)}, {len(sensors)}")
        if hass.states.get(sensors[0]).state != "on":
            raise RuntimeError("Synthetic stream availability was not detected")
        entity = camera.get_camera_from_entity_id(hass, cameras[0])
        print(f"camera features={entity.supported_features} stream_stills={entity.use_stream_for_stills} "
              f"state={hass.states.get(cameras[0]).state}", flush=True)
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
        if not await hass.config_entries.async_unload(entry.entry_id):
            raise RuntimeError("Synthetic entry failed to unload")
        print("Isolated HA: config and options flows, entities, RTSP availability, Stream JPEG, "
              "offline transition and unload verified", flush=True)
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
