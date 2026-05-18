"""
ColourWave Operator — Python/kopf port of the Go/kubebuilder operator.

Key differences from the Go version
-------------------------------------
1. **Handlers vs Reconcile loop**
   Go/kubebuilder has a single `Reconcile(ctx, req)` function that the framework
   calls for every event. kopf uses decorators instead: each handler is a separate
   function decorated with @kopf.on.<event>. We stack all three events onto one
   function to keep the same "one reconcile function" mental model.

2. **Startup sync (on.resume)**
   kubebuilder automatically re-queues all existing CRs when the operator starts.
   kopf does NOT do this unless you explicitly add @kopf.on.resume. Without it,
   running instances would be ignored until their next change event.

3. **CreateOrUpdate**
   controller-runtime has a handy controllerutil.CreateOrUpdate. The kubernetes
   Python client has no equivalent — we try to create, catch the 409 Conflict
   error if it already exists, and fall back to a patch instead.

4. **Owner references**
   Go uses ctrl.SetControllerReference(&colourWave, obj, r.Scheme).
   kopf exposes kopf.adopt(obj, owner=body) which does the same thing: sets
   ownerReferences so child resources are garbage-collected with the parent CR.

5. **Type safety**
   Go's generated types give compile-time field names. In Python we use the
   kubernetes client's typed model classes (V1Deployment etc.) where possible,
   and fall back to plain dicts for the CRD spec (which has no generated class).

6. **Running the operator**
   Go compiles to a binary: `./manager`. kopf runs interpreted:
   `kopf run --all-namespaces main.py`
"""

import logging

import kopf
from kubernetes import client, config

logger = logging.getLogger(__name__)

IMAGE_REPO = "harbor.lizardnode.com/colourwave/app-py"
DOMAIN = "lizardnode.com"


# ---------------------------------------------------------------------------
# Kubernetes client
# ---------------------------------------------------------------------------

def k8s_clients():
    """
    Load cluster config and return typed API clients.

    Mirrors the Go pattern:
        cfg, _ := rest.InClusterConfig()     // try in-cluster
        cfg, _ = clientcmd.BuildConfigFromFlags(...)  // fall back to kubeconfig

    The kubernetes Python client does the same with load_incluster_config() /
    load_kube_config().
    """
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return (
        client.AppsV1Api(),
        client.CoreV1Api(),
        client.NetworkingV1Api(),
    )


# ---------------------------------------------------------------------------
# Builder functions — mirror buildDeployment/buildService/buildIngress in Go
# ---------------------------------------------------------------------------

def desired_replicas(spec: dict) -> int:
    """Return 0 if stopped, otherwise the configured replica count."""
    if spec.get("state") == "stopped":
        return 0
    return int(spec.get("replicas", 1))


def build_deployment(name: str, namespace: str, spec: dict, body: dict) -> client.V1Deployment:
    replicas = desired_replicas(spec)
    version = spec.get("version", "latest")
    colour = spec.get("colour", "#cccccc")

    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        spec=client.V1DeploymentSpec(
            replicas=replicas,
            selector=client.V1LabelSelector(match_labels={"app": name}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": name}),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="colourwave",
                            image=f"{IMAGE_REPO}:{version}",
                            env=[
                                client.V1EnvVar(name="VERSION", value=version),
                                client.V1EnvVar(name="COLOUR", value=colour),
                            ],
                        )
                    ]
                ),
            ),
        ),
    )
    # kopf.adopt sets ownerReferences on the child so it is garbage-collected
    # when the parent CR is deleted. Equivalent to ctrl.SetControllerReference
    # in controller-runtime.
    kopf.adopt(deployment, owner=body)
    return deployment


def build_service(name: str, namespace: str, body: dict) -> client.V1Service:
    service = client.V1Service(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        spec=client.V1ServiceSpec(
            selector={"app": name},
            ports=[client.V1ServicePort(port=8080, target_port=8080, protocol="TCP")],
        ),
    )
    kopf.adopt(service, owner=body)
    return service


def build_ingress(name: str, namespace: str, body: dict) -> client.V1Ingress:
    hostname = f"{name}.{DOMAIN}"
    ingress = client.V1Ingress(
        metadata=client.V1ObjectMeta(
            name=name,
            namespace=namespace,
            annotations={
                "traefik.ingress.kubernetes.io/router.tls": "true",
                "traefik.ingress.kubernetes.io/router.tls.certresolver": "myresolver",
                "traefik.ingress.kubernetes.io/router.middlewares": "kube-system-cors-policy@kubernetescrd",
            },
        ),
        spec=client.V1IngressSpec(
            rules=[
                client.V1IngressRule(
                    host=hostname,
                    http=client.V1HTTPIngressRuleValue(
                        paths=[
                            client.V1HTTPIngressPath(
                                path="/",
                                path_type="Prefix",
                                backend=client.V1IngressBackend(
                                    service=client.V1IngressServiceBackend(
                                        name=name,
                                        port=client.V1ServiceBackendPort(number=8080),
                                    )
                                ),
                            )
                        ]
                    ),
                )
            ]
        ),
    )
    kopf.adopt(ingress, owner=body)
    return ingress


# ---------------------------------------------------------------------------
# Create-or-patch helpers
# ---------------------------------------------------------------------------
# controller-runtime has controllerutil.CreateOrUpdate which handles this for
# us. The Python client has no equivalent, so we implement the pattern manually:
# try to create; if the resource already exists (HTTP 409) patch it instead.

def create_or_patch_deployment(apps_v1, name, namespace, deployment):
    try:
        apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)
        logger.info("deployment created: %s/%s", namespace, name)
    except client.exceptions.ApiException as e:
        if e.status == 409:
            apps_v1.patch_namespaced_deployment(name=name, namespace=namespace, body=deployment)
            logger.info("deployment patched: %s/%s", namespace, name)
        else:
            raise


def create_or_patch_service(core_v1, name, namespace, service):
    try:
        core_v1.create_namespaced_service(namespace=namespace, body=service)
        logger.info("service created: %s/%s", namespace, name)
    except client.exceptions.ApiException as e:
        if e.status == 409:
            core_v1.patch_namespaced_service(name=name, namespace=namespace, body=service)
            logger.info("service patched: %s/%s", namespace, name)
        else:
            raise


def create_or_patch_ingress(net_v1, name, namespace, ingress):
    try:
        net_v1.create_namespaced_ingress(namespace=namespace, body=ingress)
        logger.info("ingress created: %s/%s", namespace, name)
    except client.exceptions.ApiException as e:
        if e.status == 409:
            net_v1.patch_namespaced_ingress(name=name, namespace=namespace, body=ingress)
            logger.info("ingress patched: %s/%s", namespace, name)
        else:
            raise


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
# In Go/kubebuilder there is ONE Reconcile function. kopf uses decorators.
# We stack on.create + on.update + on.resume onto the same function to match
# that pattern. on.resume is what fires for objects that already exist when
# the operator starts — kubebuilder does this automatically, kopf requires it
# to be explicit.

@kopf.on.create("colourwave-python.lizardnode.com", "v1alpha1", "colourwaves")
@kopf.on.update("colourwave-python.lizardnode.com", "v1alpha1", "colourwaves")
@kopf.on.resume("colourwave-python.lizardnode.com", "v1alpha1", "colourwaves")
def reconcile(spec, name, namespace, body, **kwargs):
    """
    Reconcile a ColourWave CR: create or update a Deployment, Service,
    and Ingress to match the desired spec.
    """
    logger.info("reconciling %s/%s", namespace, name)

    apps_v1, core_v1, net_v1 = k8s_clients()

    deployment = build_deployment(name, namespace, spec, body)
    create_or_patch_deployment(apps_v1, name, namespace, deployment)

    service = build_service(name, namespace, body)
    create_or_patch_service(core_v1, name, namespace, service)

    ingress = build_ingress(name, namespace, body)
    create_or_patch_ingress(net_v1, name, namespace, ingress)

    # Returning a dict stores it in the CR's status.reconcile field.
    # In the Go version we don't update status, but it's cheap here.
    return {"state": spec.get("state"), "version": spec.get("version")}
