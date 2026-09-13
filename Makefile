SHELL := /bin/sh
.PHONY: configure validate audit up down ui
configure:
	python3 scripts/configure.py
validate:
	./scripts/validate.sh
audit:
	./scripts/audit-public
up: configure
	./scripts/compose.sh up -d --build
down:
	./scripts/compose.sh down
ui:
	./scripts/compose.sh --profile ui up -d grafana
