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

## Règles exactes de connexion et de reconnexion

### Connexion caméra Netvue

| Étape | Délai ou limite | Comportement |
|---|---:|---|
| Découverte LAN | 2 s | Une requête UDP `Q=8,V=2,P=[]` est envoyée sur le port 36868 ; la collecte s'arrête après 2 s. |
| Connexion TCP | 2 s | La caméra est jointe à son adresse/port découverts avec un délai de connexion de 2 s. |
| Écriture des commandes | 2 s | Les écritures LOGIN, LIVE et talkback utilisent une échéance maximale de 2 s, réduite par le contexte actif. |
| Authentification LOGIN | 4 s | Au plus 8 paquets de réponse sont inspectés ; une réponse `A=1,V=1/2,C=0` est requise. Tout `C` absent ou non nul est un échec. |
| Confirmation LIVE | Deadline vidéo | Il n'y a pas de minuteur LIVE séparé : la réception utilise les échéances vidéo ci-dessous après l'envoi de `Q=3,V=2`. |
| RTSP local | 3 s | Ouverture/lecture du lien RTSP local : timeout de 3 s côté pont. |

Après découverte, le pont envoie la sélection vidéo seule `[1,0]`, puis ajoute l'audio `[2,0]` uniquement si la découverte annonce l'audio. Si la caméra refuse la sélection A/V, une seconde tentative en vidéo seule est effectuée une fois. Aucun média n'est accepté avant la confirmation LIVE `A=3,C=0`.

### Reconnexion de la caméra

La session vidéo continue est supervisée en séquence. Les délais de reprise sont exponentiels :

```text
2 s -> 4 s -> 8 s -> 16 s -> 30 s -> 30 s ...
```

Le maximum est 30 s. Si une session a duré au moins 5 minutes avant son échec, le prochain délai repart à 2 s. Les erreurs de protocole, d'authentification rejetée et les erreurs vidéo fatales ne sont pas reprises automatiquement. La fermeture/coupure du contexte annule immédiatement les sockets et les lectures/écritures bloquées.

### Vidéo publiée

| Situation | Délai | Résultat |
|---|---:|---|
| Première publication H.264 | 5 s | Sans première image publiable/IDR avant 5 s, la session passe en erreur de démarrage. |
| Après une première publication | 3 s | Sans nouvelle image publiée pendant 3 s, le flux est considéré interrompu. |
| Changement SPS/PPS | 5 s | Après changement de paramètres, une nouvelle image attendue devient obligatoire sous 5 s ; la publication reprend sur un IDR. |

La détection porte sur une image H.264 effectivement publiée, pas sur la simple réception de paquets ou d'audio. Une trame audio ne prolonge donc pas le délai vidéo.

### Audio caméra vers navigateur

L'audio est facultatif et n'affecte pas la vidéo. Le transcodage AAC vers Opus peut redémarrer jusqu'à trois fois après des échecs rapides. Les délais entre essais sont :

```text
1 s -> 2 s -> 4 s
```

Si un processus audio reste actif au moins 30 s, son historique d'échecs est remis à zéro. Après trois redémarrages sans stabilité de 30 s, l'audio est désactivé pour cette session WebRTC, tandis que la vidéo continue.

### Session WebRTC

| Élément | Valeur |
|---|---:|
| Établissement initial | 15 s |
| Durée maximale par pair | 12 h |
| Nombre maximal de pairs par caméra | 4 |
| Invalidation d'une offre répétée | Tombstone conservé 10 min |
| Long-poll des événements | 20 s |
| Lecture HTTP Home Assistant | 35 s |
| Mise à jour coordinateur Home Assistant | 30 s |

Un pair est considéré actif dès le changement Pion `connected`. Une défaillance ICE, un arrêt de la caméra, la fermeture de la session ou l'expiration du garde-fou 12 h ferme le pair et ses workers.

### Push-to-talk

Le canal de données s'appelle `netvue-ptt`. Il ne reconstruit ni ne renégocie le `RTCPeerConnection`.

| Élément | Valeur |
|---|---:|
| Ouverture talkback côté caméra | 5 s |
| Fermeture/restauration de la réception caméra | 5 s |
| Perte keepalive | 2 s |
| Durée maximale continue | 1 min |
| Durée maximale d'une session ADTS autonome | 30 s |
| Durée maximale d'une session autonome | 2 min |
| Pause initiale du mode ADTS autonome | 0 à 10 s |

Une pression active coupe immédiatement l'audio caméra vers le navigateur. Le canal reste half-duplex jusqu'à la fermeture propre du segment. Chaque `start` porte un identifiant supérieur au précédent. Une répétition ou un identifiant inférieur est ignorée. `stop` termine le segment demandé ; `keepalive` doit renouveler le délai de 2 s. Une ouverture confirme seulement l'écriture de la commande, pas l'émission audible par le haut-parleur caméra.

### Identifiants TURN

Les identifiants Cloudflare TURN ont une durée de base d'une heure. Le pont tente leur renouvellement 15 min avant expiration et attend 1 min avant une nouvelle tentative après échec. Une session WebRTC ne reçoit des credentials TURN que si elle conserve au moins 10 min de validité.

## Correction du scénario E2E CI

- Échec du run `44799148553` reproduit : zéro trame talkback et direction
  `[1]`. Le test envoyait seulement du RTP, sans le contrôle explicite désormais
  requis ; aucun défaut du protocole caméra n'est démontré par cet échec.
- Le test crée maintenant `netvue-ptt`, attend les états du serveur, envoie
  `start`, des keepalives puis `stop`. Les contrôles ADTS, fréquence du signal
  et cadence restent inchangés. Il exige les directions `[1, 3, 1]` et la
  conservation de la session caméra.
- Le retour en direction `1` est attendu côté simulateur : sa lecture TCP
  peut suivre la notification WebRTC `listening`.
- Validation hors ligne : dix répétitions talkback Linux réussies, puis suite
  E2E Linux complète réussie avec les binaires précompilés comme en CI,
  MediaMTX 1.21.4 et FFmpeg 8.1. `go vet ./tests/e2e` et 44 tests Node réussis.
  La commande Node avec glob, déjà corrigée localement dans le workflow avant
  cette intervention, est conservée et documentée.
- Limites : aucun nouveau run GitHub ni essai matériel. Les répétitions
  Windows ont révélé des pertes RTP intermittentes avec échec du contrôle de
  niveau sonore ; leur cause reste à diagnostiquer, sans assouplir les seuils.
