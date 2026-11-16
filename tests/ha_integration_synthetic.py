"""Exercise a real isolated Home Assistant config flow and synthetic camera."""

import asyncio
import logging
import os
import socket
import subprocess
from pathlib import Path

from homeassistant import bootstrap, core, loader
from homeassistant.components import camera

logging.basicConfig(level=logging.WARNING)


async def main():
    root = Path("/tmp/ha-synthetic")
    (root / "custom_components").mkdir(parents=True)
    (root / "custom_components" / "netvue_local").symlink_to("/component")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = root / "mediamtx.yml"
    config.write_text(Path("/mediamtx-template.yml").read_text().replace(":8554", f":{port}"))
    url = f"rtsp://127.0.0.1:{port}/netvue2"
    server = subprocess.Popen(["/mediamtx", str(config)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE)
    publisher = None
    hass = None
    try:
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
            "/usr/bin/ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i",
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
        from custom_components.netvue_local.rtsp import async_probe_stream
        for _ in range(100):
            if publisher.poll() is not None:
                raise RuntimeError("Synthetic publisher failed to start")
            if await async_probe_stream(url):
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError("Synthetic H.264 RTSP stream unavailable")
        manager = hass.config_entries.flow
        result = await manager.async_init("netvue_local", context={"source": "user"})
        if result["type"] != "menu" or set(result["menu_options"]) != {"manual", "discover"}:
            raise RuntimeError("Expected configuration menu missing")
        flow_id = result["flow_id"]
        result = await manager.async_configure(flow_id, {"next_step_id": "manual"})
        if result["step_id"] != "manual":
            raise RuntimeError("Manual setup missing")
        result = await manager.async_configure(flow_id, {
            "name": "Synthetic Netvue", "host": "192.168.10.2", "stream_url": url,
        })
        if result["step_id"] != "confirm":
            raise RuntimeError("Association confirmation missing")
        result = await manager.async_configure(flow_id, {"confirm": True})
        if result["step_id"] != "status" or result["description_placeholders"]["status"] != "H.264 SDP":
            raise RuntimeError("Synthetic RTSP probe failed")
        result = await manager.async_configure(flow_id, {})
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
        stream = await entity.async_create_stream()
        if stream is None:
            raise RuntimeError("Home Assistant did not create a Stream")
        await asyncio.wait_for(stream.async_get_image(wait_for_next_keyframe=True), 20)
        image = await camera.async_get_image(hass, cameras[0], timeout=20)
        if not image.content.startswith(b"\xff\xd8"):
            raise RuntimeError("Home Assistant Stream did not produce a JPEG")
        publisher.terminate()
        await asyncio.to_thread(publisher.wait, 5)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        if hass.states.get(sensors[0]).state != "off" or hass.states.get(cameras[0]).state != "unavailable":
            raise RuntimeError("Stopped synthetic stream did not become unavailable")
        if not await hass.config_entries.async_unload(entry.entry_id):
            raise RuntimeError("Synthetic entry failed to unload")
        print("Isolated HA: config flow, entities, RTSP availability, Stream JPEG, "
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


if __name__ == "__main__":
    asyncio.run(main())
