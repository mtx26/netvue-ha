# Phase 40 — Coupures simultanées et transcodage WebRTC partagé

## Objectif

Analyser les journaux de production du 20/11/2026 (21:58–22:51 UTC) : coupures
`no video received before timeout` sur trois caméras, puis échecs répétés de
`Connection error [discovery]` sur `192.168.129.0`.

## Constats vérifiés (diagnostic passif SSH, sans contact caméra)

- Le conteneur `netvue-bridge` est limité à `cpu.max = 50000 100000` (0,5 CPU).
  Après 54 min : 234 périodes bridées, `throttled_usec` ≈ 47 s,
  `cpu.pressure full total` ≈ 8,9 s. Mémoire saine : pic 198 Mo / 512 Mo,
  aucun OOM.
- Chaque pair WebRTC adaptant le H.264 Main 4.1 (`4d0029`) lançait son propre
  FFmpeg libx264 : ≈ 8–10 % CPU chacun en régime établi (`docker top`), plus au
  démarrage. Jusqu'à 4 pairs × 3 caméras = 12 transcodeurs possibles.
- 22:48:11–22:48:21 : 10 adaptations démarrées en 10 s, puis 22:48:23–26 :
  perte vidéo sur les trois caméras et fin des sessions WebRTC.
- Le sous-réseau est `192.168.128.0/23` : `192.168.129.0` est une adresse
  d'hôte valide. Son entrée ARP est `FAILED` (toujours à 23:01 UTC) : aucun
  équipement ne répond pour cette adresse. `.1` et `.2` sont `REACHABLE`.
- La caméra avant est derrière un répéteur Wi-Fi. Le cache ARP montre des
  dizaines d'adresses derrière deux MAC partagées (`6e:4c:bc:0c:17:3c`,
  `22:23:51:04:0a:cc`) : les répéteurs traduisent les MAC. L'absence de l'OUI
  caméra (`20:32:33`) ne prouve donc rien pour un client du répéteur.

## Interprétation

- **Confirmé :** le coût des transcodeurs croissait avec le nombre de
  spectateurs et dépassait la limite CPU du conteneur.
- **Probable, non démontré :** la saturation a contribué à la perte vidéo
  simultanée (le LIVE est en TCP, la famine retarde la lecture sans perte).
- **Hors pont, confirmé :** une découverte LAN unique (autorisée) a trouvé une
  caméra sur le SSID du répéteur (`WiFi-2.4-5830_bureau`) à
  `192.168.129.100` (série `2041503611700059`) ; `.1` et `.2` répondent
  toujours, `.0` non. Le pont associe le même numéro de série à l'entrée `.0`
  (métadonnées mémorisées) : la caméra avant a changé d'IP par DHCP et le pont
  suit une IP fixe. Sur 17 découvertes autorisées suivantes, une a manqué
  `.100` (et la recherche HA l'a manquée au moins une fois) : la requête
  unique était perdue de façon intermittente via le répéteur.
- Après une coupure vidéo, une caméra ignore parfois la découverte quelques
  secondes ; le backoff existant le gère.

## Modifications

- `internal/manager/h264_transcode.go` : `acquireVideoAdapter` /
  `releaseVideoAdapter` ; un FFmpeg par caméra écrit dans une
  `TrackLocalStaticRTP` partagée (Pion diffuse à chaque liaison). Les erreurs
  d'écriture d'un spectateur n'arrêtent pas les autres ; l'arrêt du FFmpeg
  termine tous ses pairs (`video_transcode_failed`) ; le dernier départ l'arrête.
- `internal/manager/webrtc.go` : les pairs rejoignent l'adaptation de leur
  caméra au lieu d'en démarrer une.
- Le journal `Netvue WebRTC adapting H.264` inclut `camera` et n'apparaît plus
  qu'une fois par adaptation ; un arrêt inattendu est journalisé une fois.
- `internal/netvue/discovery.go` : les découvertes broadcast et unicast
  renvoient la requête toutes les 500 ms pendant la fenêtre de 2 s.

## Tests

- `TestWebRTCVideoAdapterIsSharedPerCamera` : partage, isolation par caméra,
  arrêt au dernier départ, propagation d'échec et non-réutilisation.
- `TestWebRTCTranscodesMain41RTSP` (FFmpeg réel, `NETVUE_TEST_FFMPEG`) : deux
  navigateurs Pion simultanés reçoivent du baseline 3.1 décodable depuis une
  seule adaptation, arrêtée après les deux `DELETE`.
- `TestDiscoverLANResendsLostRequest` : le faux appareil ignore la première
  requête ; échoue sur l'ancien code, passe avec le renvoi.
- `go build ./...`, `go vet ./...`, `go test ./...`,
  `go test -race ./internal/manager/ ./internal/netvue/...`.

## Limites

- Non déployé ; effet en production à confirmer par les mesures cgroup.
- Un nouveau spectateur attend la prochaine IDR (≤ 1 s, `-g 15`).
- L'encodeur audio AAC→Opus reste par pair (≈ 1,7 % CPU chacun).
- La caméra avant doit être reconfigurée sur `192.168.129.100` dans HA ;
  réservation DHCP conseillée. Le pont ne suit pas un changement d'IP par
  numéro de série (amélioration possible).
