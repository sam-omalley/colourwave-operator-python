import json
import logging
import os
import socket
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request
from kubernetes import client, config
from kubernetes.dynamic import DynamicClient

app = Flask(__name__)

VERSION = os.environ.get("VERSION", "unknown")
COLOUR = os.environ.get("COLOUR", "#cccccc")
HOSTNAME = socket.gethostname()


# ---------------------------------------------------------------------------
# JSON logging
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
# Kubernetes helpers
# ---------------------------------------------------------------------------

def k8s_clients():
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return DynamicClient(client.ApiClient()), client.CoreV1Api()


def pod_status(pod) -> str:
    """Reduce a pod object to a simple display status string."""
    # Terminating: kubelet has set a deletion timestamp but the pod is still running
    if pod.metadata.deletion_timestamp:
        return "terminating"
    phase = pod.status.phase or "Unknown"
    if phase == "Running":
        statuses = pod.status.container_statuses or []
        if statuses and all(cs.ready for cs in statuses):
            return "running"
        return "starting"   # running but containers not yet ready
    if phase == "Pending":
        return "pending"
    if phase in ("Failed", "Unknown"):
        return "error"
    if phase == "Succeeded":
        return "stopped"
    return "unknown"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    app.logger.info("request received", extra={"method": request.method, "path": "/"})
    return render_template("index.html", version=VERSION, colour=COLOUR, hostname=HOSTNAME)


@app.route("/api/instances")
def api_instances():
    """
    Return all ColourWave instances with their live pod states.
    The frontend polls this every 2 s to show the ripple effect of
    deployment changes (pods appearing, terminating, becoming ready).
    """
    app.logger.info("request received", extra={"method": request.method, "path": "/api/instances"})
    try:
        dyn, core_v1 = k8s_clients()
        crd_api = dyn.resources.get(
            api_version="colourwave-python.lizardnode.com/v1alpha1",
            kind="ColourWave",
        )

        result = []
        for item in crd_api.get().items:
            name = item.metadata.name
            namespace = item.metadata.namespace
            spec = item.spec

            pods = core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"app={name}",
            )

            result.append({
                "name": name,
                "namespace": namespace,
                "colour": spec.get("colour", "#cccccc"),
                "version": spec.get("version", ""),
                "state": spec.get("state", ""),
                "replicas": spec.get("replicas", 0),
                "pods": [
                    {
                        "name": pod.metadata.name,
                        "status": pod_status(pod),
                        "restarts": sum(
                            cs.restart_count
                            for cs in (pod.status.container_statuses or [])
                        ),
                    }
                    for pod in pods.items
                ],
            })

        return jsonify(result)

    except Exception as e:
        app.logger.error("failed to list instances: %s", e)
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # Development only — production uses gunicorn (see Containerfile)
    app.run(host="0.0.0.0", port=8080)
