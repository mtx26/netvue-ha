# Phase 54 — Cycle WebRTC, timestamps AAC et crashes Windows

## Objectif et périmètre

Reprendre les investigations de phase 53 : réduire les crashes Windows sous
`-race`, comparer les comportements Windows/Linux et les réglages `GOGC`,
corriger le défaut démontré de réservation WebRTC pendant la fermeture, puis
évaluer les discontinuités AAC sans changer arbitrairement la timeline.

Le dépôt était propre au début de la phase. Les quatre modifications Go
présentes sont celles de ce lot. Aucun appareil réel, compte Netvue ni service
de production n'a été contacté.

## Cycle de vie WebRTC

### Défaut établi

La fermeture d'un pair peut se faire en parallèle depuis la fin de session
caméra, la requête HTTP et les workers WebRTC. Le signal d'arrêt utilisé pour
la réservation caméra ne garantissait pas que les appels de fermeture des
ressources — notamment du mux ICE — étaient eux aussi terminés. Un nouveau
pair pouvait donc être autorisé alors que les ressources de l'ancien pair
étaient encore en cours de fermeture.

Un test de régression avec un `PacketConn` dont `Close` est bloqué a échoué
avant la correction : le pair était considéré comme arrêté trop tôt.

### Correction

- `webRTCPeer` distingue désormais la fermeture des ressources de l'arrêt
  complet. `stopped` ne se ferme qu'après la fin des workers et la fermeture
  des ressources.
- Les chemins d'échec de démarrage, de suppression HTTP et d'arrêt caméra
  attendent ce signal avant de considérer la réservation libérée.
- `releaseWebRTCPeer` centralise la suppression et vérifie que le pair publié
  est bien celui qui est libéré, afin de ne pas effacer une réservation plus
  récente.
- Le test `TestWebRTCCloseWaitsForResourcesWhenClosersRace` vérifie que la
  réservation reste occupée tant que la fermeture du mux n'est pas terminée.

Le test de régression a d'abord démontré le défaut, puis les tests de cycle de
vie et la suite `internal/manager` sous `-race` ont réussi après la correction.
Les chemins d'arrêt conservent leur annulation et leur ordre de fermeture ;
aucun format média ni paramètre de protocole n'a changé.

## Diagnostic des crashes Windows

### Environnements et réductions

- Windows : Go 1.26.1, `windows/amd64`, `CGO_ENABLED=1`, compilateur GCC
  MSYS2 15.2.0.
- Linux : Go 1.26.1 dans l'image locale `golang:1.26.1-bookworm`, réseau
  Docker isolé (`--network none`).
- Dépendances WebRTC du projet : Pion WebRTC 4.2.26, ICE 4.4.8, TURN 5.1.3,
  mDNS 2.2.2, SRTP 3.1.3.

Un reproducer Pion réduit à deux pairs par TURN provoque un crash Windows
sous `-race` dans certaines exécutions. La trace passe par la lecture IPv6 de
`pion/mdns/v2`, `internal/poll.FD.execIO`, puis `sync.(*Pool).pin` avec
l'adresse `0x100000010`. Aucun rapport `WARNING: DATA RACE` n'accompagne ce
crash.

### Comparaisons

- Le scénario intégré TURN sur Windows a été exécuté trois fois par réglage.
  Après la correction du cycle de vie, `race/GOGC=100` a crashé ; les cas
  `race/GOGC=20`, `race/GOGC=off`, `sans race/GOGC=100` et
  `sans race/GOGC=20` ont réussi. Des essais précédents avaient également
  produit des résultats différents sous `GOGC=20`.
- Le scénario intégré TURN Linux a réussi trois fois pour chacun des réglages
  `race/GOGC=100`, `race/GOGC=20`, `race/GOGC=off`, `sans race/GOGC=100` et
  `sans race/GOGC=20`.
- La réduction à deux pairs TURN sous Linux a réussi cinq fois avec mDNS et
  `-race`, dix fois avec mDNS désactivé et `-race`, et dix fois avec mDNS et
  sans `-race`. La variante directe à deux pairs avec mDNS et `-race` a
  réussi dix fois.
- Sous Windows, désactiver mDNS a permis dix exécutions réussies du test
  réduit ; mDNS activé avec `GOGC=20` a réussi trois fois dans cette série.
  Ces petits échantillons ne démontrent ni une causalité mDNS, ni une relation
  causale avec le GC. mDNS n'a pas été désactivé dans le produit.
- Le premier `go test -race ./... -count=1` Windows de cette phase a connu un
  autre crash, distinct : `unexpected fault address` dans
  `runtime.deferconvert`, pendant `syscall.StartProcess` appelé par
  `DecodeVideoPixel` dans `TestE2EMultiCameraIsolation` pour lancer FFmpeg.
  Aucun rapport de race Go n'a été émis. Le test multi-caméra isolé a ensuite
  réussi une fois puis trois fois avec `-race`, et la suite complète race a
  réussi à sa relance.

Les traces des deux familles de crash et les matrices précédentes sont
préservées dans les fichiers de session `phase54-windows-two-peer-crash.log`,
`phase54-windows-full-race-crash.log`,
`phase54-windows-turn-matrix-after-fix.log`,
`phase54-linux-turn-matrix.log` et `phase54-linux-minimal-turn.log`.

### Conclusion provisoire

Les résultats restent intermittents et les deux piles sont différentes. Ils
ne prouvent ni un défaut du runtime Go, ni un bug de Pion, de mDNS ou de TURN,
ni une cause commune. Le reproducer Windows réduit reste la prochaine cible ;
les répétitions sont restées bornées aux matrices consignées.

## Discontinuités AAC/Opus

Le chemin caméra AAC → WebRTC reconstruit les unités ADTS sans timestamps
individuels pour l'entrée FFmpeg. Le premier PTS AAC sert d'ancre RTP ; les
PTS ultérieurs, y compris les sauts avant ou arrière, ne traversent pas le
transcodeur. Les tests synthétiques confirment que les timestamps Opus restent
contigus relativement à la ligne de base et monotones, y compris au wrap RTP.

Cela préserve la monotonie, mais peut comprimer une lacune audio et décaler
l'audio par rapport à la vidéo après une discontinuité source. Les tests
n'établissent ni la latence du transcodeur ni la synchronisation perceptuelle,
et les spécifications ne prescrivent pas de politique de rattrapage. Le
comportement reste donc inchangé et doit être considéré comme best-effort,
sans garantie de synchronisation A/V stricte. Une politique future devra
préserver ou représenter les timestamps par unité après validation de leur
correspondance à travers FFmpeg ; aucun remplissage, saut ou ré-ancrage
arbitraire n'est introduit ici.

La limitation est également signalée dans la
[spécification du client LAN](../docs/netvue-lan-client-spec.md).

## Validations

| Validation | Résultat |
|---|---|
| Régression WebRTC avant correction | Échec démontrant la libération prématurée |
| Tests ciblés fermeture/réservation, WebRTC synthétique et TURN avec `-race` | Réussis après correction |
| `go test ./... -count=1` avec FFmpeg, MediaMTX et bridge enfant instrumenté | Réussi sur Windows |
| Premier `go test -race ./... -count=1` | Échec intermittent : crash runtime dans le test E2E multi-caméra |
| `TestE2EMultiCameraIsolation` ciblé avec `-race`, `-count=3` | Réussi trois fois |
| Nouvelle exécution de `go test -race ./... -count=1` | Réussie sur Windows ; le crash antérieur reste non résolu |
| `go vet ./...` | Réussi |
| `go build ./...` | Réussi |
| `gofmt` sur les quatre fichiers Go modifiés | Aucun fichier à reformater |
| `git diff --check` | Réussi ; avertissements Git de conversion LF/CRLF uniquement |
| Matrices TURN Windows/Linux, `-race`, `GOGC` et réductions Pion | Résultats détaillés ci-dessus ; crashes Windows intermittents |

Les tests Python/Home Assistant n'ont pas été relancés : aucun module Python
n'a été modifié dans cette phase. Les résultats synthétiques antérieurs ne
constituent pas une validation réelle. Aucune mesure de performance ni mesure
de synchronisation perceptuelle n'a été réalisée.

## Modifications et état restant

Fichiers Go modifiés :

- `internal/manager/webrtc.go`
- `internal/manager/webrtc_http.go`
- `internal/manager/manager.go`
- `internal/manager/webrtc_test.go`

Documentation mise à jour :

- `docs/refactor-progress.md`
- `docs/netvue-lan-client-spec.md`
- `findings/phase54-crashes-cycle-webrtc.md`

La refonte globale reste **incomplète**. Prochaine étape : poursuivre
l'examen du cycle de vie des sessions et des reconnexions dans le manager,
puis traiter les frontières HTTP et Home Assistant. Garder la réduction du
crash Windows comme investigation distincte ; ne pas déclarer résolue une
instabilité intermittente sur la seule base d'une relance réussie.
