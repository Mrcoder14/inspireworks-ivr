import hmac
from functools import wraps

from flask import Blueprint, Response, current_app, request
from plivo import plivoxml

from security import (
    callback_url,
    normalize_phone,
    valid_plivo_signature,
)


# Replace with your birthday in DDMM format.
# Example: 5 March = "0503".
EXPECTED_OTP = "1412"

ivr_bp = Blueprint("ivr", __name__)

VOICE = {
    "en": "en-US",
    "es": "es-ES",
}

TEXT = {
    "en": {
        "menu": (
            "Press 1 to hear a short audio message. "
            "Press 2 to connect to a live associate."
        ),
        "invalid": "Invalid option. Please try again.",
        "timeout": "We did not receive your input. Please try again.",
        "play": "Here is your audio message.",
        "transfer": (
            "Please hold while we connect you to a live associate."
        ),
        "unavailable": (
            "The associate is unavailable. Returning to the menu."
        ),
        "goodbye": "Thank you for calling InspireWorks. Goodbye.",
    },
    "es": {
        "menu": (
            "Presione uno para escuchar un mensaje de audio. "
            "Presione dos para hablar con un asesor."
        ),
        "invalid": "Opción no válida. Inténtelo de nuevo.",
        "timeout": "No recibimos su respuesta. Inténtelo de nuevo.",
        "play": "Aquí está su mensaje de audio.",
        "transfer": (
            "Por favor espere mientras le conectamos con un asesor."
        ),
        "unavailable": (
            "El asesor no está disponible. Volviendo al menú."
        ),
        "goodbye": "Gracias por llamar a InspireWorks. Adiós.",
    },
}


def xml(response):
    return Response(
        response.to_string(),
        mimetype="application/xml",
    )


def speak(response, text, language="en"):
    response.add(
        plivoxml.SpeakElement(
            text,
            language=VOICE[language],
        )
    )


def expired_xml():
    response = plivoxml.ResponseElement()

    speak(
        response,
        "This call session is no longer available. "
        "Please start a new call.",
    )

    response.add(plivoxml.HangupElement())

    return xml(response)


def prompt(call, prefix=None):
    response = plivoxml.ResponseElement()

    stage = call["stage"]
    language = call["language"] if stage == "action" else "en"

    if prefix:
        speak(response, prefix, language)

    if stage not in ("otp", "language", "action"):
        return xml(response)

    paths = {
        "otp": "/ivr/verify-otp",
        "language": "/ivr/language",
        "action": "/ivr/action",
    }

    digits = plivoxml.GetDigitsElement(
        action=callback_url(paths[stage], call["id"]),
        method="POST",
        num_digits=4 if stage == "otp" else 1,
        timeout=10,
        digit_timeout=5,
        retries=1,
        finish_on_key="none",
        redirect=True,
        log=False,
    )

    if stage == "otp":
        digits.add(
            plivoxml.SpeakElement(
                "Please enter your four digit verification code "
                "using your phone keypad.",
                language="en-US",
            )
        )

    elif stage == "language":
        digits.add(
            plivoxml.SpeakElement(
                "Press 1 for English.",
                language="en-US",
            )
        )

        digits.add(
            plivoxml.SpeakElement(
                "Presione dos para español.",
                language="es-ES",
            )
        )

    else:
        digits.add(
            plivoxml.SpeakElement(
                TEXT[language]["menu"],
                language=VOICE[language],
            )
        )

    response.add(digits)

    # If no digits arrive, repeat the current stage.
    response.add(
        plivoxml.RedirectElement(
            callback_url(
                "/ivr/prompt",
                call["id"],
                reason="timeout",
            ),
            method="POST",
        )
    )

    return xml(response)


@ivr_bp.before_request
def verify_webhook():
    if not valid_plivo_signature():
        return "Forbidden", 403


def live_session(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        store = current_app.extensions["call_store"]

        with store.lock:
            store.prune()

            session_id = request.args.get("session", "")
            call = store.calls.get(session_id)

            if not call or call["stage"] == "done":
                if request.path in ("/ivr/ring", "/ivr/hangup"):
                    return "", 204

                return expired_xml()

            call_uuid = request.form.get("CallUUID", "")

            # Dial callbacks identify the original call as the A-leg.
            if request.path == "/ivr/dial-result":
                call_uuid = (
                    request.form.get("DialALegUUID", "")
                    or call_uuid
                )

            if not call_uuid:
                return "Missing CallUUID", 400

            if (
                call["call_uuid"]
                and call["call_uuid"] != call_uuid
            ):
                return "Call mismatch", 403

            if not call["call_uuid"]:
                if request.path not in (
                    "/ivr/answer",
                    "/ivr/ring",
                    "/ivr/hangup",
                ):
                    return "Call has not answered", 403

                try:
                    destination = request.form.get("To", "")

                    if destination.isdigit():
                        destination = "+" + destination

                    if normalize_phone(destination) != call["receiver"]:
                        return "Receiver mismatch", 403

                except ValueError:
                    return "Receiver mismatch", 403

                call["call_uuid"] = call_uuid

            # Reuse responses for identical provider request retries.
            fingerprint = (
                request.full_path,
                request.headers["X-Plivo-Signature-V3-Nonce"],
                request.headers["X-Plivo-Signature-V3"],
            )

            if fingerprint in call["seen"]:
                return call["seen"][fingerprint]

            result = function(call, store, *args, **kwargs)

            call["seen"][fingerprint] = result

            if len(call["seen"]) > 200:
                del call["seen"][next(iter(call["seen"]))]

            return result

    return wrapped


@ivr_bp.post("/ivr/ring")
@live_session
def ring(call, store):
    if call["status"] in ("requesting", "accepted", "uncertain"):
        call["status"] = "ringing"
        store.event(call, "Receiver phone is ringing.")

    return "", 204


@ivr_bp.post("/ivr/answer")
@live_session
def answer(call, store):
    if call["status"] != "in-progress":
        call["status"] = "in-progress"

        store.event(
            call,
            "Receiver answered; authentication prompt started.",
        )

        return prompt(call, "Welcome to InspireWorks.")

    return prompt(call)


@ivr_bp.post("/ivr/verify-otp")
@live_session
def verify_otp(call, store):
    if call["stage"] != "otp":
        return prompt(call)

    entered_digits = request.form.get("Digits", "")

    if hmac.compare_digest(
        entered_digits.encode(),
        EXPECTED_OTP.encode(),
    ):
        call.update(
            authenticated=True,
            stage="language",
        )

        store.event(
            call,
            "Authentication successful; language menu started.",
        )

        return prompt(call, "Verification successful.")

    store.event(
        call,
        "Authentication unsuccessful; prompt repeated.",
    )

    return prompt(
        call,
        "Incorrect or incomplete code. Please try again.",
    )


@ivr_bp.post("/ivr/language")
@live_session
def language(call, store):
    if (
        not call["authenticated"]
        or call["stage"] != "language"
    ):
        return prompt(call)

    selected = request.form.get("Digits", "")

    if selected not in ("1", "2"):
        store.event(
            call,
            "Invalid language input; language menu repeated.",
        )

        return prompt(
            call,
            "Invalid option. Please select 1 or 2.",
        )

    call.update(
        language="en" if selected == "1" else "es",
        stage="action",
    )

    store.event(
        call,
        "English selected." if selected == "1" else "Spanish selected.",
    )

    return prompt(call)


@ivr_bp.post("/ivr/action")
@live_session
def action(call, store):
    if (
        not call["authenticated"]
        or call["stage"] != "action"
    ):
        return prompt(call)

    selected = request.form.get("Digits", "")
    language = call["language"]

    response = plivoxml.ResponseElement()

    if selected == "1":
        call["stage"] = "playing"

        store.event(call, "MP3 playback instructions sent.")

        speak(
            response,
            TEXT[language]["play"],
            language,
        )

        filename = (
            "english.mp3"
            if language == "en"
            else "spanish.mp3"
        )

        audio_url = (
            current_app.config["PUBLIC_BASE_URL"]
            + "/static/audio/"
            + filename
        )

        response.add(plivoxml.PlayElement(audio_url))

        response.add(
            plivoxml.RedirectElement(
                callback_url(
                    "/ivr/audio-finished",
                    call["id"],
                ),
                method="POST",
            )
        )

        return xml(response)

    if selected == "2":
        call["stage"] = "dialing"

        store.event(
            call,
            "Associate dialing instructions sent.",
        )

        speak(
            response,
            TEXT[language]["transfer"],
            language,
        )

        dial = plivoxml.DialElement(
            action=callback_url(
                "/ivr/dial-result",
                call["id"],
            ),
            method="POST",
            redirect=True,
            timeout=25,
            time_limit=300,
            caller_id=current_app.config["PLIVO_FROM_NUMBER"],
        )

        dial.add(
            plivoxml.NumberElement(
                normalize_phone(
                    current_app.config["ASSOCIATE_NUMBER"]
                )
            )
        )

        response.add(dial)

        return xml(response)

    store.event(
        call,
        "Invalid action input; action menu repeated.",
    )

    return prompt(call, TEXT[language]["invalid"])


@ivr_bp.post("/ivr/prompt")
@live_session
def repeat_prompt(call, store):
    language = (
        call["language"]
        if call["stage"] == "action"
        else "en"
    )

    store.event(
        call,
        "No input received; current prompt repeated.",
    )

    return prompt(call, TEXT[language]["timeout"])


@ivr_bp.post("/ivr/audio-finished")
@live_session
def audio_finished(call, store):
    if (
        call["authenticated"]
        and call["stage"] == "playing"
    ):
        call["stage"] = "action"

        store.event(
            call,
            "Playback sequence finished; action menu resumed.",
        )

    return prompt(call)


@ivr_bp.post("/ivr/dial-result")
@live_session
def dial_result(call, store):
    if (
        not call["authenticated"]
        or call["stage"] != "dialing"
    ):
        return prompt(call)

    status = request.form.get("DialStatus", "").lower()

    known_statuses = {
        "completed",
        "busy",
        "failed",
        "timeout",
        "no-answer",
        "cancel",
    }

    store.event(
        call,
        "Associate dial result: "
        + (status if status in known_statuses else "unknown"),
    )

    if status == "completed":
        response = plivoxml.ResponseElement()

        speak(
            response,
            TEXT[call["language"]]["goodbye"],
            call["language"],
        )

        response.add(plivoxml.HangupElement())

        call["stage"] = "ending"

        return xml(response)

    call["stage"] = "action"

    return prompt(
        call,
        TEXT[call["language"]]["unavailable"],
    )


@ivr_bp.post("/ivr/hangup")
@live_session
def hangup(call, store):
    call.update(
        status="completed",
        stage="done",
        authenticated=False,
    )

    call["seen"].clear()

    store.event(
        call,
        "Call ended; authentication state cleared.",
    )

    return "", 204
