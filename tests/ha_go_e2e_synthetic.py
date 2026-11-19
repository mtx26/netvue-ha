"""Exercise HA, the real Go manager and a synthetic Netvue camera in isolation."""

import asyncio
import ipaddress
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from aiohttp import ClientConnectorError, ClientSession
from homeassistant import bootstrap, core, loader
from homeassistant.components import camera
from homeassistant.helpers import entity_registry as er

logging.basicConfig(level=logging.WARNING)


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


def private_ipv4(value: str) -> bool:
    return any(ipaddress.ip_address(value) in ipaddress.ip_network(network) for network in
               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def synthetic_camera() -> None:
    """Replace this process with the shared Go Netvue camera simulator.

    The simulator lives in tests/simulator/netvue-sim and is the only synthetic
    camera implementation. CI builds it and exposes it as NETVUE_TEST_SIMULATOR.
    """
    host = os.environ["NETVUE_TEST_CAMERA_HOST"]
    if not private_ipv4(host):
        raise RuntimeError("The synthetic camera must use a private IPv4 address")
    simulator = os.environ.get("NETVUE_TEST_SIMULATOR")
    if not simulator or not os.access(simulator, os.X_OK):
        raise RuntimeError("Set NETVUE_TEST_SIMULATOR to the built netvue-sim executable")
    arguments = [simulator, "-host", host]
    if os.environ.get("NETVUE_TEST_REJECT_INITIAL_LOGIN", "1") == "1":
        arguments.append("-reject-initial-login")
    os.execv(simulator, arguments)


async def camera_login_count(host: str) -> int:
    async with ClientSession() as session:
        async with session.get(f"http://{host}:5556/status") as response:
            response.raise_for_status()
            return (await response.json())["login_count"]


async def camera_status(host: str) -> dict:
    async with ClientSession() as session:
        async with session.get(f"http://{host}:5556/status") as response:
            response.raise_for_status()
            return await response.json()


async def wait_camera_ready(host: str) -> None:
    for _ in range(100):
        try:
            await camera_login_count(host)
            return
        except ClientConnectorError:
            await asyncio.sleep(0.1)
    raise RuntimeError(f"Synthetic camera status endpoint {host}:5556 did not become ready")


async def wait_camera_login_count(host: str, minimum: int, timeout: float = 20) -> int:
    count = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                count = await camera_login_count(host)
                if count >= minimum:
                    return count
                await asyncio.sleep(0.1)
    except TimeoutError as exc:
        raise RuntimeError(
            f"Synthetic camera login count did not reach {minimum} within {timeout}s "
            f"(last observed: {count})"
        ) from exc


async def decode_rtsp_stream(stream_name: str) -> None:
    command = [
        "/usr/bin/ffmpeg", "-v", "error", "-rtsp_transport", "tcp",
        "-i", f"rtsp://127.0.0.1:8554/{stream_name}",
        "-frames:v", "1", "-f", "null", "-",
    ]
    result = await asyncio.to_thread(
        subprocess.run, command, capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        raise RuntimeError(f"Could not decode {stream_name}: {result.stderr}")


async def manager_camera(host: str) -> dict:
    async with ClientSession() as session:
        async with session.get(f"http://127.0.0.1:8765/api/v1/cameras/{host}") as response:
            response.raise_for_status()
            return await response.json()


async def wait_manager_camera_state(host: str, wanted: str, timeout: float = 20) -> dict:
    camera_state = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                camera_state = await manager_camera(host)
                if camera_state["state"] == wanted:
                    return camera_state
                await asyncio.sleep(0.1)
    except TimeoutError as exc:
        raise RuntimeError(
            f"Synthetic camera state did not become {wanted!r} within {timeout}s "
            f"(last observed: {camera_state})"
        ) from exc


async def main():
    component = Path(os.environ["NETVUE_TEST_COMPONENT"])
    mediamtx = Path(os.environ["NETVUE_TEST_MEDIAMTX"])
    config = Path(os.environ["NETVUE_TEST_MEDIAMTX_CONFIG"])
    bridge = Path(os.environ["NETVUE_TEST_GO_BRIDGE"])
    camera_host = os.environ["NETVUE_TEST_CAMERA_HOST"]
    camera2_host = os.environ["NETVUE_TEST_CAMERA_2_HOST"]
    bridge_host = socket.gethostbyname(socket.gethostname())
    if not all(private_ipv4(host) for host in (camera_host, camera2_host, bridge_host)):
        raise RuntimeError("The synthetic bridge and cameras must use private IPv4 addresses")
    if len({camera_host, camera2_host, bridge_host}) != 3:
        raise RuntimeError("The synthetic cameras and bridge must use distinct addresses")

    with tempfile.TemporaryDirectory(prefix="netvue-ha-go-") as directory:
        root = Path(directory)
        custom_components = root / "custom_components"
        custom_components.mkdir()
        shutil.copytree(component, custom_components / "netvue_local")
        server = subprocess.Popen([str(mediamtx), str(config)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE)
        bridge_process = None
        hass = None

        try:
            await wait_port(8554, server)
            for _ in range(100):
                try:
                    _, writer = await asyncio.open_connection(camera_host, 5555)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Synthetic camera did not become ready")

            bridge_process = subprocess.Popen(
                [str(bridge), "serve", "--source", bridge_host],
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
            result = await flow.async_configure(
                result["flow_id"], {"next_step_id": "discovery"}
            )
            if result["type"] != "form" or result["step_id"] != "select":
                raise RuntimeError(f"Synthetic LAN discovery failed: {result}")
            result = await flow.async_configure(
                result["flow_id"],
                {"name": "Synthetic Netvue", "host": camera_host, "manual": False},
            )
            if result["type"] != "create_entry":
                raise RuntimeError("Synthetic Home Assistant entry was not created")
            entry = result["result"]
            await hass.async_block_till_done()
            registry = er.async_get(hass)

            def entity_id(domain):
                suffix = {"sensor": "session", "binary_sensor": "rtsp"}.get(domain)
                entities = [item.entity_id for item in er.async_entries_for_config_entry(
                    registry, entry.entry_id) if item.domain == domain and (
                        suffix is None or item.unique_id == f"{entry.entry_id}_{suffix}"
                    )]
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
            expected_error = "Camera authentication rejected [authentication]"
            actual_error = failed.attributes.get("error")
            if actual_error != expected_error:
                raise RuntimeError(
                    f"Home Assistant diagnostic: expected {expected_error!r}, got {actual_error!r}"
                )
            if await camera_login_count(camera_host) != 1:
                raise RuntimeError("A failed session was retried without an explicit request")
            await hass.services.async_call("button", "press", {"entity_id": restart}, blocking=True)
            await wait_state(session_sensor, "supervising")
            await wait_state(stream_sensor, "on")
            if await camera_login_count(camera_host) != 2:
                raise RuntimeError("Explicit restart did not open exactly one new session")
            entity = camera.get_camera_from_entity_id(hass, camera_entity)
            entity.stream = await entity.async_create_stream()
            if entity.stream is None:
                raise RuntimeError("Home Assistant did not create its video stream")
            image = await asyncio.wait_for(entity.stream.async_get_image(
                wait_for_next_keyframe=True), 20)
            if not image or not image.startswith(b"\xff\xd8"):
                raise RuntimeError("Home Assistant did not decode a synthetic JPEG")

            await wait_camera_ready(camera2_host)
            async with ClientSession() as session:
                async with session.post(
                    "http://127.0.0.1:8765/api/v1/cameras",
                    json={"host": camera2_host, "stream_name": "synthetic-camera-b"},
                ) as response:
                    if response.status != 202:
                        raise RuntimeError(f"Go manager did not add camera B: {response.status}")
                async with session.get(
                    f"http://127.0.0.1:8765/api/v1/cameras/{camera_host}"
                ) as response:
                    response.raise_for_status()
                    camera_a = await response.json()
                async with session.get(
                    f"http://127.0.0.1:8765/api/v1/cameras/{camera2_host}"
                ) as response:
                    response.raise_for_status()
                    camera_b = await response.json()
            if camera_a["state"] != "supervising" or camera_b["state"] != "supervising":
                raise RuntimeError("Both synthetic cameras must be supervised")
            await wait_camera_login_count(camera2_host, 1)
            await decode_rtsp_stream(camera_a["stream_name"])
            await decode_rtsp_stream(camera_b["stream_name"])
            stable_camera_a_logins = await camera_login_count(camera_host)
            reconnect_count = await camera_login_count(camera2_host) + 1
            async with ClientSession() as session:
                async with session.post(f"http://{camera2_host}:5556/fail-next") as response:
                    if response.status != 202:
                        raise RuntimeError("Could not induce a synthetic camera B disconnect")
            actual_reconnect_count = await wait_camera_login_count(camera2_host, reconnect_count)
            if actual_reconnect_count != reconnect_count:
                raise RuntimeError(
                    f"Camera B opened {actual_reconnect_count - reconnect_count + 1} "
                    "sessions after a single synthetic disconnect"
                )
            if await camera_login_count(camera_host) != stable_camera_a_logins:
                raise RuntimeError("Camera A was interrupted by camera B's failure")
            async with ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:8765/api/v1/cameras/{camera2_host}"
                ) as response:
                    response.raise_for_status()
                    camera_b = await response.json()
            if camera_b["state"] != "supervising":
                raise RuntimeError("Camera B did not recover to the supervising state")
            await decode_rtsp_stream(camera_a["stream_name"])
            await decode_rtsp_stream(camera_b["stream_name"])

            before_retryable_error = await camera_login_count(camera2_host)
            async with ClientSession() as session:
                async with session.post(
                    f"http://{camera2_host}:5556/fault-next/login/close-connection"
                ) as response:
                    if response.status != 202:
                        raise RuntimeError("Could not schedule a synthetic login disconnect")
            await wait_camera_login_count(camera2_host, before_retryable_error + 2)
            after_retryable_error = await camera_status(camera2_host)
            if after_retryable_error["login_count"] != before_retryable_error + 2:
                raise RuntimeError("Login disconnect did not recover with exactly one retry")
            if after_retryable_error["last_login_fault"] != "close-connection":
                raise RuntimeError("Synthetic login disconnect was not exercised")
            await decode_rtsp_stream(camera_b["stream_name"])

            before_timeout = await camera_login_count(camera2_host)
            async with ClientSession() as session:
                async with session.post(
                    f"http://{camera2_host}:5556/fault-next/login/login-timeout"
                ) as response:
                    if response.status != 202:
                        raise RuntimeError("Could not schedule a synthetic login timeout")
            # Two retries can each wait up to the supervisor's 30-second backoff,
            # with a 4-second authentication deadline between them.
            await wait_camera_login_count(camera2_host, before_timeout + 2, timeout=75)
            after_timeout = await camera_status(camera2_host)
            if after_timeout["login_count"] != before_timeout + 2:
                raise RuntimeError("Login timeout did not recover with exactly one retry")
            if after_timeout["last_login_fault"] != "login-timeout":
                raise RuntimeError("Synthetic login timeout was not exercised")
            await decode_rtsp_stream(camera_b["stream_name"])

            fault_modes = (
                ("login", "flagged-header"),
                ("login", "wrong-packet-kind"),
                ("login", "invalid-json"),
                ("login", "missing-result"),
                ("login", "unsupported-version"),
                ("live", "invalid-json"),
                ("live", "missing-result"),
                ("live", "rejected-result"),
                ("media", "unsupported-type"),
                ("media", "truncated-video"),
            )
            for stage, fault in fault_modes:
                before_b = await camera_login_count(camera2_host)
                async with ClientSession() as session:
                    async with session.post(
                        f"http://{camera2_host}:5556/fault-next/{stage}/{fault}"
                    ) as response:
                        if response.status != 202:
                            raise RuntimeError(f"Could not schedule synthetic fault {fault}")
                try:
                    failed = await wait_manager_camera_state(camera2_host, "failed", timeout=45)
                except RuntimeError as exc:
                    raise RuntimeError(f"Synthetic fault {stage}/{fault} did not complete") from exc
                after_fault = await camera_status(camera2_host)
                expected_fault_logins = before_b + (1 if stage != "media" else 0)
                if after_fault["login_count"] != expected_fault_logins:
                    raise RuntimeError(
                        f"Fault {fault} caused unexpected login count "
                        f"{after_fault['login_count']} (started at {before_b})"
                    )
                if after_fault[f"last_{stage}_fault"] != fault or not failed.get("error"):
                    raise RuntimeError(f"Fault {stage}/{fault} was not reported by the camera session")
                expected_stage = {
                    "login": "[authentication]",
                    "live": "[LIVE response]",
                    "media": "[media frame]",
                }[stage]
                if expected_stage not in failed["error"]:
                    raise RuntimeError(
                        f"Fault {stage}/{fault} produced an unexpected diagnostic: {failed['error']}"
                    )
                if await camera_login_count(camera_host) != stable_camera_a_logins:
                    raise RuntimeError(f"Fault {stage}/{fault} disrupted camera A")
                if (await manager_camera(camera_host))["state"] != "supervising":
                    raise RuntimeError(f"Fault {stage}/{fault} stopped camera A supervision")

                async with ClientSession() as session:
                    async with session.post(
                        f"http://127.0.0.1:8765/api/v1/cameras/{camera2_host}/start"
                    ) as response:
                        if response.status != 202:
                            raise RuntimeError(f"Could not restart camera B after {stage}/{fault}")
                await wait_manager_camera_state(camera2_host, "supervising")
                await wait_camera_login_count(camera2_host, expected_fault_logins + 1)
                await decode_rtsp_stream(camera_b["stream_name"])

            async with ClientSession() as session:
                async with session.delete(
                    f"http://127.0.0.1:8765/api/v1/cameras/{camera2_host}"
                ) as response:
                    if response.status != 200:
                        raise RuntimeError("Go manager did not remove synthetic camera B")

            if not await hass.config_entries.async_unload(entry.entry_id):
                raise RuntimeError("Synthetic Home Assistant entry failed to unload")
            async with ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:8765/api/v1/cameras/{camera_host}"
                ) as response:
                    if response.status != 404:
                        raise RuntimeError("Go manager retained the unloaded camera")
            print("HA -> Go discovery -> synthetic Netvue -> Go -> MediaMTX -> HA JPEG: "
                  "failure diagnostic, explicit restart and unload verified", flush=True)
            print("Go multi-camera E2E: camera B failure isolated, reconnect and RTSP decode verified",
                  flush=True)
            print("Synthetic protocol errors: flagged header, wrong packet kind, invalid JSON, "
                  "missing auth result, unsupported auth version, login timeout, bad LIVE replies "
                  "and malformed media all fail safely and restart",
                  flush=True)
        finally:
            if hass is not None:
                await hass.async_stop(force=True)
            for process in (bridge_process, server):
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        await asyncio.to_thread(process.wait, 10)
                    process.stderr.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--camera"]:
        synthetic_camera()
    else:
        asyncio.run(asyncio.wait_for(main(), 240))
