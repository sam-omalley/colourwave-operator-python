import json
import logging
import os
import socket
from datetime import datetime, timezone

from flask import Flask, render_template, request

app = Flask(__name__)

VERSION = os.environ.get("VERSION", "unknown")
COLOUR = os.environ.get("COLOUR", "#cccccc")
HOSTNAME = socket.gethostname()


# Configure structured JSON logging.
# In Go we called slog.SetDefault — here we replace Flask's default handler.
class JSONFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "time": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
            **{k: v for k, v in record.__dict__.items()
               if k in ("method", "path", "remote_addr")},
        })


handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
app.logger.handlers = [handler]
app.logger.setLevel(logging.INFO)
# Suppress Werkzeug's default access log — we log requests ourselves
logging.getLogger("werkzeug").disabled = True


@app.route("/")
def index():
    app.logger.info(
        "request received",
        extra={"method": request.method, "path": request.path, "remote_addr": request.remote_addr},
    )
    return render_template("index.html", version=VERSION, colour=COLOUR, hostname=HOSTNAME)


if __name__ == "__main__":
    # Development only — production uses gunicorn (see Containerfile)
    app.run(host="0.0.0.0", port=8080)
