"""
ColourWave UI — Python/Flask port of the Go control-panel UI.

Key differences from the Go version
-------------------------------------
1. **Routing**
   Go uses http.HandleFunc("/api/...", handler). Flask uses decorators:
   @app.route("/api/..."). Both patterns register a path -> function mapping;
   the decorator form just reads more naturally in Python.

2. **Kubernetes client**
   Go uses k8s.io/client-go/dynamic.NewForConfig which returns a dynamic client
   for any resource including CRDs. Python's kubernetes library has an equivalent:
   DynamicClient(ApiClient()). The resource lookup is slightly different:
     Go:     s.k8s.Resource(colourWaveGVR).Namespace("").List(...)
     Python: crd_api.get()   (list across namespaces — no explicit Namespace(""))

3. **HTTP client for Harbor**
   Go uses net/http directly. Python uses the third-party `requests` library
   which has a nicer API — no manual resp.Body.Close(), JSON decoded automatically.

4. **JSON responses**
   Go builds slices of structs and json.Encodes them.
   Flask has jsonify() which calls json.dumps and sets Content-Type for you.

5. **Server struct**
   Go wraps the k8s and harbor clients in a Server struct so they can be shared
   across handlers as methods. Flask uses module-level globals (or app.config)
   instead — Python doesn't need the struct because functions are first-class
   and the module is already a shared namespace.
"""

import json
import logging
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, render_template, request
from kubernetes import client, config
from kubernetes.dynamic import DynamicClient

app = Flask(__name__)

HARBOR_URL = os.environ.get("HARBOR_URL", "https://harbor.lizardnode.com")
HARBOR_TOKEN = os.environ.get("HARBOR_TOKEN", "")


# ---------------------------------------------------------------------------
# JSON logging — same pattern as app/app.py
# ---------------------------------------------------------------------------

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
logging.getLogger("werkzeug").disabled = True


# ---------------------------------------------------------------------------
# Kubernetes client helper
# ---------------------------------------------------------------------------

def dynamic_client() -> DynamicClient:
    """
    Build a dynamic Kubernetes client.

    Go equivalent:
        cfg, _ := rest.InClusterConfig()
        k8s, _ := dynamic.NewForConfig(cfg)

    The dynamic client lets us work with CRDs (or any resource) without
    needing generated clientsets — the same reason the Go UI uses
    dynamic.NewForConfig rather than the controller-runtime manager.
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return DynamicClient(client.ApiClient())


def colourwave_api():
    """Return the dynamic resource API for ColourWave CRs."""
    return dynamic_client().resources.get(
        api_version="colourwave-python.lizardnode.com/v1alpha1",
        kind="ColourWave",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    app.logger.info("request received", extra={"method": request.method, "path": "/"})
    return render_template("index.html")


@app.route("/api/colourwaves")
def list_colourwaves():
    """
    List all ColourWave CRs across all namespaces and return a flat JSON view.

    Go equivalent: s.handleColourWaves — iterates list.Items, extracts spec
    fields via type-assertion helpers (stringField, int64Field).
    Python gets an object with attribute access (.spec.state etc.) via the
    dynamic client's resource model, so no manual type casting needed.
    """
    app.logger.info("request received", extra={"method": request.method, "path": "/api/colourwaves"})
    api = colourwave_api()
    items = api.get()
    result = [
        {
            "name": item.metadata.name,
            "namespace": item.metadata.namespace,
            "state": item.spec.get("state", ""),
            "version": item.spec.get("version", ""),
            "colour": item.spec.get("colour", ""),
            "replicas": item.spec.get("replicas", 0),
        }
        for item in items.items
    ]
    return jsonify(result)


@app.route("/api/colourwaves/patch", methods=["POST"])
def patch_colourwave():
    """
    Apply a merge patch to a ColourWave CR.

    Go equivalent: s.handlePatch — encodes a map to JSON and calls
    s.k8s.Resource(...).Namespace(...).Patch(..., types.MergePatchType, ...)
    Python's dynamic client accepts a plain dict and a content_type string.
    """
    app.logger.info("request received", extra={"method": request.method, "path": "/api/colourwaves/patch"})
    body = request.get_json()

    api = colourwave_api()
    api.patch(
        name=body["name"],
        namespace=body["namespace"],
        body={
            "spec": {
                "state": body["state"],
                "version": body["version"],
                "replicas": body["replicas"],
                "colour": body["colour"],
            }
        },
        content_type="application/merge-patch+json",
    )
    return "", 200


@app.route("/api/tags")
def list_tags():
    """
    Proxy Harbor's artifact API and return sorted image tags.

    Go equivalent: s.handleTags — uses net/http directly.
    Python uses the `requests` library: cleaner API, no manual body close.
    """
    app.logger.info("request received", extra={"method": request.method, "path": "/api/tags"})
    url = f"{HARBOR_URL}/api/v2.0/projects/colourwave/repositories/app-py/artifacts?with_tag=true&page_size=50"
    headers = {}
    if HARBOR_TOKEN:
        headers["Authorization"] = f"Basic {HARBOR_TOKEN}"

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()

    artifacts = resp.json()
    tags = sorted(
        [tag["name"] for artifact in artifacts for tag in (artifact.get("tags") or [])],
        reverse=True,
    )
    return jsonify(tags)


if __name__ == "__main__":
    # Development only — production uses gunicorn (see Containerfile)
    app.run(host="0.0.0.0", port=8080)
