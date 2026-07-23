import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .models import ObsSettings


class ObsError(Exception):
    pass


@dataclass
class ObsRecordStatus:
    active: bool
    paused: bool
    duration_seconds: float
    timecode: str


class ObsClient:
    def __init__(self, settings: ObsSettings, timeout: float = 2.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def get_record_status(self) -> ObsRecordStatus:
        response = self._request("GetRecordStatus")
        return ObsRecordStatus(
            active=bool(response.get("outputActive")),
            paused=bool(response.get("outputPaused")),
            duration_seconds=float(response.get("outputDuration", 0) or 0) / 1000.0,
            timecode=str(response.get("outputTimecode", "") or ""),
        )

    def _request(self, request_type: str, request_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        websocket = self._websocket_module()
        url = "ws://{}:{}".format(self.settings.host, self.settings.port)
        ws = websocket.create_connection(url, timeout=self.timeout, subprotocols=["obswebsocket.json"])
        try:
            hello = json.loads(ws.recv())
            self._identify(ws, hello)
            request_id = str(uuid.uuid4())
            ws.send(
                json.dumps(
                    {
                        "op": 6,
                        "d": {
                            "requestType": request_type,
                            "requestId": request_id,
                            "requestData": request_data or {},
                        },
                    }
                )
            )
            while True:
                message = json.loads(ws.recv())
                if message.get("op") != 7:
                    continue
                payload = message.get("d", {})
                if payload.get("requestId") != request_id:
                    continue
                status = payload.get("requestStatus", {})
                if not status.get("result"):
                    raise ObsError(status.get("comment") or "OBS request failed.")
                return payload.get("responseData", {}) or {}
        finally:
            ws.close()

    def _identify(self, ws, hello: Dict[str, Any]) -> None:
        data = hello.get("d", {})
        identify = {"rpcVersion": min(int(data.get("rpcVersion", 1) or 1), 1)}
        auth = data.get("authentication")
        if auth:
            if not self.settings.password:
                raise ObsError("OBS WebSocket requires a password.")
            identify["authentication"] = _auth_response(
                self.settings.password,
                str(auth.get("salt", "")),
                str(auth.get("challenge", "")),
            )
        ws.send(json.dumps({"op": 1, "d": identify}))
        response = json.loads(ws.recv())
        if response.get("op") != 2:
            raise ObsError("OBS WebSocket identification failed.")

    def _websocket_module(self):
        try:
            import websocket
        except ImportError as exc:
            raise ObsError("Install websocket-client to use OBS timing.") from exc
        return websocket


def _auth_response(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode("utf-8")).digest())
    return base64.b64encode(hashlib.sha256(secret + challenge.encode("utf-8")).digest()).decode("utf-8")
