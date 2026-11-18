# Phase 24 — Fiabilisation du moteur vidéo Go

## OBJECTIF

Fiabiliser le traitement vidéo Go avant le superviseur continu et le
multi-caméra. Cette phase caractérise les anomalies de timestamps connues,
les limites SPS, les changements de paramètres H.264 et le cycle de vie du
publieur RTSP, sans contacter de caméra ni modifier la production.

Les rapports historiques documentent un timestamp dupliqué et une remise à
zéro, mais ne conservent pas les valeurs PTS brutes ni les images concernées
([phase 15](./phase15-real-stability.md),
[phase 12b](./phase12b-timestamp-recovery.md)). Leur cause exacte reste donc
inconnue.

## MODIFICATIONS

- Dans `netvue-go/internal/netvue/video.go`, l'horloge utilise directement
  les ticks RTP à 90 kHz. Un delta source nul, un retour arrière ou un saut
  supérieur à dix secondes est corrigé par une avance minimale de 1 ms
  (90 ticks), puis la nouvelle valeur source devient l'ancre. Les wraps
  naturels de l'horloge 32 bits restent acceptés. Cette règle ne déduit pas la
  cadence des images et n'utilise pas l'heure d'arrivée réseau.
- La validation SPS borne séparément les dimensions codées et affichées :
  largeur maximale 3840, hauteur maximale 2160, avec un macrobloc de padding
  autorisé pour les dimensions codées. Cela évite qu'un axe dépasse sa limite
  tout en restant sous la limite totale de pixels.
- Le publieur rejette plusieurs SPS ou PPS différents dans une même unité
  d'accès, car il ne gère qu'un jeu de paramètres courant. Les paramètres sont
  validés dans des variables locales et copiés dans l'état du publieur
  uniquement après validation complète : une unité rejetée ne laisse pas de
  SPS/PPS partiellement remplacés. Les changements successifs entre unités
  d'accès sont acceptés. La réinjection teste l'identité des octets, pas
  seulement le type NAL, afin de fournir les SPS/PPS courants.
- Les erreurs RTSP et de parsing conservent leur cause avec `%w`. Le test
  loopback gortsplib exerce trois générations de publication séparées par
  deux changements SPS/PPS ; chaque nouvelle génération démarre à une IDR.

## FONCTIONNEMENT

`videoPublisher.Push` analyse l'unité Annex B, met à jour le jeu SPS/PPS et
arrête une publication active si ces paramètres changent. La publication
suivante attend une IDR et réinjecte les paramètres courants s'ils ne sont pas
déjà présents à l'identique. Les paquets sont horodatés avec la sortie RTP de
l'horloge monotone.

Le test de transitions utilise des SPS synthétiques valides pour trois
résolutions et des PPS synthétiques. Le test RTSP local passe par le client et
le serveur gortsplib ; il vérifie les échanges de contrôle et les trois
enregistrements, sans prétendre décoder les images.

## TESTS

Depuis `netvue-go/` :

- `go test ./...` — réussi.
- `go test -race ./...` — réussi.
- `go vet ./...` — réussi.
- `go build ./cmd/netvue-bridge` — réussi.
- `go test -run TestPublisherRestartsRecordingAfterParameterChanges -count=1 -v ./internal/netvue` — réussi.
- Le même scénario ciblé avec `-race` — réussi.
- `TestPublisherRejectsConflictingParameterSetsWithoutChangingState` — réussi ;
  il vérifie qu'un SPS/PPS modifié puis contradictoire ne corrompt pas l'état
  d'une publication active.

Les tests couvrent collisions, retours arrière, sauts excessifs, remise à zéro,
wrap source et wrap RTP, dimensions SPS par axe, paramètres contradictoires
dans une unité d'accès sans mutation partielle, changements SPS/PPS successifs
et redémarrage RTSP loopback.

MediaMTX et FFmpeg, ainsi que leurs variables d'intégration, sont absents de
l'environnement courant. La publication/décodage indépendante n'a donc pas
été rejouée après ces modifications. La preuve historique de la phase 23 ne
vaut pas validation de cette version.

## ÉTAT FINAL

Le moteur vidéo est corrigé et testé hors ligne pour la politique de
timestamps, les limites SPS, le remplacement répété des paramètres et le
cycle de redémarrage RTSP local. La phase ne valide pas le décodage après
changement de résolution avec FFmpeg/MediaMTX, la stabilité prolongée, les
B-frames, les fragments répartis entre paquets ni les caméras réelles.

Prochaine étape : poursuivre la conception puis l'implémentation du
superviseur unique et du multi-caméra ; rejouer les tests de décodage
indépendant lorsque MediaMTX et FFmpeg seront disponibles localement.
