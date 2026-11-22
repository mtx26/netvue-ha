# Phase 66 - Demarrage WebRTC et indisponibilite camera

## Objectif et preuves

Eviter les echecs precoces de WebRTC lors d'une republication RTSP, sans
masquer une camera hors ligne ni ajouter une boucle de reconnexion illimitee.

Le code imposait 5 s pour l'offre HA, contre 12 s de lecture RTSP. La
deadline ICE de 15 s commencait avant la preparation RTSP/SDP. Un DESCRIBE
404/503 echouait immediatement.

Diagnostic SSH `home` autorise le 22 novembre 2026 : la camera
192.168.129.100 est injoignable depuis Debian (trois pings sans reponse,
`Destination Host Unreachable`, voisin ARP `FAILED`). Les logs du pont
confirment les echecs discovery. L'etat `supervising` indique une supervision
active, pas une connexion camera reussie. L'alimentation, le repeteur et
l'adresse effective de la camera ne sont pas verifies.

## Decisions et modifications

- DESCRIBE 404/503 : nouveau lecteur toutes les 500 ms, limite par les 15 s
  de preparation ; autres erreurs inchangees.
- Chaque lecteur echoue est ferme avant une reprise. L'annulation HTTP,
  l'arret camera et la fermeture du pair interrompent la preparation.
- ICE dispose de ses propres 15 s apres preparation ; l'offre HA attend
  20 s et la carte attend la reponse SDP pendant 25 s.
- Les trois reprises existantes de la carte sont conservees (1/2/4 s).
- Journaux de preparation et d'echec : camera et cause technique, sans
  transmettre les details internes au navigateur.
- Integration 0.2.39 : URL frontend versionnee pour invalider le cache des
  imports du lecteur.

## Tests et etat final

- Tests WebRTC Go, puis verification avec `-race` : reprise 404 puis 503
  suivie d'une offre acceptee, annulation HTTP pendant l'attente, deadlines
  preparation/ICE distinctes, liberation du pair.
- Provider HA : offre lente non coupee par le timeout API courant,
  signalisation et fermetures existantes.
- Lecteur : reponse SDP tardive et budget ICE preserve ; politique de
  reprise bornee conservee.

Resultats : `go test -race ./internal/manager -count=1`, `go vet
./internal/manager`, compilation de la passerelle et Ruff reussis ; 15 tests
HA (plus trois sous-tests) et 22 tests du lecteur reussis.
`staticcheck` reste en echec sur des diagnostics preexistants ST1005 et
SA1019 ; aucun de ces diagnostics ne concerne les lignes ajoutees.
Le test media complet WebRTC/AAC est ignore : `NETVUE_TEST_FFMPEG` absent.

Fichiers principaux :
[preparation RTSP](../internal/manager/webrtc_rtsp.go),
[cycle du pair](../internal/manager/webrtc.go),
[API WebRTC](../internal/manager/webrtc_http.go),
[provider HA](../custom_components/netvue_local/webrtc.py),
[lecteur](../custom_components/netvue_local/frontend/connection.js) et
[version](../custom_components/netvue_local/manifest.json).
Tests Go/HA/lecteur et
[reference des delais](../docs/netvue-connections-and-reconnection.md)
mis a jour, avec la [roadmap](../docs/roadmap.md).

Correction locale uniquement. Aucun deploiement, redemarrage de service ni
session video reelle ouvert. La disparition de l'erreur en production reste
a verifier apres mise a jour et retour de la camera sur le reseau. Le lien
entre le repeteur et la panne n'est pas demontre.
