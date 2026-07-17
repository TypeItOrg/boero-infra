COMPOSE := docker compose
ENV ?= staging
ENV_FILE := .env.$(ENV)
COMPOSE_FILE := compose.$(ENV).yaml
LOCK_FILE := /tmp/boero-infra-$(ENV).lock
VOLUME_SUFFIX := $(if $(filter production,$(ENV)),prod,$(ENV))

.DEFAULT_GOAL := status

.PHONY: prepare bootstrap deploy-ui deploy-api rollback-ui rollback-api status logs down test

prepare:
	docker volume create boero-ui-next-cache-$(VOLUME_SUFFIX)
	docker volume create boero-api-postgres-data-$(VOLUME_SUFFIX)
	docker volume create boero-api-redis-data-$(VOLUME_SUFFIX)

bootstrap: prepare
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) pull
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d --wait --wait-timeout 180

deploy-ui:
	@test -n "$(VERSION)" || (echo "VERSION is required" >&2; exit 1)
	flock $(LOCK_FILE) ./scripts/deploy-service.sh $(ENV) ui $(VERSION)

deploy-api:
	@test -n "$(VERSION)" || (echo "VERSION is required" >&2; exit 1)
	flock $(LOCK_FILE) ./scripts/deploy-service.sh $(ENV) api $(VERSION)

rollback-ui:
	flock $(LOCK_FILE) ./scripts/rollback-service.sh $(ENV) ui

rollback-api:
	flock $(LOCK_FILE) ./scripts/rollback-service.sh $(ENV) api

status:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) ps

logs:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) logs -f --tail=200

down:
	$(COMPOSE) --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down --remove-orphans

test:
	./tests/deploy-service.test.sh
