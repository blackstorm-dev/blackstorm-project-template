export SOPS_AGE_KEY_FILE := $(CURDIR)/age.key

.PHONY: init secrets

# Ejecutar desde el clon del proyecto: gh usa su repositorio, no el del template.
init:
	git config core.hooksPath .githooks
	@gh auth status >/dev/null 2>&1 || { echo "Primero ejecutá gh auth login"; exit 1; }
	bash scripts/init-sops
	gh secret set SOPS_AGE_KEY --repo "$$(gh repo view "$$(git remote get-url origin)" --json nameWithOwner --jq .nameWithOwner)" < age.key

secrets:
	@test -n "$(FILE)" || { echo "Uso: make secrets FILE=ruta/al/archivo"; exit 1; }
	bash scripts/edit-secret "$(FILE)"
