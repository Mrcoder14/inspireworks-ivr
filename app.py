from flask import Flask, jsonify, render_template
from waitress import serve

from calls import calls_bp
from config import Config
from ivr import ivr_bp
from store import CallStore


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)

    if overrides:
        app.config.update(overrides)

    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    app.extensions["call_store"] = CallStore()

    app.register_blueprint(calls_bp)
    app.register_blueprint(ivr_bp)

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(
            status="ok",
            application="InspireWorks IVR Demo",
        )

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"

        if not response.mimetype.startswith("audio/"):
            response.headers["Cache-Control"] = "no-store"

        return response

    return app


app = create_app()


if __name__ == "__main__":
    port = app.config["PORT"]

    print(f"Open http://127.0.0.1:{port}")

    serve(
        app,
        host="127.0.0.1",
        port=port,
        threads=8,
    )