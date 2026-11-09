#!/usr/bin/env bash
# Prépare le déploiement du projet Docker « saphyra » via l'API Hostinger (vps_docker_create).
# L'API limite le compose (content) et les variables (environment) à 8 192 caractères chacun :
#   - le code compressé est coupé en deux : APP_B64_1 va dans les variables, APP_B64_2 est inscrit dans le compose ;
#   - les commentaires du compose sont retirés.
# Aucun secret : mots de passe et token MCP sont générés sur le VPS (service init).
# Usage : scripts/render-compose.sh <dossier de sortie>   -> compose.yml et env
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:?dossier de sortie}"; mkdir -p "$OUT"
B64="$(gzip -9cn server/server.mjs | base64 -w0)"
CUT=8000
P1="${B64:0:$CUT}"; P2="${B64:$CUT}"
grep -v '^\s*#' vps/docker-compose.yml | sed -e "s|\${APP_B64_2}|\"${P2}\"|" > "$OUT/compose.yml"
printf 'APP_B64_1=%s\n' "$P1" > "$OUT/env"
for f in compose.yml env; do n=$(wc -c < "$OUT/$f"); echo "$f : $n caractères" >&2; [ "$n" -le 8192 ] || { echo "ERREUR : $f dépasse 8192" >&2; exit 1; }; done
