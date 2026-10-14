from flask import Flask, jsonify, render_template
from waitress import serve

from calls import calls_bp
from config import Config


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    app.register_blueprint(calls_bp)

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/health")
    def health():
        return jsonify(
            status="ok",
            application="InspireWorks IVR Demo",
        )

    return app


app = create_app()


if __name__ == "__main__":
    port = app.config["PORT"]

    print(f"Open http://127.0.0.1:{port}")
    serve(app, host="127.0.0.1", port=port)