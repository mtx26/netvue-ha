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
Home Assistant 2026.11.2 nécessite Python 3.14.2 ou ultérieur.

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
hors nettoyage. La caméra synthétique doit tourner dans un second conteneur,
avec une IPv4 privée distincte dans le même sous-réseau que le conteneur HA/Go ;
le réseau Docker temporaire doit être `--internal`, sans ports publiés. Le
script `--camera` répond à la découverte broadcast et fournit le flux TCP
synthétique. Cette séparation est nécessaire car Go filtre les réponses de
découverte provenant de sa propre adresse source. La CI exige également le
marqueur final émis après les assertions de diagnostic, de redémarrage, d'image
JPEG et de déchargement.

Requiert FFmpeg dans l'image HA, MediaMTX Linux et le binaire Go Linux compilé
depuis le code testé. Monter sources et binaires en lecture seule, configurer
les variables suivantes avec chemins **dans le conteneur**, puis supprimer le
réseau et le conteneur caméra :
`NETVUE_TEST_COMPONENT`, `NETVUE_TEST_MEDIAMTX`,
`NETVUE_TEST_MEDIAMTX_CONFIG`, `NETVUE_TEST_GO_BRIDGE`,
`NETVUE_TEST_CAMERA_HOST`.

Le workflow [`.github/workflows/publish-go-image.yml`](../.github/workflows/publish-go-image.yml)
contient la commande Linux reproductible : il épingle Go, HA et MediaMTX,
exécute les tests de publication/décodage H.264 et d'isolation multi-caméra
sans réseau, puis le scénario complet sur un réseau interne avec les deux
conteneurs synthétiques. Il exige explicitement que les tests vidéo soient
`PASS` et échoue s'ils sont ignorés. Les tests Go, race detector, vet et les
tests HA unitaires restent des étapes séparées. La stack Swarm est aussi
rendue avec des valeurs factices via `docker stack config` ; aucune stack
n'est déployée.

La CI démarre également MediaMTX dans un conteneur sans réseau et vérifie ses
écouteurs effectifs pour la configuration de référence et les variables de
la stack : seul RTSP/TCP sur loopback est permis. Pour un essai manuel, utiliser
`deployment/mediamtx.yml` plutôt que les paramètres par défaut de MediaMTX.

Ces tests utilisent un réseau isolé, des données fictives et des ports libres.
Les résultats ne prouvent ni la compatibilité matérielle ni la stabilité
prolongée. Expurger les journaux avant partage.

## Docker

Le [guide Portainer](../deployment/go-portainer.md) explique l'image et le
rendu local de stack, sans déploiement. Le workflow valide les pull requests
et publie uniquement depuis `main`. `test_portainer_config.py` compare les
variables MediaMTX à la référence et protège les principaux réglages de
sécurité de la stack. `.dockerignore` limite le contexte aux sources Go
requises.
