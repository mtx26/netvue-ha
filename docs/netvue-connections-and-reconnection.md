# Délais de connexion et reconnexion Netvue

Référence concise des règles utilisées par le pont Go. Ce sont des limites
d'implémentation, sauf indication contraire.

Références : [protocole](./netvue-protocol.md),
[spécification LAN](./netvue-lan-client-spec.md),
[pont](./bridge.md).

## Connexion caméra

| Étape | Délai | Règle |
|---|---:|---|
| Découverte UDP | 2 s | `Q=8,V=2,P=[]`, port 36868 ; cible ou broadcast. |
| TCP caméra | 2 s | Connexion à l'adresse et au port annoncés. |
| Écriture des commandes | 2 s | LOGIN, LIVE, direction audio et talkback. |
| LOGIN | 4 s | Au plus 8 paquets ; exigence `A=1,V=1/2,C=0`. |
| Preflight RTSP local | 2 s | Vérification de l'écoute avant découverte. |
| RTSP gortsplib | 3 s | Lecture et écriture TCP locales. |

Après LOGIN, la caméra reçoit `Q=3,V=2` avec vidéo `[1,0]`, puis audio
`[2,0]` si sa découverte l'annonce. L'intervalle LIVE est limité à maintenant
plus un an.

- En cas de refus A/V, une nouvelle demande vidéo seule est tentée une fois.
- Aucun média n'est accepté avant `A=3,V=1/2,C=0`.
- Après sélection audio, la direction est `P=[1]` pour la réception.
- Talkback : `P=[3]`, puis retour à `P=[1]`.
- Une écriture réussie de `P=[3]` ne prouve ni l'acceptation matérielle ni
  l'émission sonore.

## Watchdog vidéo

| Situation | Délai | Effet |
|---|---:|---|
| Première image publiable | 15 s | Fermeture : démarrage vidéo impossible. |
| Après la première image | 30 s | Fermeture : flux vidéo interrompu. |
| Changement SPS/PPS | 15 s | Attente de la prochaine IDR. |

La détection porte sur la réception et la publication vidéo, pas sur l'audio.
Un paquet vidéo exactement répété avec le même PTS n'est pas republié ; un PTS
invariant pendant 30 s interrompt la session. Une collision ponctuelle ou un
retour de PTS reste accepté. L'APK tolère 30 s d'inactivité de lecture en LAN,
mais cela ne démontre pas son watchdog vidéo. Les lecteurs WebRTC (ffmpeg,
gortsplib) attendent 12 s et peuvent donc se reconnecter avant la caméra.

## Reconnexion

La supervision découvre la caméra puis conserve en mémoire les informations
d'une authentification réussie. Après une coupure, elle tente d'abord TCP et
LOGIN directement ; en cas d'erreur réseau ou de refus du login, elle
redécouvre avant de réessayer. Une découverte fraîche suivie d'un refus de
login reste fatale. La clé n'est ni persistée ni journalisée.

```text
2 s -> 4 s -> 8 s -> 16 s -> 30 s -> 30 s...
```

- Maximum : 30 s, avec une variation aléatoire entre 75 % et 100 % du délai.
- Après au moins 5 min de fonctionnement stable : retour à 2 s.
- Erreurs récupérables : discovery, réseau, EOF, interruption, timeout,
  première vidéo absente et réponse RTSP 503.
- Erreurs fatales : login rejeté, protocole/trame invalide, URL invalide,
  erreur vidéo fatale et RTSP 403.
- Pas de plafond global de tentatives.
- Annulation explicite : fermeture immédiate des sockets.

Pendant les reprises récupérables, l'état HA reste `supervising`.

### Santé par caméra

L'API expose séparément `health` : `connected` après publication vidéo,
`degraded` après 10 s sans image publiée, `reconnecting` pendant les essais,
`unavailable` après trois échecs consécutifs ou une erreur fatale, et
`recovered` pendant 35 s après la reprise. La dégradation ne ferme pas la
session. La carte HA affiche la reconnexion lorsque le diagnostic est connu.

La dernière image publiée est échantillonnée au plus une fois par seconde.
Le compteur compte les sessions récupérables échouées, pas les connexions
réussies. La durée d'indisponibilité est celle de la coupure en cours ou de
la dernière coupure, estimée depuis la dernière image observée ; elle n'est
donc pas une mesure exacte des trous d'enregistrement. Les compteurs sont
réinitialisés au redémarrage manuel de la supervision. La dernière erreur est
un diagnostic assaini.

La configuration d'enregistrement MediaMTX est réappliquée après une reprise
vidéo et réessayée après échec avec backoff. Son indicateur actif exige une
source disponible et une configuration MediaMTX activée ; il ne prouve pas
que les fichiers ont été écrits. Les trous restent établis à partir des
segments réellement disponibles.

## Audio

- Audio demandé uniquement si annoncé dans la découverte.
- AAC/ADTS : publié en RTP ; caméra réelle surtout 8 kHz mono.
- PCM : publié en L16 16 bits ; l'ordre des octets n'est pas établi pour tous
  les firmwares.
- Un format invalide désactive l'audio sans fermer la vidéo.
- Aucun watchdog séparé ne surveille le silence audio.

## Talkback Netvue

| Élément | Valeur |
|---|---:|
| Fichier AAC-LC/ADTS | 30 s max |
| Taille du fichier | 2 MiB max |
| Session autonome | 2 min max |
| Délai initial autonome | 0 à 10 s |
| Écriture/timeout caméra | 2 s |
| Ouverture WebRTC | 5 s |
| Fermeture WebRTC | 5 s |
| Keepalive WebRTC | 2 s |
| Durée continue WebRTC | 1 min |
| Cadence | 1024 échantillons par trame |

Les trames sont cadencées selon la fréquence annoncée, sans rattraper les
écritures lentes par rafale. Le stream talkback est lié à la session active ;
il ne survit pas à une reconnexion.

## WebRTC

| Élément | Valeur |
|---|---:|
| Préparation RTSP et SDP | 15 s maximum |
| Établissement ICE après préparation | 15 s |
| Durée maximale d'un pair | 12 h |
| Pairs maximum par caméra | 4 |
| Événements conservés | 128 |
| Candidats ICE | 128 |
| Tombstone d'une offre | 10 min |
| Long-poll événements | 20 s |

Le pair est actif après l'état Pion `connected`. Une défaillance ICE, un arrêt
de la caméra, une fermeture ou 12 h ferme immédiatement le pair.

Si le DESCRIBE RTSP retourne 404 ou 503 pendant une republication, le pont
réessaie toutes les 500 ms dans la même offre, sans dépasser la deadline de
préparation. Les autres erreurs échouent immédiatement. Chaque lecteur RTSP
échoué est fermé avant la reprise ; fermer l'offre annule aussi cette attente.
Une caméra réellement injoignable reste en erreur : attendre plus ne la
rend pas accessible.

La carte réessaie une négociation récupérable avec un nouvel identifiant :
1, 2, 4, 8, 16 puis 30 s, avec la même variation aléatoire. Les essais
continuent au maximum de 30 s tant que la carte est active ; les erreurs
classées fatales exigent toujours une reprise manuelle.

Un échec d’envoi de candidat ICE ferme la négociation courante et suit cette
même classification de reprise ; il n’est plus ignoré. Le watchdog vidéo de
la carte utilise `totalVideoFrames - droppedVideoFrames` lorsque le navigateur
expose ces compteurs. Sans ces compteurs, il retombe sur `currentTime`, qui
peut aussi avancer pendant que seul l’audio progresse.

### Audio caméra vers navigateur

Transcodage AAC vers Opus, avec reprise après échec rapide :

```text
1 s -> 2 s -> 4 s
```

- Trois reprises maximum.
- Reprise remise à zéro après 30 s de fonctionnement stable.
- Après l'échec maximal, l'audio est désactivé sans fermer la vidéo.

### Push-to-talk

Le navigateur utilise le canal `netvue-ptt` et ne renégocie pas le peer.

1. `start` coupe l'audio caméra vers le navigateur.
2. Le pont envoie la direction `P=[3]`.
3. Les paquets RTP microphone deviennent des trames AAC-LC/ADTS.
4. `keepalive` renouvelle la deadline toutes les 2 s.
5. `stop`, keepalive perdu ou durée maximale ferme le segment.
6. Le pont restaure `P=[1]` et revient à l'écoute.

Limites :

- 32 paquets RTP en file locale par appui.
- 256 octets maximum par message de contrôle.
- Les identifiants `start` doivent être strictement croissants.
- Mode half-duplex : pas de `replaceTrack` ni de renégociation.

## TURN et HTTP

| Élément | Valeur |
|---|---:|
| TTL TURN Cloudflare | 1 h |
| Renouvellement anticipé | 15 min |
| Délai d'attente après échec TURN | 1 min |
| Validité minimum au démarrage | 10 min |
| Timeout API TURN | 3 s |
| Timeout HTTP HA courant | 5 s |
| Timeout ouverture WebRTC HA | 20 s |
| Attente de réponse SDP dans la carte | 25 s |
| Timeout découverte HA | 30 s |
| Timeout sonde RTSP HA | 4 s |
| Timeout événements HA | 35 s |
| Mise à jour coordinateur HA | 30 s |
| Rafraîchissement TURN HA | 60 s |

Sans au moins 10 min de validité TURN, aucune nouvelle paire ne reçoit ces
credentials.

## Arrêt

L'annulation ferme les sockets Netvue et RTSP, ferme les pairs WebRTC,
annule les transcodeurs FFmpeg et attend les workers. L'arrêt global possède
une deadline de 20 s.
