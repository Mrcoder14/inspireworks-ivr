import hmac
import time
from threading import Lock
from urllib.parse import urlsplit

import phonenumbers
import plivo
from flask import Blueprint, Response, current_app, jsonify, request
from plivo import plivoxml


calls_bp = Blueprint("calls", __name__)

# Prevent rapid repeat clicks from creating multiple calls.
call_lock = Lock()
last_attempt = None


def normalize_phone(value):
    """Validate an international number and return E.164 format."""
    if not isinstance(value, str) or not value.strip().startswith("+"):
        raise ValueError(
            "Enter the number with its country code, such as +91."
        )

    try:
        number = phonenumbers.parse(value.strip(), None)
    except phonenumbers.NumberParseException:
        raise ValueError("Invalid phone number.") from None

    if not phonenumbers.is_valid_number(number):
        raise ValueError("Invalid phone number.")

    return phonenumbers.format_number(
        number,
        phonenumbers.PhoneNumberFormat.E164,
    )


@calls_bp.post("/calls")
def start_call():
    global last_attempt

    config = current_app.config

    # Authenticate the demo user without exposing Plivo credentials.
    expected_key = config.get("DEMO_ACCESS_KEY", "")
    supplied_key = request.headers.get("X-Demo-Key", "")

    if not expected_key:
        return jsonify(
            error="Set DEMO_ACCESS_KEY in .env."
        ), 503

    if not hmac.compare_digest(
        supplied_key.encode("utf-8"),
        expected_key.encode("utf-8"),
    ):
        return jsonify(
            error="Incorrect demo access key."
        ), 401

    required = [
        "PLIVO_AUTH_ID",
        "PLIVO_AUTH_TOKEN",
        "PLIVO_FROM_NUMBER",
        "ALLOWED_RECEIVER_NUMBER",
        "PUBLIC_BASE_URL",
    ]

    missing = [
        name for name in required
        if not config.get(name)
    ]

    if missing:
        return jsonify(
            error="Missing configuration: " + ", ".join(missing)
        ), 503

    base_url = config["PUBLIC_BASE_URL"].rstrip("/")

    try:
        parsed_url = urlsplit(base_url)

        valid_base_url = (
            parsed_url.scheme == "https"
            and bool(parsed_url.hostname)
            and parsed_url.path == ""
            and not parsed_url.query
            and not parsed_url.fragment
            and not parsed_url.username
            and not parsed_url.password
        )
    except ValueError:
        valid_base_url = False

    if not valid_base_url:
        return jsonify(
            error=(
                "PUBLIC_BASE_URL must be your HTTPS tunnel address "
                "without a path, query string, or fragment."
            )
        ), 503

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(
            error="Expected a JSON object."
        ), 400

    try:
        receiver = normalize_phone(data.get("phone"))
    except ValueError as error:
        return jsonify(error=str(error)), 400

    try:
        allowed_receiver = normalize_phone(
            config["ALLOWED_RECEIVER_NUMBER"]
        )
    except ValueError:
        return jsonify(
            error=(
                "ALLOWED_RECEIVER_NUMBER in .env is invalid. "
                "Use your mobile number with its country code."
            )
        ), 503

    # This demo can call only the configured receiver.
    if receiver != allowed_receiver:
        return jsonify(
            error="Use the receiver number configured in .env."
        ), 403

    with call_lock:
        now = time.monotonic()

        if last_attempt is not None and now - last_attempt < 20:
            return jsonify(
                error="Wait 20 seconds before requesting another call."
            ), 429

        last_attempt = now

    try:
        client = plivo.RestClient(
            config["PLIVO_AUTH_ID"],
            config["PLIVO_AUTH_TOKEN"],
        )

        result = client.calls.create(
            from_=config["PLIVO_FROM_NUMBER"],
            to_=receiver,
            answer_url=f"{base_url}/ivr/answer",
            answer_method="POST",
            ring_timeout=30,
            time_limit=600,
        )

    except Exception as error:
        # Log only the exception class, not potentially sensitive details.
        current_app.logger.warning(
            "Call creation failed (%s).",
            type(error).__name__,
        )

        return jsonify(
            error=(
                "Could not create the call. Check your Plivo credentials, "
                "number permissions, and account balance."
            )
        ), 502

    # Plivo returns a ResponseObject, not a standard dictionary.
    request_uuid = getattr(result, "request_uuid", None)

    return jsonify(
        message=(
            "Plivo accepted the call request. "
            "Wait for your phone to ring."
        ),
        request_uuid=request_uuid,
    ), 202


@calls_bp.post("/ivr/answer")
def answer_call():
    config = current_app.config

    signature = request.headers.get("X-Plivo-Signature-V3", "")
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce", "")

    auth_token = config.get("PLIVO_AUTH_TOKEN", "")
    base_url = config.get("PUBLIC_BASE_URL", "").rstrip("/")

    if not signature or not nonce or not auth_token or not base_url:
        return "Forbidden", 403

    # Match the public URL Plivo requested, even behind an ngrok tunnel.
    webhook_url = base_url + request.path

    try:
        if request.query_string:
            webhook_url += "?" + request.query_string.decode("utf-8")

        valid_signature = plivo.utils.validate_v3_signature(
            request.method,
            webhook_url,
            nonce,
            auth_token,
            signature,
            request.form.to_dict(),
        )
    except Exception:
        valid_signature = False

    if not valid_signature:
        current_app.logger.warning(
            "Rejected webhook: invalid Plivo signature."
        )
        return "Forbidden", 403

    response = plivoxml.ResponseElement()

    response.add(
        plivoxml.SpeakElement(
            "Welcome to InspireWorks. "
            "Your outbound call integration is working. Goodbye.",
            language="en-US",
        )
    )

    response.add(plivoxml.HangupElement())

    return Response(
        response.to_string(),
        mimetype="application/xml",
    )