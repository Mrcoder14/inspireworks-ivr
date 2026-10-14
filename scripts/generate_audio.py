from pathlib import Path

from gtts import gTTS


MESSAGES = {
    "english.mp3": (
        "en",
        "Welcome to InspireWorks. "
        "This is our demonstration audio message. "
        "Our team helps businesses build better communication experiences. "
        "Thank you for trying our interactive voice response system.",
    ),
    "spanish.mp3": (
        "es",
        "Bienvenido a InspireWorks. "
        "Este es nuestro mensaje de audio de demostración. "
        "Nuestro equipo ayuda a las empresas a mejorar sus comunicaciones. "
        "Gracias por probar nuestro sistema de respuesta de voz interactiva.",
    ),
}


def main():
    destination = (
        Path(__file__).resolve().parents[1]
        / "static"
        / "audio"
    )

    destination.mkdir(parents=True, exist_ok=True)

    for filename, (language, message) in MESSAGES.items():
        output = destination / filename
        temporary = output.with_suffix(".tmp")

        try:
            gTTS(
                message,
                lang=language,
                timeout=20,
            ).save(str(temporary))

            temporary.replace(output)

            print("Generated", filename)

        except Exception:
            temporary.unlink(missing_ok=True)

            raise SystemExit(
                "Audio generation failed. Check your internet connection."
            )


if __name__ == "__main__":
    main()