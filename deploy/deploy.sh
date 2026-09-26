#!/usr/bin/env bash
set -euo pipefail

deploy_sha="${1:?commit SHA is required}"

actual_sha="$(git rev-parse HEAD)"
if [[ "$actual_sha" != "$deploy_sha" ]]; then
  echo "Expected $deploy_sha but checked out $actual_sha" >&2
  exit 1
fi

docker compose up -d --build --remove-orphans

for attempt in $(seq 1 30); do
  if docker compose exec -T tapnap python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3).read(1)" \
    >/dev/null 2>&1; then
    docker compose ps
    echo "TapNap deployed at $deploy_sha"
    exit 0
  fi

  echo "Waiting for TapNap to become healthy ($attempt/30)"
  sleep 2
done

docker compose ps
docker compose logs --tail=100 tapnap
echo "TapNap did not become healthy after deployment." >&2
exit 1
