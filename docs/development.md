# Développement et tests hors caméra

Depuis la racine, avec Go 1.26 ou supérieur :

```sh
go test ./...
go test -race ./...
go vet ./...
go build ./cmd/netvue-bridge
```

Les tests Go ordinaires utilisent des données fictives et des pairs locaux.
Les tests vidéo optionnels utilisent MediaMTX et FFmpeg ; voir le
[guide du pont](./bridge.md) pour les variables et commandes. Ne fournissez
jamais aux tests une adresse de caméra réelle.

## Tests Home Assistant

Les tests d'intégration Python sont conservés dans
[`tests/`](../tests/). Les dépendances de test sont indiquées dans
[`requirements-homeassistant.txt`](../tests/requirements-homeassistant.txt).
Dans un environnement Python compatible isolé :

```sh
python -m pip install -r tests/requirements-homeassistant.txt
python -B -m unittest discover -s tests -p test_home_assistant_integration.py -v
```

On peut aussi exécuter les tests avec l'image officielle Home Assistant en
montant le dépôt en lecture seule et sans réseau :

```sh
docker run --rm --network none \
  --mount "type=bind,source=$(pwd),target=/workspace,readonly" \
  --workdir /workspace --env PYTHONPATH=/workspace \
  --entrypoint python ghcr.io/home-assistant/home-assistant:2026.11.2 \
  -B -m unittest discover -s tests -p 'test_*.py' -v
```

[`ha_integration_synthetic.py`](../tests/ha_integration_synthetic.py) teste
également l'intégration HA et un flux fictif avec MediaMTX et FFmpeg. Il
utilise un faux manager HTTP, pas le gestionnaire Go. Configurer les variables
`NETVUE_TEST_COMPONENT`, `NETVUE_TEST_MEDIAMTX`,
`NETVUE_TEST_MEDIAMTX_CONFIG` et `NETVUE_TEST_FFMPEG` avec des chemins locaux ;
la configuration MediaMTX fournie est
[`deployment/mediamtx.yml`](../deployment/mediamtx.yml). Ce scénario exige
une interface IPv4 pour Zeroconf ; en Docker, employer un réseau bridge
`--internal` temporaire, sans ports publiés.

[`ha_go_e2e_synthetic.py`](../tests/ha_go_e2e_synthetic.py) va plus loin : il
utilise le véritable binaire Go `serve`, un pair Netvue fabriqué, MediaMTX et
Home Assistant. Le premier login fictif est rejeté ; le test vérifie l'erreur
HA, la reprise explicite, la publication RTSP, une image JPEG et la suppression
de la session. Le script est annulé après 120 secondes ; le nettoyage peut
prolonger cette durée. Il doit être
exécuté dans un conteneur de test sur un réseau Docker temporaire `--internal`,
sans ports publiés : la caméra fictive utilise exclusivement l'IPv4 privée du
conteneur lui-même. Il requiert FFmpeg dans l'image HA, un binaire Linux
MediaMTX et le binaire Go Linux compilé depuis le code à tester. Monter les
sources et binaires en lecture seule et définir `NETVUE_TEST_COMPONENT`,
`NETVUE_TEST_MEDIAMTX`, `NETVUE_TEST_MEDIAMTX_CONFIG` et
`NETVUE_TEST_GO_BRIDGE` avec les chemins **dans le conteneur**. Supprimer le
réseau temporaire après le test. Aucune adresse de caméra réelle ne doit être
fournie.

Exemple depuis la racine du dépôt sous Linux, après avoir défini
`MEDIAMTX_BIN` comme chemin absolu d'un binaire Linux/amd64 MediaMTX 1.21.4 :

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

Le réseau est créé uniquement pour le test et supprimé à la sortie du sous-shell.
La commande ne doit pas être exécutée sur un réseau de production.

## Docker et diagnostic

Le [guide Portainer](../deployment/go-portainer.md) décrit la publication de
l'image et le rendu de la stack sans déploiement. Le workflow teste Go et HA,
puis publie l'image et l'artefact HA associé sur `main`. Le test
`test_portainer_config.py` compare les variables MediaMTX de la stack avec la
configuration de référence. Le fichier `.dockerignore` n'envoie que les
sources Go nécessaires au build.

Utiliser des données fictives, des ports libres et des journaux expurgés.
Ne pas modifier de services de production ni contacter de caméra sans
autorisation explicite. Les résultats hors ligne ne prouvent pas la stabilité
ni la compatibilité du matériel réel.
