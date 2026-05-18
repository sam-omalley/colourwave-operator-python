# colourwave-operator-python

A Python port of the [colourwave-operator](https://github.com/sam-omalley/colourwave-operator) project.
Same three components — demo app, Kubernetes operator, control-panel UI — but
implemented with Flask, kopf, and the Kubernetes Python client instead of Go,
kubebuilder, and controller-runtime.

This document focuses on **what changed and why**. Read the Go project's README
first if you want the background on the ColourWave concept itself.

---

## Components

| Component | Go | Python |
|---|---|---|
| Demo app | `net/http` + `html/template` | Flask + Jinja2 |
| Operator | kubebuilder / controller-runtime | kopf |
| Control-panel UI | `net/http` + `dynamic.Client` | Flask + `DynamicClient` |
| Production server | built-in HTTP server | gunicorn |
| Image base | `distroless/static-debian12` | `python:3.12-slim` |

---

## Running locally (devcontainer)

Open the repo in VS Code, choose **Reopen in Container**, and the devcontainer
will install all three `requirements.txt` files automatically.

```bash
# App
cd app && flask run --port 8080

# Operator (needs a cluster with the CRD installed)
cd operator && kopf run --all-namespaces main.py

# UI (needs a cluster to talk to)
cd ui && flask run --port 8080
```

---

## Building and pushing images

Each component builds from its own directory (no shared module like Go's `go.mod`):

```bash
# App
podman build --platform linux/amd64 -t harbor.lizardnode.com/colourwave/app-py:v0.1.0 app/
podman push harbor.lizardnode.com/colourwave/app-py:v0.1.0

# Operator
podman build --platform linux/amd64 -t harbor.lizardnode.com/colourwave/operator-py:v0.1.0 operator/
podman push harbor.lizardnode.com/colourwave/operator-py:v0.1.0

# UI
podman build --platform linux/amd64 -t harbor.lizardnode.com/colourwave/ui-py:v0.1.0 ui/
podman push harbor.lizardnode.com/colourwave/ui-py:v0.1.0
```

> **Note:** `--platform linux/amd64` is required when building on Apple Silicon.
> The homelab cluster runs on amd64.

---

## Installing the CRD

The CRD is the same as the Go version (it describes the API contract, not the
implementation language). If the Go operator's CRD is already installed you can
skip this step.

```bash
kubectl apply -f crd/colourwave-python.lizardnode.com_colourwaves.yaml
```

---

## Key differences from the Go version

### App

**Templates at runtime vs compile-time**

Go uses `//go:embed templates/` to bundle the template into the binary at
compile time. At runtime the template is already in memory.

Python/Flask reads template files from the `templates/` directory on disk at
request time (with caching in production). There is no embedding step — the
directory just has to exist next to `app.py`.

```go
// Go: embed at compile time
//go:embed templates/index.html
var tmplFS embed.FS
```

```python
# Python: Flask finds templates/ automatically at runtime
return render_template("index.html", version=VERSION, colour=COLOUR, hostname=HOSTNAME)
```

**Template syntax**

| Go `html/template` | Jinja2 |
|---|---|
| `{{.Version}}` | `{{ version }}` |
| `{{.Colour}}` | `{{ colour }}` |
| `{{.Hostname}}` | `{{ hostname }}` |

**Production server**

Go's `http.ListenAndServe` is production-grade — the binary is its own server.
Python's built-in `flask run` is for development only. In production we use
gunicorn, a battle-tested WSGI server.

---

### Operator

**Single Reconcile vs decorator handlers**

Go/kubebuilder has one `Reconcile(ctx, req)` function. The framework calls it
for every event and on startup (via its internal cache sync).

kopf uses decorators. We stack three onto the same function to mirror the Go
mental model:

```python
@kopf.on.create("colourwave-python.lizardnode.com", "v1alpha1", "colourwaves")
@kopf.on.update("colourwave-python.lizardnode.com", "v1alpha1", "colourwaves")
@kopf.on.resume("colourwave-python.lizardnode.com", "v1alpha1", "colourwaves")
def reconcile(spec, name, namespace, body, **kwargs):
    ...
```

**on.resume is the startup sync**

kubebuilder automatically re-queues all existing CRs when the operator starts
(cache warm-up). kopf does *not* do this unless you add `@kopf.on.resume`.
Without it, existing instances would be ignored until their next change.

**No controllerutil.CreateOrUpdate**

controller-runtime ships `CreateOrUpdate` — try to create; if it exists, update.
The Python client has no equivalent. We implement it manually with try/except:

```python
try:
    apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)
except client.exceptions.ApiException as e:
    if e.status == 409:  # Already exists
        apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=deployment)
    else:
        raise
```

**Owner references: kopf.adopt vs SetControllerReference**

```go
// Go
ctrl.SetControllerReference(&colourWave, deployment, r.Scheme)
```

```python
# Python
kopf.adopt(deployment, owner=body)
```

Both set `ownerReferences` on the child resource so it is garbage-collected
when the parent CR is deleted.

**Running the operator**

```bash
# Go: compiled binary
./manager

# Python: interpreted
kopf run --all-namespaces main.py
```

---

### UI

**Routing**

```go
// Go: function registration
http.HandleFunc("/api/colourwaves", server.handleColourWaves)
```

```python
# Python: decorator
@app.route("/api/colourwaves")
def list_colourwaves():
    ...
```

**No Server struct**

Go wraps the k8s and Harbor clients in a `Server` struct so handlers can access
them as methods. Python functions close over module-level state — we build the
client inside each handler (it's cheap; the underlying connection is reused by
the HTTP client pool).

**Dynamic client**

```go
// Go
k8s, _ := dynamic.NewForConfig(cfg)
k8s.Resource(colourWaveGVR).Namespace("").List(ctx, metav1.ListOptions{})
```

```python
# Python
dyn = DynamicClient(client.ApiClient())
api = dyn.resources.get(api_version="colourwave-python.lizardnode.com/v1alpha1", kind="ColourWave")
api.get()  # lists across all namespaces
```

**HTTP client for Harbor**

Go uses `net/http` directly. Python uses the `requests` library — cleaner API,
no manual `resp.Body.Close()`, JSON decoded by calling `.json()`.

---

## CRD

The CRD YAML in `crd/` is identical to the Go version. CRDs describe the API
schema — they are language-agnostic. Any operator that watches the same group
(`colourwave-python.lizardnode.com`) and version (`v1alpha1`) can manage the same CRs.
