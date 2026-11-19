# Tests

Aucun compte Netvue ni caméra réelle n'est nécessaire : tout repose sur le
simulateur synthétique ci-dessous, des ports éphémères et des réseaux isolés.

| Catégorie | Emplacement | Outils requis |
|---|---|---|
| Go (unitaires, concurrence, fuzzing) | `internal/`, `cmd/` | Go 1.26+ |
| Home Assistant | `tests/ha/`, `tests/test_home_assistant_*.py` | Python ≥ 3.14.2 |
| Bout en bout | `tests/e2e/`, `internal/netvue/*_integration_test.go`, `tests/ha_go_e2e_synthetic.py` | FFmpeg, MediaMTX (+ Docker pour HA↔Go) |
| Endurance et performances | `internal/manager/endurance_test.go`, workflow `endurance.yml` | Go |

## Tests Go

```sh
go test ./...
go test -race ./...
go vet ./...
```

Les tests qui exigent FFmpeg ou MediaMTX sont ignorés (`SKIP`) s'ils sont
absents. Variables : `NETVUE_TEST_FFMPEG`, `NETVUE_TEST_FFPROBE`,
`NETVUE_TEST_MEDIAMTX`. Avec `NETVUE_REQUIRE_INTEGRATION=1`, un outil manquant
devient un échec : c'est le mode de la CI.

Fuzzing natif (toutes les cibles `Fuzz*`, durée par cible) :

```sh
bash scripts/ci/fuzz.sh 10s
go test ./internal/netvue -run='^$' -fuzz='^FuzzParseDatagram$' -fuzztime=30s
```

## Tests Home Assistant

```sh
python -m pip install -r tests/requirements-homeassistant.txt   # Python ≥ 3.14.2
python -m pytest tests/ha tests/test_home_assistant_integration.py tests/test_home_assistant_webrtc.py -q
```

`tests/ha/harness.py` démarre un vrai `HomeAssistant` par test (dossier
temporaire, aucun listener, aucun accès à une HA de production) avec un faux
gestionnaire HTTP local. C'est l'unique système de fixtures.
`pytest-homeassistant-custom-component` n'est pas utilisé : sa dernière version
exige `homeassistant==2026.2.3` et échoue à l'import avec 2026.11.2. À
réévaluer à chaque montée de version HA.

Couverture : config flow manuelle, découverte LAN, validation, options,
entités, image caméra, disponibilité, panne du gestionnaire, rechargement,
suppression, WebRTC et `two_way_audio`, diagnostics (capteurs).

## Simulateur Netvue

Package Go [`internal/netvue/netvuetest`](../internal/netvue/netvuetest) :
découverte UDP, authentification par clé, TCP, sélection LIVE, vidéo H.264,
audio AAC, talkback, paquets incorrects, coupures (`Drop`), changements de
résolution, plusieurs instances indépendantes. Fixtures déterministes. Dans un
test Go, l'importer directement ; hors Go (HA E2E), utiliser le binaire :

```sh
go build -o netvue-sim ./tests/simulator/netvue-sim
```

## Bout en bout

```sh
export NETVUE_REQUIRE_INTEGRATION=1 NETVUE_TEST_FFMPEG=$(which ffmpeg) NETVUE_TEST_MEDIAMTX=/chemin/mediamtx
go test -race -count=1 ./tests/e2e/ ./internal/netvue/
```

`tests/e2e` vérifie bridge → MediaMTX → lecteur (vidéo et audio décodables,
synchronisation, reconnexion, changement de résolution, nettoyage), l'isolation
d'une panne entre deux caméras et le chemin WebRTC → Go → AAC → caméra
(contenu et cadence). Le test de résolution réessaie le décodage avec des
délais bornés pendant que MediaMTX remplace la publication RTSP. Il exige le
port local 8554 libre.

Le scénario HA → Go → caméras simulées → MediaMTX → HA
(`tests/ha_go_e2e_synthetic.py`) s'exécute dans des conteneurs sur un réseau
Docker `--internal` ; la séquence de commandes de référence est le job
`integration` de [ci.yml](../.github/workflows/ci.yml).

## Endurance

Workflow [endurance.yml](../.github/workflows/endurance.yml) (nuit ou
`workflow_dispatch`, jamais sur PR) : `go test -race -count=N`, fuzzing long
(`fuzztime`) et tests d'endurance multi-caméra (churn, panne isolée,
goroutines, arrêt propre). En local :

```sh
go test -race -run Endurance -count=10 ./internal/manager
```

## Interpréter les erreurs

- `SKIP` en local : outil manquant, relancer avec les variables ci-dessus.
- `SKIP` ou échec « required » en CI : un outil d'intégration a disparu, le test
  n'a pas réellement tourné.
- Échec `-race` : course de données ; le rapport donne les deux piles.
- Un fuzz en échec écrit son entrée dans `testdata/fuzz/…` : la relancer avec
  `go test`, corriger, conserver le fichier comme test de régression.
- Tests E2E : la sortie du simulateur et du bridge est incluse dans le log.

## Ce que vérifie la CI

[ci.yml](../.github/workflows/ci.yml), jobs parallèles : `go` (test, race, vet,
build, fuzz 2 s par cible), `ha` (ruff + pytest), `build` + `integration`
(binaires compilés une fois ; MediaMTX/FFmpeg, WebRTC/audio, E2E ; aucun `SKIP`
toléré), `docker` (image construite, démarrée, `/api/v1/status`, rendu de la
stack Portainer sans déploiement). `publish` n'a lieu que sur `main` après
succès de tous les jobs ; jamais sur PR, aucun déploiement automatique.
