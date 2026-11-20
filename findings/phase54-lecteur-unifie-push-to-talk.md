# Phase 54 — Lecteur WebRTC unifié et push-to-talk

## Objectif
Un seul lecteur interactif (vidéo + audio caméra + push-to-talk momentané) sans
redémarrage vidéo ni renégociation, modèle audio half-duplex.

## Causes constatées
- La carte WebRTC native de HA possède le `RTCPeerConnection` : son bouton micro le reconstruit.
- Le micro était détecté via `sendrecv` dans l'offre et le talkback ouvert/fermé selon la présence de RTP (idle 10 s).
- Toute erreur audio appelait `peer.fail` et tuait la vidéo ; un seul pair par caméra ; durée de vie 10 min.

## Décisions
- Carte `netvue-camera-card` unique, passant par la signalisation HA. Options `webrtc_enabled` et `experimental_talkback` supprimées ; `two_way_audio` natif retiré.
- Contrôle explicite par canal de données `netvue-ptt` ; `replaceTrack` au lieu de renégocier.
- `talkController` (acteur par pair), `audioGate` (coupure de l'audio du locuteur, séquences contiguës), jusqu'à 4 pairs par caméra, audio non fatal avec redémarrages bornés, durée de vie 12 h (garde-fou).
- Seuil de validité des identifiants TURN découplé de la durée de vie (`turnMinRemaining`).

## Tests
- Go : `go vet ./...`, `go test -race ./internal/manager ./cmd/...` OK (E2E synthétique Pion : 3 appuis, zéro renégociation, séquences audio sans trou, ouvertures/fermetures du talkback comptées ; tests unitaires du contrôleur et du gate).
- Python 3.14 + HA 2026.11.2 : 91 tests ; Node : 40 tests ; ruff OK.
- `internal/netvue` et `netvuetest` : échecs d'environnement (alias loopback 127.201.0.x refusés), paquets non modifiés.

## Limites
Non validés en réel : sortie haut-parleur caméra, comportement de `P=3` en half-duplex, push-to-talk dans un vrai navigateur/mobile. Un trou audio côté caméra reste compressé par FFmpeg. Un FFmpeg bloqué sans sortie n'est pas détecté.
