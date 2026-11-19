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
- Run GitHub `44510504917` : les tests Netvue passent, mais les trois E2E tentent de lancer `go build` dans le conteneur HA sans Go. Le harnais accepte maintenant `NETVUE_TEST_GO_BRIDGE`, fourni par la CI avec le binaire déjà construit ; la compilation locale reste disponible en l'absence de cette variable.
- Run GitHub `44512017267` : les tests Go d'intégration et E2E passent ; le scénario HA échoue sur le compteur d'authentifications après injection média. L'API du simulateur commun coupe la connexion pour tous les types de panne, contrairement à l'attente conservée pour l'ancien simulateur. Le scénario exige désormais exactement une nouvelle authentification pour chaque injection, média compris, sans autoriser de tentative supplémentaire après l'erreur fatale.

## Tests exécutés (Windows, local)
- `go build`, `go vet` : OK. `go test -race -count=1 ./...` avec FFmpeg 9.0.2 et MediaMTX 1.21.4 réels : tous OK (~47 s). Sans ces outils : OK, tests dépendants ignorés.
- Suivi qualité : `go build ./...`, `go vet ./...`, `go test ./...` et `go test -race ./...` : OK ; deux tests sensibles au temps répétés 10 fois : OK.
- Vérification du correctif après le run GitHub : tests MediaMTX audio/vidéo et simulateur/reconnexion/résolution passés ensemble (30 s) ; scénario simulateur seul passé aussi sous `-race` (28 s). `go vet ./...` et `go build ./...` passent.
- HA : 75/75 (Python 3.14.10, venv hors dépôt), ~40 s.
- `actionlint` sur les deux workflows : OK.
- Correctif du binaire E2E : test de régression avec `PATH` sans Go passé sous `-race`, `go vet ./tests/e2e` et `actionlint` sur les deux workflows passés. Désactivation temporaire du chemin précompilé : échec attendu « go executable file not found » ; comportement correct restauré et test relancé avec succès.
- Correctif du compteur HA : `TestFaultsDropAndVideoChange` passé sous `-race` ; syntaxe Python validée par Pylance. L'expression réelle du scénario a été évaluée avec les compteurs CI (22 → 23) pour les trois étapes ; une tentative supplémentaire (24) reste rejetée. Ce contrôle ciblé ne remplace pas l'exécution HA/Docker complète.
- Mutations (défaut introduit → échec constaté → restauré) : authentification sans résultat, en-tête ADTS, `cancel()` du manager, erreur fatale de supervision (non détectée au départ → test ajouté), absence de reconnexion, AAC talkback tronqué, RTSP indisponible, déchargement sans suppression de session, micro toujours autorisé.

## Limites
- Les trois premiers runs GitHub ont révélé successivement la transition RTSP, la compilation E2E dans un conteneur sans Go, puis une attente de compteur média héritée de l'ancien simulateur. Une CI complète verte après correction reste à confirmer ; les temps globaux et téléchargements ne sont pas encore mesurés.
- Une tentative récente de `go test ./...` n'est pas verte : la variable locale `NETVUE_TEST_FFPROBE` pointait par erreur vers FFmpeg, et les tests `tests/e2e` ont échoué sur l'indisponibilité de l'encodeur audio. La suite complète reste à revalider dans l'environnement Linux de CI ; les tests RTSP ciblés avec la configuration corrigée passent.
- `ha_go_e2e_synthetic.py` (Docker) non exécuté avec le nouveau simulateur ; absence de Docker local.
- Mutation de suppression du pacing talkback non détectée lors du premier essai ; l'assertion de cadence a depuis été rendue plus robuste, mais la mutation n'a pas été rejouée. L'isolation multi-caméra n'a pas été mutée.
- Un échec `-race` unique et non reproduit a été signalé pendant les essais précédents ; la suite complète sous race passe dans le suivi, sans validation prolongée.
- `tests/e2e` exige le port 8554 libre (codé en dur dans le manager).
- Pas de `diagnostics.py` HA : diagnostics testés via capteurs seulement.
