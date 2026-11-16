import argparse
import json
import socket

from .authentication import authenticate
from .connection import connect
from .discovery import discover
from .protocol import ProtocolError
from .video import receive_live


def main() -> int:
    parser = argparse.ArgumentParser(description="One bounded authorized LAN test")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    stage = "discovery"
    try:
        camera = discover(args.source, args.target)
        if camera.port != 5555:
            raise ProtocolError("Discovered port differs from the authorized port")
        print("discovery: matching endpoint on TCP 5555", flush=True)
        stage = "tcp"
        with connect(camera) as sock:
            print("tcp: connected", flush=True)
            stage = "authentication"
            print("authentication: sending one request", flush=True)
            code = authenticate(sock, camera)
            print(f"authentication: result_code={code}", flush=True)
            if code != 0:
                return 1
            if args.live:
                stage = "live"
                print("live: sending one selection request, maximum 10 seconds", flush=True)
                result = receive_live(sock)
                summary = result.safe_result()
                print("live: " + json.dumps(summary), flush=True)
                if (result.channel_result != 0 or result.video_frames == 0
                        or summary["decode"]["status"] != "decoded_h264_image_in_memory"):
                    return 1
        return 0
    except (ProtocolError, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, ProtocolError):
            print(f"{stage}: error={exc}", flush=True)
        else:
            print(f"{stage}: error={type(exc).__name__}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
