import hmac
from functools import wraps
from urllib.parse import urlencode, urlsplit

import phonenumbers
import plivo
from flask import current_app, jsonify, request


def normalize_phone(value):
    if not isinstance(value, str) or not value.strip().startswith("+"):
        raise ValueError("Include your country code, such as +91.")

    try:
        parsed = phonenumbers.parse(value.strip(), None)
    except phonenumbers.NumberParseException:
        raise ValueError("Invalid phone number.") from None

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number.")

    return phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.E164,
    )


def demo_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        expected = current_app.config.get("DEMO_ACCESS_KEY", "")
        provided = request.headers.get("X-Demo-Key", "")

        if not expected:
            return jsonify(error="Set DEMO_ACCESS_KEY in .env."), 503

        if not hmac.compare_digest(
            provided.encode(),
            expected.encode(),
        ):
            return jsonify(error="Incorrect demo access key."), 401

        return function(*args, **kwargs)

    return wrapped


def validate_base_url(value):
    try:
        parsed = urlsplit(value)

        valid = (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
        )

        # Accessing this property also validates the port.
        _ = parsed.port

    except ValueError:
        valid = False

    if not valid:
        raise ValueError(
            "PUBLIC_BASE_URL must be the HTTPS tunnel origin without a path."
        )


def callback_url(path, session_id, **extra):
    parameters = {
        "session": session_id,
        **extra,
    }

    return (
        current_app.config["PUBLIC_BASE_URL"]
        + path
        + "?"
        + urlencode(parameters)
    )


def valid_plivo_signature():
    signature = request.headers.get("X-Plivo-Signature-V3", "")
    nonce = request.headers.get("X-Plivo-Signature-V3-Nonce", "")

    token = current_app.config.get("PLIVO_AUTH_TOKEN", "")
    base_url = current_app.config.get("PUBLIC_BASE_URL", "")

    if not all((signature, nonce, token, base_url)):
        return False

    try:
        public_url = base_url + request.path

        if request.query_string:
            public_url += "?" + request.query_string.decode("utf-8")

        if any(
            len(values) != 1
            for _, values in request.form.lists()
        ):
            return False

        return plivo.utils.validate_v3_signature(
            "POST",
            public_url,
            nonce,
            token,
            signature,
            request.form.to_dict(),
        )

    except Exception:
        return False