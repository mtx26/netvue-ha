# Phase 43 — Microphone Home Assistant

## Décisions et modifications

- Le provider WebRTC HA reste opt-in. `two_way_audio` reste désactivé tant
  qu'une restitution réelle n'est pas confirmée ; le contrôle microphone n'est
  donc pas affiché dans HA.
- Le pont reçoit l'Opus WebRTC, le décode en PCM puis l'encode en AAC-LC/ADTS
  sur la session Netvue supervisée. Le retour audio AAC caméra est converti en
  Opus. Le refus du talkback n'interrompt plus l'audio caméra ni la vidéo.
- Le délai sans RTP microphone est de 10 s afin de tolérer la suppression de
  silence. Le test vérifie une pause de 5 s, la fermeture après inactivité, la
  reprise du talkback et la continuité vidéo.
- Les tests FFmpeg ont révélé des timestamps Opus répétés en fin de flux ; ils
  sont maintenant relevés selon la durée des paquets, sans écraser les avances
  valides. Les fixtures AAC utilisent des trames ADTS valides.
- Les candidats ICE loopback sont permis uniquement quand l'adresse source
  configurée est elle-même loopback, pour les tests locaux isolés.

## Tests

- Go : `go test ./...`, `go test -race ./...`, `go vet ./...` et `go build ./...`
  réussis.
- Média synthétique Linux : tests Go avec FFmpeg 8.1.3, MediaMTX 1.21.4 et
  ffprobe réussis, notamment WebRTC→AAC, AAC→Opus, arrêt/reprise, continuité A/V,
  changement de résolution et isolation multi-caméra.
- Home Assistant 2026.11.2 : les 54 tests Python de `tests/` réussissent dans
  l'image officielle isolée (`--network none`), avec MediaMTX pour les tests de
  configuration/runtime.
- Rendu `docker stack config` réussi sans déploiement. Le workflow GitHub
  distant n'a pas été déclenché ; ses validations locales équivalentes ont été
  exécutées.
- Mesure synthétique ponctuelle : premier AAC micro en ~261 ms ; jusqu'à 3,55 %
  d'un cœur CPU et 37,43 MiB de mémoire dans un conteneur limité à 1 CPU/256 MiB.
  Ce n'est pas une projection multi-caméra.
- Les avertissements DTS historiques ne sont pas corrigés : les sources des
  phases 37–41 ne permettent pas d'en établir la cause. Les scénarios
  MediaMTX/FFmpeg synthétiques décodent A/V, sans prouver la monotonie DTS de
  chaque sortie caméra réelle.

## Validation réelle et limites

- Avant l'essai, `ssh home` fonctionnait, HA était actif, aucun processus ou
  socket Netvue serveur vers `192.168.129.2` n'était présent et la route source
  `192.168.129.159` était correcte. L'utilisateur a confirmé avant chaque
  essai l'absence d'une session concurrente.
- Deux sessions directes Go `talk`, chacune limitée à 10 s, ont utilisé une
  sinusoïde AAC-LC/ADTS mono 8 kHz, 700 Hz, 2 s. Le pic mesuré après décodage
  de la source était -23,90 dBFS (cible -24 dBFS). Les deux journaux indiquent
  « audio sending » puis « audio sent » sans erreur et les processus se sont
  terminés normalement.
- Lors du premier essai, FFmpeg a identifié H.264 et AAC mono 8 kHz sur RTSP,
  mais la capture audio a échoué avant lecture (`rw_timeout` non pris en charge).
  Après correction vers l'option RTSP `-timeout`, le second essai a capturé
  5 s de retour AAC décodé en PCM. Le flux annonçait H.264 Main 640×360 à 15 fps
  et AAC-LC mono 8 kHz.
- Analyse spectrale temporaire du retour : maximum Goertzel à 700 Hz
  -54,1 dBFS sur une fenêtre de 100 ms à 2,8 s ; les autres fenêtres sont
  intermittentes et bien plus faibles. L'alignement temporel n'est pas assez
  précis pour attribuer ce pic au signal envoyé. Ce n'est pas une preuve de
  restitution acoustique ni d'un son audible au haut-parleur.
- MediaMTX 1.21.4 était temporaire, limité à `127.0.0.1:8554`, puis arrêté ;
  les fichiers audio temporaires ont été supprimés. HA et les services de
  production n'ont pas été redémarrés ou modifiés.
- Cet essai utilisait le chemin direct Go `talk` avec AAC préencodé, pas le
  microphone du navigateur ni l'interface HA. `two_way_audio` reste désactivé
  et le bouton microphone masqué ; le trajet HA→WebRTC→caméra et la restitution
  réelle restent non validés.
