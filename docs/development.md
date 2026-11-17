# Développement et tests hors caméra

Go 1.26+ ; depuis la racine :

```sh
go test ./...
go test -race ./...
go vet ./...
go build ./cmd/netvue-bridge
```

Les tests Go ordinaires utilisent des données fictives ou des pairs locaux.
Le test vidéo MediaMTX/FFmpeg est documenté dans le
[guide du pont](./bridge.md).

## Home Assistant

Dépendances Python dans [`tests/requirements-homeassistant.txt`](../tests/requirements-homeassistant.txt) :

```sh
python -m pip install -r tests/requirements-homeassistant.txt
python -B -m unittest discover -s tests -p test_home_assistant_integration.py -v
```

Optionnellement, lancer les tests dans l'image HA officielle, sans réseau et
avec le dépôt en lecture seule :

```sh
docker run --rm --network none \
  --mount "type=bind,source=$(pwd),target=/workspace,readonly" \
  --workdir /workspace --env PYTHONPATH=/workspace \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.11.2 \
  -B -m unittest discover -s tests -p 'test_*.py' -v
```

`tests/ha_integration_synthetic.py` vérifie HA et un flux fictif avec
MediaMTX/FFmpeg via un faux manager HTTP (pas le gestionnaire Go). Définir
`NETVUE_TEST_COMPONENT`, `NETVUE_TEST_MEDIAMTX`,
`NETVUE_TEST_MEDIAMTX_CONFIG` et `NETVUE_TEST_FFMPEG` avec des chemins locaux.
La config de référence est `deployment/mediamtx.yml`. Zeroconf exige une
interface IPv4 ; sous Docker, utiliser un réseau bridge temporaire `--internal`,
sans ports publiés.

`tests/ha_go_e2e_synthetic.py` teste le vrai binaire Go `serve`, un pair Netvue
fictif, MediaMTX et HA : rejet du premier login, erreur HA, reprise explicite,
RTSP, image JPEG et suppression de session. Le script est annulé après 120 s,
hors nettoyage. Exécuter uniquement dans un conteneur sur un réseau Docker
temporaire `--internal`, sans ports publiés ; le pair utilise l'IPv4 privée du
conteneur. Requiert FFmpeg dans l'image HA, MediaMTX Linux et le binaire Go
Linux compilé depuis le code testé. Monter sources et binaires en lecture seule,
configurer les quatre variables ci-dessous avec chemins **dans le conteneur**,
puis supprimer le réseau :
`NETVUE_TEST_COMPONENT`, `NETVUE_TEST_MEDIAMTX`,
`NETVUE_TEST_MEDIAMTX_CONFIG`, `NETVUE_TEST_GO_BRIDGE`.

Exemple Linux après définition de `MEDIAMTX_BIN` (binaire Linux/amd64
MediaMTX 1.21.4) :

```sh
(
set -eu
: "${MEDIAMTX_BIN:?indiquer le chemin du binaire MediaMTX Linux}"
go_binary=$(mktemp /tmp/netvue-go-e2e.XXXXXX)
network="netvue-go-e2e-$(basename "$go_binary")"
trap 'docker network rm "$network" >/dev/null 2>&1; rm -f "$go_binary"' EXIT
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o "$go_binary" ./cmd/netvue-bridge
docker network create --internal "$network"
docker run --rm --network "$network" \
  --mount "type=bind,source=$(pwd),target=/workspace,readonly" \
  --mount "type=bind,source=$go_binary,target=/netvue-bridge,readonly" \
  --mount "type=bind,source=$MEDIAMTX_BIN,target=/mediamtx,readonly" \
  --workdir /workspace --env PYTHONPATH=/workspace \
  --env NETVUE_TEST_COMPONENT=/workspace/custom_components/netvue_local \
  --env NETVUE_TEST_MEDIAMTX=/mediamtx \
  --env NETVUE_TEST_MEDIAMTX_CONFIG=/workspace/deployment/mediamtx.yml \
  --env NETVUE_TEST_GO_BRIDGE=/netvue-bridge \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.11.2 \
  -B tests/ha_go_e2e_synthetic.py
)
```

Ces tests utilisent un réseau isolé, des données fictives et des ports libres.
Les résultats ne prouvent ni la compatibilité matérielle ni la stabilité
prolongée. Expurger les journaux avant partage.

## Docker

Le [guide Portainer](../deployment/go-portainer.md) explique l'image et le
rendu local de stack, sans déploiement. Le workflow teste Go et HA puis publie
sur `main` ; `test_portainer_config.py` compare les variables MediaMTX à la
référence. `.dockerignore` limite le contexte aux sources Go requises.
