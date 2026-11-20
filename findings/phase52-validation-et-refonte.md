# Phase 52 — Validation synthétique et refonte progressive

## Objectif et décisions

Poursuivre la phase 51 sans changer le protocole ni utiliser de caméra réelle
ou de service de production. Les tests utilisent des caméras simulées, des
processus isolés et, pour Home Assistant, un réseau Docker interne temporaire.
La refonte globale reste incomplète.

## Modifications

- `tests/e2e/stack_test.go` : compilation du bridge avec `-race` lorsque le
  test est instrumenté, contrôle des binaires précompilés, capture des
  rapports de courses et échec en cas de sortie prématurée du processus.
  Les régressions couvrent un enfant normal, un rapport et un crash.
- `tests/ha_go_e2e_synthetic.py` : stderr des processus capturé dans des
  fichiers temporaires, rapports race vérifiés et arrêts contrôlés.
  `tests/test_ha_go_e2e_synthetic.py` couvre rapport, arrêt et sortie
  prématurée ; ces tests sont ajoutés à `.github/workflows/ci.yml`.
- `internal/manager/webrtc.go` et `webrtc_rtsp.go` : ouverture RTSP et
  transfert de propriété isolés dans `openRTSP`. Un test dans
  `webrtc_test.go` vérifie l'échec d'une source indisponible puis une nouvelle
  tentative. Les délais et l'ordre des opérations ne changent pas volontairement.
- `internal/manager/manager.go` : la création réutilise la même normalisation
  d'adresse que les opérations de session ; l'invariant de revérification après
  attente du verrou est documenté. `manager_test.go` couvre la création,
  l'idempotence, la recherche et l'arrêt avec une adresse IPv4 mappée.
  Le tri du snapshot des caméras est effectué après libération du verrou ;
  les données copiées restent protégées pendant leur lecture.

L'API HTTP du gestionnaire et les modules HA `__init__.py`, `bridge_api.py`,
`config_flow.py`, `coordinator.py` et `rtsp.py` ont été réexaminés sans
modification de production. Les protections contre les résultats de création
ou suppression incertains et le rollback d'un déchargement annulé sont
couvertes par les tests existants ; les simplifier sans préserver ces
garanties changerait le cycle de vie. Les autres interactions restent à revoir.

## Validations

- Docker Desktop fonctionne. MediaMTX 1.21.4, FFmpeg et les images Go et Home
  Assistant locales ont été utilisés sans modifier les services existants.
  Le simulateur est compilé statiquement pour l'image HA Alpine ; le bridge
  Linux avec race utilise les bibliothèques glibc de l'image Go montées en
  lecture seule uniquement pour ce test.
- En conteneurs isolés, suites manager, Netvue et E2E média avec race : 57,
  116 et 6 tests réussis, sans skips. Le parcours HA → Go → deux caméras
  simulées → MediaMTX → image HA a réussi, y compris diagnostic, redémarrage,
  déchargement, isolation et reconnexion. Les erreurs de protocole synthétiques
  testées provoquent l'échec contrôlé puis la reprise.
- `go test ./... -count=1` réussit avec MediaMTX, FFmpeg et bridge Windows
  non instrumenté. Une exécution complète de `go test -race ./... -count=1`
  a également réussi avec bridge instrumenté et outils média. `go vet ./...`
  et `go build ./...` réussissent. Les tests WebRTC, H.264 et conversions
  audio ciblés réussissent avec race.
- Tests Python HA : 92 tests et 61 sous-tests réussis en conteneur HA sans
  réseau, 33 avertissements ; pytest 9.2.0 installé temporairement depuis des
  wheels locaux. L'interpréteur Windows ne possède pas Home Assistant et sa
  tentative de collecte a échoué. Ruff réussit selon la sélection CI. Actionlint
  réussit sur les deux workflows ; ShellCheck n'était pas disponible pour cette
  exécution.
- Le lot gestionnaire a passé avant et après modification les tests ciblés
  d'adresses, de cycle de vie et de concurrence avec race ; `go vet` et
  `gofmt -d` ciblés réussissent. Les suites Go complètes avec et sans race ont
  été rejouées après la normalisation et le déplacement du tri ; les tests de
  snapshot, API et endurance ciblés réussissent aussi avec race.
- Les conteneurs et le réseau propres à l'E2E HA ont été supprimés. Aucune
  amélioration de performance n'est revendiquée sans mesure.

## Limites et prochaine étape

Une exécution Windows avec race a subi une panique dans `bufio.Reader.ReadSlice` ;
une relance complète a réussi. Une autre exécution avec bridge enfant Windows
instrumenté a échoué avec `fatal error: found pointer to free object` ; le
harnais a signalé sa sortie et conservé ses logs. Cinq répétitions ciblées du
talkback avec ce même bridge ont réussi. L'origine de ces anomalies n'est pas
établie et ces succès ne les invalident pas.

Reprendre le cycle de vie des sessions et du gestionnaire, puis les frontières
média et HA, avec des tests préalables ; poursuivre l'investigation Windows et
le suivi fichier par fichier. La conversion AAC → Opus n'utilise que le premier
timestamp source ; les tests actuels couvrent des timestamps continus, pas
une discontinuité. Mesurer ce cas synthétique avant de conclure à une dérive ou
de modifier la synchronisation. Les tests synthétiques ne démontrent pas le
comportement sur caméra réelle ; le talkback reste expérimental et désactivé
par défaut.
