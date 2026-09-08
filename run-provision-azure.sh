#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ -f .env.local ]]; then
  set -a
  source .env.local
  set +a
fi

: "${HCP_TERRAFORM_TOKEN:?Preencha HCP_TERRAFORM_TOKEN no .env.local}"
input="${1:-${PROVISION_AZURE_INPUT:-examples/provision-azure.local.input.json}}"
if [[ ! -f "$input" ]]; then
  echo "Arquivo de entrada não encontrado: $input" >&2
  exit 1
fi
jar="runner/target/ows-runner-0.1.0.jar"
if [[ ! -f "$jar" ]]; then
  echo "Compile o runner: mvn -f runner/pom.xml --batch-mode --no-transfer-progress clean package" >&2
  exit 1
fi
exec java -jar "$jar" run workflows/provision-azure.yaml --input "$input"
