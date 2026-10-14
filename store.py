import secrets
import time
from datetime import datetime, timezone
from threading import RLock


TERMINAL_STATUSES = {"completed", "failed", "expired"}


class CallStore:
    def __init__(self):
        self.lock = RLock()
        self.calls = {}
        self.last_attempt = None

    def prune(self):
        now = time.monotonic()

        for session_id, call in list(self.calls.items()):
            age = now - call["created"]

            if age > 900 and call["status"] not in TERMINAL_STATUSES:
                call.update(
                    status="expired",
                    stage="done",
                    authenticated=False,
                )

                self.event(call, "Call session expired.")

            if age > 3600:
                del self.calls[session_id]

    def reserve(self, receiver):
        with self.lock:
            self.prune()

            if any(
                call["status"] not in TERMINAL_STATUSES
                for call in self.calls.values()
            ):
                raise ValueError(
                    "A call is already active. Finish it before starting another."
                )

            now = time.monotonic()

            if (
                self.last_attempt is not None
                and now - self.last_attempt < 20
            ):
                raise ValueError(
                    "Wait 20 seconds before requesting another call."
                )

            self.last_attempt = now
            session_id = secrets.token_urlsafe(24)

            call = {
                "id": session_id,
                "receiver": receiver,
                "created": now,
                "status": "requesting",
                "stage": "otp",
                "authenticated": False,
                "language": "en",
                "call_uuid": None,
                "request_uuid": None,
                "events": [],
                "seen": {},
            }

            self.event(call, "Outbound call requested.")
            self.calls[session_id] = call

            return call

    @staticmethod
    def event(call, message):
        call["events"].append(
            {
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                "message": message,
            }
        )

        del call["events"][:-80]

    @staticmethod
    def public(call):
        return {
            "id": call["id"],
            "status": call["status"],
            "stage": call["stage"],
            "language": call["language"],
            "authenticated": call["authenticated"],
            "events": [dict(event) for event in call["events"]],
        }