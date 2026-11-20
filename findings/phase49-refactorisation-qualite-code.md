# Phase 49 — Refactorisation ciblée de la qualité du code

## Objectif

Poursuivre l’audit de maintenabilité Go et Home Assistant, puis simplifier un
parcours média sans modifier le protocole, les flux ou le contrat HA.

## Analyse et décisions

- Les tests H.264 existants couvrent les paramètres contradictoires, les
  changements de SPS/PPS, les unités Annex B invalides et les partitions.
- `videoPublisher.Push` mêlait l’analyse et la validation des NAL à la mise à
  jour d’état et à la publication. Son analyse a été extraite dans une
  fonction sans effet de bord ; les mutations de l’éditeur restent après la
  validation complète et le rejet des partitions incomplètes.
- `webRTCPeer.start` reste inchangée : son découpage croiserait le cycle de vie
  RTSP/ICE/SDP et les workers média, sans preuve d’intégration locale suffisante
  pour justifier le risque.
- La roadmap n’est pas modifiée : aucune fonctionnalité ni validation réelle
  n’a changé d’état.

## Modifications

- `internal/netvue/video.go` : extraction de l’inspection H.264 dans
  `inspectH264AccessUnit`; ajout de commentaires sur les partitions incomplètes
  et l’attente d’un IDR après changement de paramètres.
- `internal/netvue/protocol.go` : documentation GoDoc des erreurs, du paquet et
  des fonctions de lecture, d’encodage et d’analyse.
- Aucun changement fonctionnel intentionnel ; aucun protocole, délai,
  mécanisme d’annulation/reconnexion ou format média modifié.

## Vérifications

- Tests H.264 ciblés avant puis après refactorisation : réussis.
- `go test ./internal/netvue -count=1` : réussi.
- `go test ./... -count=1` : réussi.
- `go test -race ./... -count=1` : réussi.
- `go vet ./...` : réussi.
- `go build ./...` : réussi.
- `git diff --check` : réussi.
- Ruff avait réussi lors de l’audit précédent ; aucun fichier Python n’a été
  modifié dans cette phase.

## Performances et limites

- Aucun benchmark pertinent du parcours H.264 n’a été exécuté ; aucun gain CPU,
  mémoire ou latence n’est revendiqué.
- Les tests Python Home Assistant n’ont pas pu être collectés localement :
  Home Assistant est absent et l’interpréteur disponible est inférieur à la
  version minimale requise par le projet.
- Les tests média nécessitant MediaMTX/FFmpeg ne sont pas validés localement.
- Aucune caméra réelle ni installation de production n’a été contactée.
- Les autres composants ont été examinés ; les chemins WebRTC/HA fortement
  couplés restent inchangés faute d’un bénéfice de refactorisation suffisamment
  démontré par des validations exécutables ici.
