# Phase 53 — Timestamps audio et refonte

## Objectif et état

Poursuivre la refonte à partir de la phase 52, sans caméra réelle ni production.
La refonte globale reste incomplète.

## Timestamps AAC → Opus

`setupRTSPAudio` transmet une valeur à 48 kHz par unité AAC. Le convertisseur
ancre la première sortie Opus sur la première entrée ; FFmpeg reçoit les unités
ADTS par un flux stdin qui ne transporte pas leurs timestamps. Les timestamps
ultérieurs proviennent de l'encodeur, avec correction des retours en arrière.

Le test synthétique `TestAACToOpusSourceTimestampDiscontinuities` encode les
mêmes unités AAC à 8 kHz quatre fois avec FFmpeg : référence continue, saut
avant de 48 000 ticks (1 s), saut arrière de 10 000 ticks et wrap uint32.
Résultat : 54 720 ticks entre le premier et le dernier paquet Opus dans chaque
cas ; les séquences de décalages de sortie sont identiques. Le wrap est
correctement représenté modulo 2³². Test réussi avec `-race`.

Cela démontre que ces sauts de timestamps source ne sont pas propagés à la
sortie Opus ; cela ne démontre ni dérive audible ni défaut perceptible en
conditions réelles. Aucun changement de synchronisation n'est effectué :
l'alignement des sorties sur les unités d'entrée doit tenir compte des
durées, du buffering et du délai de l'encodeur, et la politique à appliquer
aux retours ou aux trous n'est pas établie.

## Cycle de vie WebRTC

Chaque attente annulée de `waitPeerWorkers` conservait une goroutine en attente
du même `WaitGroup`. La fermeture du pair crée maintenant un signal unique de
fin des workers : le superviseur, l'arrêt de caméra et les requêtes DELETE
attendent ce signal sans créer une goroutine par attente. Le verrou interdit
l'ajout de workers après fermeture. Une requête DELETE annulée laisse le
superviseur retirer la réservation une fois les workers arrêtés.

Les nouveaux tests couvrent les annulations multiples et la libération différée
de la caméra. Dans un processus de test isolé, 64 attentes annulées ajoutaient
64 goroutines avec l'ancienne implémentation ; le test échoue alors comme prévu
et réussit avec le signal partagé. Les tests de cycle de vie ciblés passent
cinq fois avec race ; les tests WebRTC et audio ciblés passent aussi avec race.
Cette modification n'est pas présentée comme une correction du crash Windows.

## Diagnostic Windows

Go 1.26.1 Windows/amd64, GCC 15.2.0, bridge compilé avec `-race` :
avec `GOGC=20`, le processus de test lui-même a paniqué dans
`bufio.Reader.ReadSlice` au troisième essai talkback après deux succès
(`slice bounds out of range`, capacité 4096 et indice aberrant). Le parent ayant
paniqué, ses deux processus enfants ont été identifiés par PID et chemin puis
arrêtés. Dix essais supplémentaires avec le même bridge instrumenté et un
processus de test non instrumenté ont réussi sous `GOGC=20`.

Avec Go 1.26.0, les deux processus instrumentés et `GOGC=20`, huit essais
talkback ont produit six succès et deux sorties inattendues du bridge : une
erreur fatale `schedule: in cgo` et une exception `0xc0000005` pendant la
fermeture du mux ICE. Le harnais a détecté les deux sorties. Ces symptômes
n'établissent ni une origine commune aux échecs précédents ni une cause dans
le runtime, une dépendance ou le bridge.

Un cas réduit, sans processus bridge enfant ni conversion audio, reproduit
également une exception `0xc0000005` dans le processus de test Go 1.26.1 :
`TestWebRTCVideoOnlyDoesNotRequireEncoder`, répété avec `-race` et `GOGC=20`.
La pile corrompue mentionne l'attente de connexion WebRTC via TURN ; elle
ne permet pas d'identifier l'origine de la corruption. En isolant les
sous-tests, 20 essais directs instrumentés avec `GOGC=20` réussissent,
tandis que le sous-test TURN instrumenté plante avec `GOGC=20`, puis avec
le réglage GC par défaut. Vingt essais TURN sans race avec `GOGC=20`
réussissent. Ces essais ne démontrent ni que TURN est la cause, ni une
origine commune avec les autres crashes.

Le code Go actif du dépôt n'emploie pas directement `unsafe` ou CGO ; les
dépendances Pion RTCP et SRTP utilisent toutefois `unsafe`, et `-race` inclut
`runtime/cgo` et `runtime/race`. Une recherche publique a identifié
[golang/go#81238](https://github.com/golang/go/issues/81238), qui décrit une
corruption mémoire Windows avec un symptôme GC similaire sous Go 1.27 après
une exception mémoire. Aucun tel déclencheur n'a été démontré ici et ce
signalement ne prouve pas une cause commune.

## Validations finales de ce lot

- `go test ./... -count=1`, `go test -race ./... -count=1`, `go vet ./...`
  et `go build ./...` réussissent sous Windows, avec FFmpeg, MediaMTX et le
  bridge enfant actuel instrumenté. Les échecs intermittents ci-dessus ne
  sont pas invalidés par ces succès.
- `gofmt` sur les fichiers Go modifiés dans ce lot et Ruff selon la sélection
  CI réussissent. Les linters Go `goimports`, `staticcheck` et
  `golangci-lint` ne sont pas disponibles localement.
- L'E2E HA sur réseau Docker interne réussit avec le bridge Linux actuel
  compilé sans race : deux caméras simulées, MediaMTX, diagnostic, reprise,
  isolation, reconnexion et erreurs de protocole. Les conteneurs et le réseau
  propres à cet essai ont été supprimés.
- La collecte pytest du harnais HA sous Windows échoue : le module
  `homeassistant` manque dans cet interpréteur. La suite HA avait été validée
  en conteneur pendant la phase 52 ; elle n'a pas été rejouée ici.

## Suites

Réduire encore le scénario Windows ; rechercher l'origine du crash avant de
conclure. Poursuivre les autres modules et examiner la
réservation du pair lors d'un échec de démarrage concurrent à sa fermeture.
Les résultats synthétiques ne constituent pas une validation sur caméra réelle.
