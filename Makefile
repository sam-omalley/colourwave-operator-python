# ── Variables ─────────────────────────────────────────────────────────────────
# Override any of these on the command line, e.g.:
#   make build TAG=v0.2.0
#   make build REGISTRY=my.registry.io/colourwave PLATFORM=linux/arm64

REGISTRY  ?= harbor.lizardnode.com/colourwave
TAG       ?= v0.1.0
PLATFORM  ?= linux/amd64

APP_IMAGE      := $(REGISTRY)/app-py:$(TAG)
OPERATOR_IMAGE := $(REGISTRY)/operator-py:$(TAG)
UI_IMAGE       := $(REGISTRY)/ui-py:$(TAG)

# Dev ports — app and UI use different ports so both can run simultaneously
APP_PORT ?= 8080
UI_PORT  ?= 8081

.DEFAULT_GOAL := help

# ── Help ──────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@printf "\nUsage: make <target> [VAR=value ...]\n\n"
	@printf "Variables (current values):\n"
	@printf "  REGISTRY  = $(REGISTRY)\n"
	@printf "  TAG       = $(TAG)\n"
	@printf "  PLATFORM  = $(PLATFORM)\n\n"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@printf "\n"

# ── Build ─────────────────────────────────────────────────────────────────────

.PHONY: build build-app build-operator build-ui

build: build-app build-operator build-ui ## Build all three images

build-app: ## Build the demo app image
	podman build --platform $(PLATFORM) -t $(APP_IMAGE) app/

build-operator: ## Build the operator image
	podman build --platform $(PLATFORM) -t $(OPERATOR_IMAGE) operator/

build-ui: ## Build the control-panel UI image
	podman build --platform $(PLATFORM) -t $(UI_IMAGE) ui/

# ── Push ──────────────────────────────────────────────────────────────────────

.PHONY: push push-app push-operator push-ui

push: push-app push-operator push-ui ## Push all three images to the registry

push-app: ## Push the demo app image
	podman push $(APP_IMAGE)

push-operator: ## Push the operator image
	podman push $(OPERATOR_IMAGE)

push-ui: ## Push the control-panel UI image
	podman push $(UI_IMAGE)

# ── Release ───────────────────────────────────────────────────────────────────

.PHONY: release release-app release-operator release-ui

release: build push ## Build and push all images (convenience: make release TAG=v0.2.0)

release-app: build-app push-app ## Build and push the app image only

release-operator: build-operator push-operator ## Build and push the operator image only

release-ui: build-ui push-ui ## Build and push the UI image only

# ── CRD ───────────────────────────────────────────────────────────────────────

.PHONY: install-crd uninstall-crd

install-crd: ## Install the CRD into the current cluster
	kubectl apply -f crd/colourwave-python.lizardnode.com_colourwaves.yaml

uninstall-crd: ## Remove the CRD from the current cluster
	kubectl delete -f crd/colourwave-python.lizardnode.com_colourwaves.yaml

# ── Dev ───────────────────────────────────────────────────────────────────────
# These run the components locally against your current kubeconfig context.
# app and UI use different ports (8080/8081) so they can run simultaneously.
# The operator talks to the cluster directly — the CRD must be installed first.

.PHONY: dev-app dev-ui dev-operator

dev-app: ## Run the demo app locally on port $(APP_PORT)
	cd app && COLOUR="\#3498db" VERSION=dev flask --app app run --port $(APP_PORT)

dev-ui: ## Run the control-panel UI locally on port $(UI_PORT)
	cd ui && flask --app app run --port $(UI_PORT)

dev-operator: ## Run the operator locally against the current cluster (verbose)
	cd operator && kopf run --all-namespaces --verbose main.py
