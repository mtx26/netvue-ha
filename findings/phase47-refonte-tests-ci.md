# Phase 47 — Refonte des tests et de la CI

## Objectif
Suite de tests simple et fiable, simulateur Netvue commun, CI parallèle sans
test silencieusement ignoré, endurance séparée. Voir [testing.md](../docs/testing.md).

## Décisions
- Simulateur unique en Go (`internal/netvue/netvuetest`, CLI `tests/simulator/netvue-sim`) ; la caméra Python de `ha_go_e2e_synthetic.py` (~240 lignes) est supprimée.
- `NETVUE_REQUIRE_INTEGRATION=1` transforme un outil manquant en échec ; la CI supprime les longues listes de noms de tests grep-ées et rejette tout `SKIP`.
- Home Assistant : `pytest-homeassistant-custom-component` incompatible avec HA 2026.11.2 (dernière version : 2026.2.3, échec à l'import) ; un seul harnais maison `tests/ha/harness.py` (vrai `HomeAssistant`, aucun listener), lancé par pytest. Python ≥ 3.14.2 requis.
- Workflows : `ci.yml` (go, ha, build→integration, docker, publish sur `main` seulement) et `endurance.yml` ; `publish-go-image.yml` et `synthetic-endurance.yml` supprimés ; cibles fuzz découvertes par `scripts/ci/fuzz.sh`.

## Modifications
- Go : fuzzers ajoutés (audio/ADTS, talkback, SDP WebRTC, AAC→ADTS), tests de concurrence (Open talkback, fin de session, 6 caméras supervisées, `Close` du manager), cas de supervision.
- Reprise des tests existants : cas nommés dans les parseurs/audio, assertions plus explicites dans l'authentification, vérification des erreurs de fixtures et écritures dans session/talkback, setup RTSP partagé, assertions complètes sur SPS/PPS. Les tests de découverte résiliente et de média déjà structurés ont été conservés.
- Un test de supervision multi-caméra avait une échéance trop courte sous charge ; son délai d'échec est maintenant borné à 5 s. Le test de cadence talkback compare à la durée AAC du fixture avec une tolérance explicite, plutôt qu'à un seuil fixe trop strict.
- Après le premier run GitHub, le scénario MediaMTX/E2E a été corrigé : le lecteur FFmpeg de résolution était lancé pendant l'intervalle de retrait/républication de la route (404). Le test attend maintenant le décodage de la bonne résolution/couleur, avec plusieurs essais bornés et 5 s par décodage.
- HA : 27 tests sur vrai HA (flows, entités, rechargement, suppression, WebRTC, two_way_audio) ; 9 tests à base de `SimpleNamespace` supprimés (doublons).
- E2E : `tests/e2e` (lecture MediaMTX, multi-caméra avec panne isolée, WebRTC→AAC→caméra).

## Tests exécutés (Windows, local)
- `go build`, `go vet` : OK. `go test -race -count=1 ./...` avec FFmpeg 9.0.2 et MediaMTX 1.21.4 réels : tous OK (~47 s). Sans ces outils : OK, tests dépendants ignorés.
- Suivi qualité : `go build ./...`, `go vet ./...`, `go test ./...` et `go test -race ./...` : OK ; deux tests sensibles au temps répétés 10 fois : OK.
- HA : 75/75 (Python 3.14.10, venv hors dépôt), ~40 s.
- `actionlint` sur les deux workflows : OK.
- Mutations (défaut introduit → échec constaté → restauré) : authentification sans résultat, en-tête ADTS, `cancel()` du manager, erreur fatale de supervision (non détectée au départ → test ajouté), absence de reconnexion, AAC talkback tronqué, RTSP indisponible, déchargement sans suppression de session, micro toujours autorisé.

## Limites
- Workflows jamais exécutés sur GitHub : temps de CI et téléchargements non mesurés ; premier run à confirmer.
- `ha_go_e2e_synthetic.py` (Docker) non exécuté avec le nouveau simulateur ; absence de Docker local.
- Mutation de suppression du pacing talkback non détectée lors du premier essai ; l'assertion de cadence a depuis été rendue plus robuste, mais la mutation n'a pas été rejouée. L'isolation multi-caméra n'a pas été mutée.
- Un échec `-race` unique et non reproduit a été signalé pendant les essais précédents ; la suite complète sous race passe dans le suivi, sans validation prolongée.
- `tests/e2e` exige le port 8554 libre (codé en dur dans le manager).
- Pas de `diagnostics.py` HA : diagnostics testés via capteurs seulement.
