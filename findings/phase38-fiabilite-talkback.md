# Phase 38 — Fiabilité, sécurité et talkback

## Objectif et décisions

Fiabiliser la publication A/V et l'isolation des essais, puis déterminer si
le talk peut être implémenté sans inventer de protocole. Les modifications
et configurations préexistantes ont été conservées ; aucune caméra n'a été
contactée pendant les vérifications hors ligne.

## Audio et vidéo

- L'horloge audio s'appuie sur le dernier timestamp vidéo et le temps vidéo
  cumulé plutôt que sur un écart signé depuis le début de publication, qui
  devenait incorrect après environ 24,9 jours. Les tests couvrent notamment
  cette durée, le bouclage des horloges et l'isolation de deux publishers.
- Les trames audio successives ne se chevauchent plus si une trame AAC
  contient plusieurs unités ou si une trame PCM est fragmentée. Les tests
  couvrent aussi les fragments et les écarts de l'horloge source.
- Une trame audio trop grande est désormais classée comme erreur audio, ce qui
  permet le repli vidéo existant plutôt que l'arrêt fatal de la session ;
  un test de réception LIVE vérifie effectivement la poursuite de la vidéo.
- Le changement de paramètres H.264 entraîne toujours une nouvelle publication
  RTSP : une interruption du lecteur reste possible. L'origine des avertissements
  DTS et de la fin de flux observés en phase 37 n'est pas établie. Un essai
  FFmpeg synthétique montre toutefois que des PTS de 0 et 10 ms deviennent
  deux DTS égaux avec une base de sortie de 1/10 s, mais restent distincts
  avec une base de 1/90000 s. Ce mécanisme possible ne prouve pas la cause
  de l'avertissement réel.

## Sécurité réseau

La configuration MediaMTX du dépôt désactivait déjà les protocoles non utilisés
et limitait RTSP/TCP à loopback ; l'exposition constatée en phase 37 provenait
d'une configuration temporaire par défaut. Des tests vérifient les réglages
et les écouteurs réels, tant pour le fichier de référence que pour les
variables de la stack. La CI exige leur exécution dans un conteneur sans réseau.
La stack de production n'a pas été modifiée ni redéployée.

## Talkback et Home Assistant

L'analyse statique de l'APK 5.27.0 confirme pour le LAN classique la commande
de direction `Q=5`, `V=1`, `P=[3]` et l'enveloppe type 201 avec en-tête média
`0x20`, PTS 32 bits little-endian et charge AAC. Le désassemblage ARM64 de
`libmobileffmpeg.so` montre que `nvAACEncInit` (`0x18a4b0`) sélectionne
`libfdk_aac` et le profil AAC-LC ; le wrapper FDK (`0x4cdeb0`) choisit ADTS
par défaut sous certaines conditions. Les octets de l'`AVPacket` sont transmis
tels quels au Java (`0x18a184–0x18a1f4`). ADTS reste attendu statiquement,
non confirmé en exécution, et le remplissage ainsi que l'acceptation par cette
Vigil restent inconnus. CameraII utilise un cadrage différent.
Le détail vérifié figure dans [la spécification du protocole](../docs/netvue-protocol.md).

Home Assistant 2026.11.2 expose une capacité `two_way_audio`, mais le frontend
nécessite un transport WebRTC natif ou fourni par un provider ; HLS ne
transmet pas le microphone. La caméra actuelle fournit uniquement une URL
RTSP de lecture et aucun chemin de retour audio. Il serait trompeur d'annoncer
cette capacité ou d'envoyer un codec supposé : l'émission et son interface HA
restent donc non implémentées.
[Documentation HA](https://contributors.home-assistant.io/core/entities/camera/#two-way-audio)

## Tests et limites

- Sur Windows, `go test ./...`, `go test -race ./...`, `go vet ./...` et
  `go build ./...` réussissent avec MediaMTX, FFmpeg et FFprobe configurés.
- Les trois tests MediaMTX de publication et d'isolation multi-caméra passent
  dans l'image HA 2026.11.2, avec réseau désactivé. FFprobe y confirme les
  pistes H.264 et AAC pendant le flux ; FFmpeg décode les deux. Un serveur
  RTSP local vérifie séparément les écarts PTS audio et vidéo reçus.
- Sur Debian, les quatre tests Portainer passent, dont les deux variantes
  d'écouteurs MediaMTX réels ; les tests Home Assistant donnent 41 réussites
  et un test d'écouteurs ignoré dans cette commande mais réussi séparément.
  `docker stack config` valide la stack sans déploiement.
- L'E2E HA 2026.11.2 → découverte Go → caméra synthétique → MediaMTX →
  image HA réussit dans un réseau Docker interne dédié ; il vérifie aussi le
  diagnostic d'échec, le redémarrage explicite et le déchargement. Un message
  `Stream ended` apparaît à la fermeture. Les ressources dédiées ont été
  supprimées après l'essai.

L'utilisateur a autorisé deux sessions successives au maximum, de 45 secondes
chacune, sans émission de son ni modification de la caméra ou de la production,
uniquement si aucune session n'est déjà active. Le contrôle passif après cette
autorisation montre encore une connexion établie vers la caméra : aucun essai
n'a donc été lancé et aucune session existante n'a été arrêtée. À la prochaine
fenêtre libre, vérifier à nouveau l'absence de session, publier sur une instance
MediaMTX isolée et lancer FFprobe et FFmpeg pendant le LIVE. Les essais de
talk nécessitent une autorisation séparée. Le décalage perceptuel A/V et les
changements de résolution réels restent aussi à valider sur l'appareil.
