export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

CONFIG ?= configs/problem1.yaml
INSTALLED := third_party/.installed

.PHONY: image setup run

image:
	docker compose build

setup: image
	docker compose run --rm --entrypoint scripts/install.sh simulator

$(INSTALLED): scripts/install.sh
	$(MAKE) setup

run: image $(INSTALLED)
	docker compose run --rm simulator $(CONFIG)
