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
  -B -m unittest discover -s tests -p test_home_assistant_integration.py -v
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

## Docker et diagnostic

Le [guide Portainer](../deployment/go-portainer.md) décrit la construction de
l'image depuis la racine et le rendu de la stack sans déploiement. Le fichier
`.dockerignore` n'envoie que les sources Go nécessaires au build.

Utiliser des données fictives, des ports libres et des journaux expurgés.
Ne pas modifier de services de production ni contacter de caméra sans
autorisation explicite. Les résultats hors ligne ne prouvent pas la stabilité
ni la compatibilité du matériel réel.
