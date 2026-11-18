# Phase 42 — Talkback et Home Assistant

## Objectif et état final

Évaluer et finaliser le talkback Netvue depuis Home Assistant. La phase ne
permet pas encore de parler depuis HA : l’envoi Go existant reste expérimental
et la restitution par le haut-parleur n’est pas prouvée.

## Décisions et modifications

- Le protocole Android observé sélectionne l’audio et demande le sens talk.
  Les trames microphone contiennent AAC avec horodatage, mais le format
  réellement envoyé par l’application (ADTS ou LATM) reste inconnu.
- Le code Go existant envoie un fichier AAC-LC/ADTS dans une session A/V
  distincte et bornée. Il ne reçoit pas de microphone en direct sur la session
  supervisée.
- Home Assistant n’expose son contrôle microphone que pour une caméra avec
  WebRTC et une entrée audio bidirectionnelle réellement prise en charge.
  L’intégration utilise RTSP/Stream et le pont n’a pas de chemin WebRTC audio
  entrant. `two_way_audio` reste donc désactivé ; l’activer seul serait une
  fausse annonce.
- La caméra HA documente et teste cette capacité désactivée, y compris si un
  fournisseur WebRTC indépendant est présent.

## Tests et preuves

- Go : `go test ./...`, `go test -race ./...`, `go vet ./...` et `go build
  ./...` réussissent. Les tests talkback synthétiques couvrent l’émission,
  l’annulation et la coexistence avec la réception A/V.
- Les observations antérieures ne prouvent pas la restitution : sept trames
  ADTS ont été émises sans refus `A=5` observé pendant 3,53 s ; une écoute
  antérieure n’a pas entendu la tonalité. L’acceptation et le son audible
  restent à vérifier.
- HA : la suite Python n’a pas pu être exécutée. L’interpréteur local est
  Python 3.13.1, inférieur au minimum 3.13.2 de Home Assistant 2026.11.2 ;
  Docker n’est pas disponible pour utiliser l’image HA documentée.
- Les tests MediaMTX/FFmpeg et le scénario CI complet n’ont pas été exécutés
  localement. Les tests Go seuls ne prouvent pas la compatibilité audio/vidéo
  indépendante ni sa synchronisation.

## Validation réelle et limites restantes

- L’état Debian a été vérifié avant l’essai : aucun service ni conteneur Netvue
  n’était actif. MediaMTX 1.21.4 a été lancé temporairement sur
  `127.0.0.1:8554`, puis arrêté ; aucun service de production n’a été modifié.
- Après autorisation, une seule session Go de 12 s maximum a ciblé
  `192.168.129.2`. Elle a envoyé une tonalité AAC-LC/ADTS synthétique 440 Hz,
  mono 8 kHz, décodée sur 1,92 s avec un pic mesuré à −32,43 dBFS. Go a
  journalisé l’envoi complet et terminé avec le code 0.
- Ce résultat confirme le chemin d’émission TCP de l’implémentation Go, pas
  la restitution audible. Aucun lecteur FFmpeg/FFprobe n’était disponible ;
  la continuité A/V et la synchronisation via MediaMTX n’ont donc pas été
  décodées indépendamment. L’utilisateur indique ne pas avoir constaté l’essai ;
  la restitution acoustique reste donc indéterminée. Aucune seconde tentative
  ne sera faite sans nouvelle autorisation.

Le résultat principal reste à développer : négociation WebRTC microphone,
acheminement audio vers la session Go et conversion du codec WebRTC vers le
format accepté par la caméra, avec tests synthétiques et validation acoustique
autorisée. Aucun déploiement ni changement de caméra n’a été effectué.
