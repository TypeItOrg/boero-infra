COMPOSE := docker compose
ENV ?= staging
ENV_FILE := .env.$(ENV)
BASE_COMPOSE_FILE := compose.yaml
ENV_COMPOSE_FILE := compose.$(ENV).yaml
COMPOSE_ARGS := --env-file $(ENV_FILE) -f $(BASE_COMPOSE_FILE) -f $(ENV_COMPOSE_FILE)
LOCK_FILE := /tmp/boero-infra-$(ENV).lock
VOLUME_SUFFIX := $(if $(filter production,$(ENV)),prod,$(ENV))

.DEFAULT_GOAL := status

.PHONY: prepare preflight bootstrap deploy-ui deploy-api rollback-ui rollback-api backup-db status logs logs-api logs-api-file logs-api-request down test

prepare:
	docker volume create boero-ui-next-cache-$(VOLUME_SUFFIX)
	docker volume create boero-api-postgres-data-$(VOLUME_SUFFIX)
	docker volume create boero-api-redis-data-$(VOLUME_SUFFIX)
	docker volume create boero-api-logs-$(VOLUME_SUFFIX)


preflight:
	@test -f "$(ENV_FILE)" || (echo "Missing $(ENV_FILE)" >&2; exit 1)
	$(COMPOSE) $(COMPOSE_ARGS) config --quiet

bootstrap: preflight prepare
	$(COMPOSE) $(COMPOSE_ARGS) pull
	$(COMPOSE) $(COMPOSE_ARGS) up -d --wait --wait-timeout 180

deploy-ui:
	@test -n "$(VERSION)" || (echo "VERSION is required" >&2; exit 1)
	flock $(LOCK_FILE) ./scripts/deploy-service.sh $(ENV) ui $(VERSION)

deploy-api: prepare
	@test -n "$(VERSION)" || (echo "VERSION is required" >&2; exit 1)
	flock $(LOCK_FILE) ./scripts/deploy-service.sh $(ENV) api $(VERSION)

rollback-ui:
	flock $(LOCK_FILE) ./scripts/rollback-service.sh $(ENV) ui

rollback-api: prepare
	flock $(LOCK_FILE) ./scripts/rollback-service.sh $(ENV) api

backup-db:
	flock $(LOCK_FILE) ./scripts/backup-postgres.sh $(ENV)


status:
	$(COMPOSE) $(COMPOSE_ARGS) ps

logs:
	$(COMPOSE) $(COMPOSE_ARGS) logs -f --tail=200

logs-api:
	$(COMPOSE) $(COMPOSE_ARGS) logs -f --tail=200 api

logs-api-file:
	$(COMPOSE) $(COMPOSE_ARGS) exec api tail -f /app/logs/boero-api.log

logs-api-request:
	@test -n "$(REQUEST_ID)" || (echo "REQUEST_ID is required" >&2; exit 1)
	$(COMPOSE) $(COMPOSE_ARGS) exec api grep "$(REQUEST_ID)" /app/logs/boero-api.log

down:
	$(COMPOSE) $(COMPOSE_ARGS) down --remove-orphans

test:
	./tests/deploy-service.test.sh
