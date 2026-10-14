from pathlib import Path

import plivo
from flask import Blueprint, current_app, jsonify, request

from security import (
    callback_url,
    demo_required,
    normalize_phone,
    validate_base_url,
)


calls_bp = Blueprint("calls", __name__)


@calls_bp.post("/calls")
@demo_required
def start_call():
    config = current_app.config

    required = [
        "PLIVO_AUTH_ID",
        "PLIVO_AUTH_TOKEN",
        "PLIVO_FROM_NUMBER",
        "ALLOWED_RECEIVER_NUMBER",
        "ASSOCIATE_NUMBER",
        "PUBLIC_BASE_URL",
    ]

    missing = [
        name
        for name in required
        if not config.get(name)
    ]

    if missing:
        return jsonify(
            error="Missing configuration: " + ", ".join(missing)
        ), 503

    try:
        validate_base_url(config["PUBLIC_BASE_URL"])

        receiver_allowed = normalize_phone(
            config["ALLOWED_RECEIVER_NUMBER"]
        )

        associate = normalize_phone(config["ASSOCIATE_NUMBER"])
        source = normalize_phone(config["PLIVO_FROM_NUMBER"])

        if associate in (receiver_allowed, source):
            raise ValueError(
                "Use a separate associate number to prevent a call loop."
            )

    except ValueError as error:
        return jsonify(
            error="Configuration error: " + str(error)
        ), 503

    audio_directory = Path(current_app.static_folder) / "audio"

    if any(
        not (audio_directory / filename).is_file()
        for filename in ("english.mp3", "spanish.mp3")
    ):
        return jsonify(
            error=(
                "Audio files missing. "
                "Run python scripts/generate_audio.py."
            )
        ), 503

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="Expected a JSON object."), 400

    try:
        receiver = normalize_phone(data.get("phone"))
    except ValueError as error:
        return jsonify(error=str(error)), 400

    if receiver != receiver_allowed:
        return jsonify(
            error="Use the receiver number configured in .env."
        ), 403

    store = current_app.extensions["call_store"]

    try:
        call = store.reserve(receiver)
    except ValueError as error:
        return jsonify(error=str(error)), 429

    session_id = call["id"]

    try:
        client = plivo.RestClient(
            config["PLIVO_AUTH_ID"],
            config["PLIVO_AUTH_TOKEN"],
        )

        result = client.calls.create(
            from_=source,
            to_=receiver,
            answer_url=callback_url("/ivr/answer", session_id),
            answer_method="POST",
            ring_url=callback_url("/ivr/ring", session_id),
            ring_method="POST",
            hangup_url=callback_url("/ivr/hangup", session_id),
            hangup_method="POST",
            ring_timeout=30,
            time_limit=600,
        )

    except Exception as error:
        current_app.logger.warning(
            "Call creation failed (%s).",
            type(error).__name__,
        )

        rejected = isinstance(
            error,
            (
                plivo.exceptions.AuthenticationError,
                plivo.exceptions.ForbiddenError,
                plivo.exceptions.GeoPermissionError,
                plivo.exceptions.InvalidRequestError,
                plivo.exceptions.ValidationError,
            ),
        )

        with store.lock:
            call["status"] = "failed" if rejected else "uncertain"

            if rejected:
                call["stage"] = "done"

            store.event(
                call,
                (
                    "Call request rejected."
                    if rejected
                    else "Call outcome uncertain. Check Plivo before retrying."
                ),
            )

        return jsonify(
            error=(
                "Could not confirm the call request. "
                "Check Plivo credentials, permissions, balance and call logs "
                "before retrying."
            ),
            session_id=session_id,
        ), 502

    with store.lock:
        call["request_uuid"] = getattr(result, "request_uuid", None)

        if call["status"] == "requesting":
            call["status"] = "accepted"

        store.event(call, "Plivo accepted the outbound call request.")

    return jsonify(
        message=(
            "Call request accepted. "
            "Answer your phone and enter your birthdate code."
        ),
        session_id=session_id,
        request_uuid=call["request_uuid"],
    ), 202


@calls_bp.get("/calls/<session_id>")
@demo_required
def call_status(session_id):
    store = current_app.extensions["call_store"]

    with store.lock:
        store.prune()
        call = store.calls.get(session_id)

        if not call:
            return jsonify(
                error="Call session not found. It may have expired."
            ), 404

        return jsonify(store.public(call))