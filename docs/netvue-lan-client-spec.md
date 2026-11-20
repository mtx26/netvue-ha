# Spécification de reconstruction du client LAN

## Objectif et limites

Ce document permet de reconstruire un client **LAN classique** capable de
découvrir une caméra compatible, de s'authentifier, de recevoir vidéo et
audio et de tenter l'envoi audio. Il décrit les données et le comportement,
sans reproduire le code de l'application. Ce n'est pas une spécification
officielle ni une garantie de fonctionnement sur toutes les caméras.

L'APK local étudié est Netvue Android 5.27.0, versionCode 121868, SHA-256
`9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`.
L'empreinte a été recalculée pour ce rapport. La décompilation précédente par
JADX 1.5.6 comportait 155 erreurs : les conclusions s'appliquent aux chemins
inspectés, pas nécessairement à toute l'application.

Trois niveaux de preuve sont employés :

- **APK** : comportement du code Android examiné statiquement.
- **Réel historique** : observation antérieure sur un exemplaire précis, pas
  un nouveau test ni une preuve pour tous les modèles.
- **Go** : choix de l'implémentation actuelle, couverts par des tests locaux ;
  une limite Go n'est pas forcément une limite imposée par la caméra.

Le détail des preuves se trouve dans la
[spécification protocolaire](../docs/netvue-protocol.md) et les
[phases 40](./phase40-analyse-protocole-lan.md) et
[41](./phase41-detail-protocole-lan.md). Les chemins `decompiled/` cités
ci-dessous sont des artefacts locaux ignorés par Git ; ils ne sont pas un SDK
publié ni une API stable.

## 1. Architecture et composants

### Chemin étudié

```text
Découverte UDP → adresse, port TCP, clé locale, capacité audio
  → TCP → authentification → sélection LIVE vidéo/audio
  → réception multiplexée des commandes et médias
       ├─ vidéo H.264 → RTP/RTSP → lecteur / Home Assistant
       └─ audio AAC ou PCM → RTP/RTSP → lecteur
  ← microphone → encodage AAC → direction audio → médias sur le même TCP
```

Le service Android choisit aussi d'autres branches, notamment CameraII,
relais TCP et Agora ; elles ne sont pas interchangeables avec ce protocole
([sélection](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L340)).
Il ne faut pas transposer les messages ou en-têtes CameraII ici.

### Références APK par responsabilité

| Responsabilité | Classe ou méthode | Rôle observé |
|---|---|---|
| Découverte | [`NvCameraFinder.startSearch`](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L61) | Recherche LAN et traitement des réponses. |
| Connexion | [`NvCameraLANService.onCameraServiceStart`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L38) | Obtient l'endpoint de la configuration et lance le connecteur TCP. |
| Login | [`makeLoginUnit`](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L91) ; [`doLogin`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L113) | Prépare puis envoie la commande locale. |
| Encadrement | [`NetviewPacketHead`](./decompiled/sources/com/netviewtech/client/packet/common/NetviewPacketHead.java) ; [`NvMINADecoderTCP`](./decompiled/sources/com/netviewtech/client/connection/camera/codec/NvMINADecoderTCP.java) | En-tête et décodage TCP. |
| LIVE | [`doSelectChannelAndReadPlugins`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L358) ; [`makeSelectChannel`](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L111) | Prépare la sélection et des commandes de réglage/lecture de plugins. |
| Réception | [`onCameraPacketReceived`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L97) ; [`handleCameraDataPacket`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L459) | Distingue commande et média, puis transmet les trames. |
| Trames | [`NvCameraMediaFrame.decode`](./decompiled/sources/com/netviewtech/client/packet/camera/data/NvCameraMediaFrame.java#L142) | Interprète type, flags, PTS et charge utile. |
| Direction audio | [`handleCameraSelectedChannels`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L532) | Demande la direction bidirectionnelle après sélection audio. |
| Microphone | [`AndroidAECVoiceService`](./decompiled/sources/com/netviewtech/client/media/audio/AndroidAECVoiceService.java#L321) ; [`NVAudioHandlerV2`](./decompiled/sources/com/netviewtech/client/media/audio/NVAudioHandlerV2.java#L294) | Capture Android, découpage et encodage AAC natif. |
| Envoi média | [`onAudioRecorded`](./decompiled/sources/com/netviewtech/client/player/NVCameraPlayerImpl.java#L88) ; [`sendMediaFrame`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L185) | Construit une trame AAC et la transmet au service actif. |
| Arrêt | [`stop`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L193) ; [`onCameraDisconnected`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L109) | Arrête le service et ferme le connecteur. |

Ce sont des composants Android internes, certains non publics, utilisant
notamment les classes Android, Apache MINA et des bibliothèques natives.
Leur présence ne prouve pas qu'on puisse les importer comme un JAR autonome
dans un programme Go ou Java SE.

## 2. Sécurité et prérequis

- Utiliser uniquement une caméra et un réseau pour lesquels on est autorisé.
  Disposer d'une adresse locale et de la capacité à recevoir la réponse UDP ;
  la clé locale et le port sont fournis par la découverte ou par une
  configuration déjà autorisée.
- Le login LAN observé n'a pas de chiffrement applicatif apparent. La clé peut
  être visible par un observateur du réseau ; limiter ce transport à un LAN
  de confiance. Ajouter du TLS seulement si les deux extrémités le prennent en
  charge : l'imposer unilatéralement casserait l'interopérabilité.
- Ne pas publier ni journaliser la clé, les identifiants, les JSON bruts de
  découverte/login, les médias ou les captures. Le `doLogin` Android examiné
  contient lui-même un appel de journalisation de champs sensibles : le
  réutiliser directement sans maîtriser ses logs peut exposer des secrets.
- Ne pas exposer RTSP ou une interface de contrôle sur Internet par défaut.
  Préférer un réseau isolé et une authentification adaptée au déploiement.
- Les noms d'utilisateur et adresses dans les exemples suivants sont
  **fictifs**. Ne pas remplacer ces exemples par des données réelles dans les
  tests ou dans le dépôt.

## 3. Enveloppe réseau commune

Une commande ou un média est constitué d'un en-tête de 16 octets suivi du
corps. Entiers en **little-endian**.

| Offset | Octets | Champ | Observé / choix |
|---:|---:|---|---|
| 0 | 4 | Taille du corps, sans l'en-tête | Longueur non signée. |
| 4 | 4 | Séquence | Go envoie 0 ; rôle exact inconnu. |
| 8 | 4 | CRC nommé ainsi par l'APK | Go envoie 0 ; algorithme et couverture inconnus. |
| 12 | 2 | Flags d'enveloppe | Go envoie 0 et rejette les valeurs non nulles. |
| 14 | 2 | Type | `200` commande JSON ; `201` média. |
| 16 | variable | Corps | UTF-8 JSON ou en-tête média et charge utile. |

**Réel historique :** séquence, CRC et flags externes étaient nuls dans les
paquets observés en phase 41. Cela ne démontre pas le comportement de la
caméra face à des valeurs non nulles. Ne pas inventer un calcul de CRC.

**Go :** taille de corps strictement supérieure à 0 et inférieure à 1 Mio.
Lire exactement 16 octets puis exactement la longueur annoncée ; une lecture
TCP peut être partielle et plusieurs paquets peuvent arriver ensemble. Une
écriture doit aussi gérer les écritures partielles. Un datagramme UDP doit
contenir exactement un paquet complet. Valider la longueur avant allocation,
les flags avant interprétation et le type attendu à chaque étape.

### Vecteur synthétique de découverte

Corps UTF-8, sans espaces : `[{"Q":8,"V":2,"P":[]}]`. Il contient **22 octets**.
Son en-tête est :

```text
16 00 00 00  00 00 00 00  00 00 00 00  00 00  c8 00
```

Concaténer cet en-tête et les 22 octets UTF-8 du corps. Le nombre `0x16`
représente la taille en octets, pas le nombre de caractères d'un JSON
quelconque. Les commandes contiennent un **tableau** d'objets : un même
paquet peut transporter plusieurs unités.

## 4. Découverte

**APK :** `Q=8`, `V=2`, `P=[]` en UDP port destination **36868**, notamment en
broadcast sur le LAN. **Go :** utilise aussi un unicast ciblé et attend jusqu'à
2 secondes ; la recherche broadcast collecte les réponses pendant cette durée.
Ces 2 secondes sont une politique client, pas un délai du protocole.
L'APK peut aussi placer une liste d'identifiants dans `P` pour filtrer la
recherche ; le pont envoie un tableau vide.

Une réponse utile est un paquet de commande contenant une unité `A=8`, `V=2`.
Les positions utilisées par le pont dans son tableau `P` sont :

| Position | Utilisation | Précaution |
|---:|---|---|
| 0–1 | Identifiant appareil et version | Noms donnés par le parseur Android ; Go ne les utilise pas. |
| 2 | Adresse IPv4 annoncée | Vérifier qu'elle correspond à l'émetteur et à la cible attendue. |
| 3 | Port TCP annoncé | Entier de 1 à 65535 ; ne pas supposer 5555 universel. |
| 4 | Clé locale | Chaîne non vide et sensible ; ne pas l'afficher. |
| 5, puis 0 | Indicateur de capacité audio | Optionnel ; sa présence ou son absence ne prouve pas tous les codecs. |

Le [parseur Android V2](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/ack/NvCameraCMDBroadcastDiscoveryAckV2.java#L32)
lit aussi dans `P[5]` les indicateurs flip, lumière, carte SD, le SSID et un
entier nommé `wifiSL`. Les positions facultatives `P[6]`, `P[7]` et `P[8]`
sont nommées respectivement `ability`, `clientVersion` et `offlineToken` par
ce parseur. Le [finder](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L136)
transfère ces champs dans le nœud de l'application ; leur nom ne suffit pas à
établir leur sémantique complète côté firmware. Identifiants, SSID et token
sont sensibles et ne doivent pas être publiés. Le pont n'en dépend pas.

Go accepte un résultat `C` absent dans la découverte ; s'il existe, il doit
être un entier nul. La réponse, son adresse et sa longueur doivent être
validées avant tout usage. Une découverte réussie ne prouve pas que le flux ou
le talkback fonctionneront.

## 5. Connexion et authentification

1. Se connecter en TCP à l'adresse et au port annoncés. L'APK utilise une
   tentative de connexion de 2 s ; Go applique aussi 2 s.
2. Envoyer un paquet type 200 contenant une unité de la forme suivante :

   ```json
   [{"Q":1,"V":1,"P":["cle-fictive",123456,"localUser","192.0.2.10",12345]}]
   ```

   L'adresse `192.0.2.10` est réservée à la documentation. Les champs sont,
   dans l'ordre : clé locale, entier aléatoire signé 32 bits, utilisateur,
   hôte et port du nœud. L'APK utilise `localUser` si le nom fourni est vide ;
   Go utilise cette valeur. Le rôle de l'entier aléatoire côté caméra n'est
   pas établi : ne pas le présenter comme un challenge cryptographique.
3. Attendre une unité de réponse avec `A=1`, `V=1` ou `V=2`. Exemple fictif :

   ```json
   [{"A":1,"V":1,"C":0}]
   ```

4. **Politique Go recommandée :** exiger un entier `C` explicitement présent
   et égal à 0 avant LIVE ; un résultat différent est un refus. Le gestionnaire
   Android examiné ne fait pas cette vérification explicite. Go borne l'attente
   à 4 s et 8 paquets, puis ferme en cas d'échec.

Les valeurs de login et les réponses complètes ne doivent jamais être
imprimées. Une erreur d'authentification n'est pas un succès partiel et ne
doit pas déclencher des tentatives illimitées.

## 6. Sélection LIVE et commandes audio

### Sélection des canaux

Après succès du login, envoyer par exemple :

```json
[{"Q":3,"V":2,"P":[1,[[1,0],[2,0]],[1700000000,1731536000]]}]
```

Les dates sont fictives. `P[0]=1` correspond à la tâche LIVE ; `P[1]`
contient les couples `[type,niveau]` : vidéo `1`, audio `2`, niveau `0` dans
le choix Go. `P[2]` contient début et fin en **secondes Unix** ; Go utilise
maintenant et maintenant + 31 536 000 s. Le code APK convertit sa
configuration depuis des millisecondes en secondes.

Attendre `A=3`, version `1` ou `2`, avec `C=0`. Une entrée de canal décodée
par le pont a la forme `[canal,tache,type,parametres]`. Pour l'audio, les
paramètres sont `[frequence,samplesize,canaux]`. Sur l'exemplaire testé, ils
étaient `[8000,1,1]` : **`samplesize=1` n'est pas démontré comme une
profondeur de 1 bit**.

Go demande l'audio si la découverte l'annonce ; si la sélection A/V est
refusée, il essaie une fois en vidéo seule. Il exige l'ACK LIVE avant un
paquet média et accepte d'autres unités de commande dans le flux. Lors d'un
test historique, deux ACK `A=3` ont été observés pour une sélection ; le
second est arrivé après le début des médias. Sa cause est inconnue.

### Direction audio

| Valeur `P[0]` | Nom APK | Sens |
|---:|---|---|
| 0 | `OFF` | Arrêt. |
| 1 | `DEVICE2CLIENT` | Caméra vers client. |
| 2 | `CLIENT2DEVICE` | Client vers caméra. |
| 3 | `TWOWAY` | Bidirectionnel. |

La commande est `Q=5`, `V=1`, `P=[direction]`. Après sélection audio, le
chemin Android étudié envoie `[3]` sans attendre explicitement son ACK.
Go demande `[1]` pour la réception et `[3]` lors de l'ouverture du talkback ;
à sa fermeture, il redemande `[1]`. Le code Go traite un éventuel refus `A=5`
pour le talkback, mais une écriture réussie **ne prouve pas** l'acceptation.
Dans l'essai historique de 3,53 s, aucun ACK `A=5` n'a été observé.

L'APK envoie aussi un réglage `Q=7`, `V=1`,
`P=[[0,1,[true]]]`, nommé `audioOn`, avant la sélection. Son effet et sa
persistance sont inconnus ; Go ne l'envoie pas. L'absence de Q7 n'a pas
empêché les essais LAN rapportés pour cet exemplaire : ne pas modifier une
configuration caméra sur simple supposition.

## 7. Format des médias

Le corps d'un paquet type `201` a cette forme :

| Offset corps | Octets | Signification |
|---:|---:|---|
| 0 | 1 | Demi-octet haut : `1` vidéo, `2` audio ; bit `0x01` : image clé ; bit `0x02` : OSD vidéo ou PCM audio. |
| 1 | 4 | PTS non signé 32 bits, little-endian. |
| 5 | reste | Charge utile média. |

L'APK traite le type vidéo LAN classique comme H.264 et l'audio comme PCM si
le bit `0x02` est présent, AAC sinon. Les charges utiles vides ou tronquées
doivent être rejetées avant d'être traitées. Le PTS semble correspondre à des
millisecondes dans les chemins et observations disponibles, mais ce n'est pas
une garantie pour tous les firmwares. À 1 ms par unité, un compteur 32 bits
reboucle après environ 49,7 jours ; une remise à zéro et un retour d'horloge
ne sont pas nécessairement un rebouclage naturel.

### Vidéo H.264

- Le pont actuel interprète chaque charge utile vidéo comme une unité
  d'accès H.264 Annex B, avec délimiteurs NAL. Cette hypothèse fonctionne sur
  l'exemplaire testé, pas nécessairement sur tous les modèles ou fragments.
- Lire les NAL, conserver SPS et PPS, attendre une image IDR avant de
  démarrer ; déterminer l'IDR depuis le type NAL, pas uniquement le flag
  Netvue. Rejeter les NAL invalides ou trop grands.
- Pour RTP H.264, l'horloge standard utilisée est de 90 kHz. Go applique
  `delta_ms × 90` ; il avance de 1 ms pour une collision, un retour ou un
  saut de plus de 10 s. C'est une politique de récupération du pont, pas une
  propriété démontrée du protocole. Gérer aussi les changements SPS/PPS et
  attendre une nouvelle IDR.
- L'adaptation H.264 pour WebRTC intervient en aval du RTSP publié. Elle
  ne modifie ni le flux RTSP natif ni les commandes de la caméra.
- **Réel historique :** H.264 a été décodé depuis une caméra ; une transition
  PTS non croissante a aussi été constatée. Les B-frames, fragments génériques
  et l'endurance ne sont pas entièrement établis.

### Audio reçu

- **Réel historique :** AAC-LC/ADTS mono 8 kHz a été décodé simultanément avec
  H.264. Cela ne prouve pas le codec de chaque modèle.
- Pour AAC/ADTS, vérifier les en-têtes, extraire fréquence, nombre de canaux et
  unités AAC ; comparer fréquence/canaux à la sélection si elle est connue.
  Pour RTP MPEG-4 audio, utiliser une bibliothèque qui construit le SDP et la
  fragmentation ; ne pas envoyer aveuglément l'ADTS comme paquet RTP.
- Le pont traite le PCM comme du 16 bits et réordonne les octets pour RTP
  L16. Le sens des octets PCM de cette caméra n'est pas validé ; ne pas le
  déduire du champ `samplesize`.
- Les horloges audio et vidéo doivent être reliées et monotones. L'hypothèse
  d'une origine commune et la synchronisation perceptuelle restent à mesurer
  sur chaque appareil ; une lecture RTSP courte ne prouve pas la stabilité.
- Dans le chemin AAC caméra → Opus/WebRTC actuel, seul le premier PTS AAC ancre
  l'horloge RTP ; les discontinuités suivantes ne sont pas propagées par
  FFmpeg. Les tests synthétiques confirment ce comportement, mais ne mesurent
  pas la synchronisation perceptuelle ; une lacune source peut ne pas être
  représentée dans la timeline audio. Ce chemin ne garantit donc pas une
  synchronisation A/V stricte.

## 8. Envoi microphone : recette expérimentale, non garantie

### Ce que fait le chemin APK étudié

1. Après sélection audio, demander `Q=5`, `V=1`, `P=[3]`.
2. Capturer le microphone Android avec `AudioRecord` ; le chemin
   `NVAudioHandlerV2` découpe les données en blocs de 2048 octets pour son
   traitement AAC. Cette taille est un détail de ce pipeline, pas une exigence
   réseau prouvée.
3. `NVAudioCodec` appelle les fonctions JNI `nvAACEncInit`,
   `nvAACEncOneFrame` et `nvAACEncFinish`. Les analyses ARM64 ont identifié
   une référence à FDK AAC et une configuration AAC-LC dans
   `libmobileffmpeg.so`. Les options effectives ADTS/LATM/global-header
   **ne sont pas établies**.
4. Pour chaque sortie AAC, construire une trame média audio ; le premier octet
   est `0x20`, puis les quatre octets de poids faible de l'heure courante en
   millisecondes, little-endian, puis les octets fournis par l'encodeur.
   L'enveloppe externe utilise le type `201`. Les écrits partagent le même
   connecteur que les autres commandes.

### Choix actuel du pont Go

Le chemin expérimental accepte des unités **AAC-LC/ADTS** dont la fréquence
et le nombre de canaux correspondent au canal sélectionné. Il envoie une
trame ADTS par paquet Netvue, cadence les unités et évite les rafales de
rattrapage. Pour une unité AAC de 1024 échantillons, la durée est
`1024 / fréquence` secondes, soit 128 ms à 8 kHz. Le code Go vérifie
notamment un profil AAC-LC, un seul bloc et une taille ADTS valide ; ce sont
des limites de son implémentation, pas des exigences universelles prouvées.

Pour Home Assistant, le chemin actuel est :

```text
microphone WebRTC/Opus → décodeur Opus → PCM
  → FFmpeg AAC-LC/ADTS → trames Netvue → socket TCP caméra
audio caméra AAC → FFmpeg/libopus → RTP Opus/WebRTC
```

Le relais Cloudflare TURN facultatif ne modifie pas ces formats ni la session
LAN Netvue. Il transporte le média WebRTC chiffré entre le navigateur et le
pont lorsque le chemin direct ne peut pas être utilisé.

Le fournisseur WebRTC est opt-in et `two_way_audio` reste désactivé tant que
la restitution physique n'est pas confirmée. L'absence de RTP microphone
pendant 3 s ferme le talkback ; une reprise peut le rouvrir. Une seule
émission microphone est admise par session ; une reconnexion invalide
l'ancienne session. Sérialiser toutes les écritures sur la socket.

**Preuve réelle historique :** sept trames ADTS synthétiques ont été écrites
sur TCP après sélection audio ; aucun son au haut-parleur n'a été confirmé.
Cela ne démontre ni l'acceptation du format, ni son caractère identique au
flux Android. La [phase 43](./phase43-microphone-ha.md) indique en outre que
son test média FFmpeg a été ignoré dans l'environnement de cette phase.

## 9. Arrêt, délais et concurrence

- L'arrêt LAN Android étudié ferme le connecteur ; aucun envoi périodique
  LAN de heartbeat ni envoi de fermeture `Q=13` n'a été démontré. `Q=15`
  existe et peut être reçu ; son existence ne justifie pas un timer inventé.
- Distinguer une **politique d'application** du protocole : dans Go, le
  délai première image est de 5 s, puis 3 s de silence vidéo ; le superviseur
  réessaie les erreurs récupérables avec backoff 2–30 s, réinitialisé après
  5 minutes de stabilité. L'APK configure un délai d'inactivité de lecture
  de 30 s. Aucun de ces nombres n'est démontré obligatoire pour tous modèles.
- Annuler les lectures et écritures bloquées en fermant la socket ; libérer
  l'encodeur, les processus, les goroutines et le lecteur RTSP à l'arrêt.
  Empêcher deux sessions concurrentes ou deux émetteurs talkback pour une
  même caméra, et isoler les états de caméras différentes.
- Distinguer refus d'authentification, paquet invalide, timeout, flux absent,
  erreur audio et arrêt demandé. Borner les buffers, les allocations et les
  files de médias ; une panne audio ne devrait pas masquer une vidéo viable.

## 10. Outils et dépendances utiles

| Outil | Utilisation | Limite |
|---|---|---|
| JADX 1.5.6 | Lecture statique DEX/ressources. | 155 erreurs sur cette décompilation ; ne restitue pas fidèlement tout le source initial. |
| `javap` / JDK | Inspection d'un JAR Java distinct. | Le JAR reconstitué n'est pas le code original de l'APK et ne charge pas directement le DEX. |
| Android ART / SDK | Exécuter expérimentalement des classes originales sur un environnement Android contrôlé. | Dépendances Android, natives, ressources, architecture CPU et initialisation à résoudre. Aucun test ART n'a été réalisé ici. |
| Go `net`, `encoding/binary`, `encoding/json` | UDP/TCP, cadrage little-endian et commandes. | Les contrôles et les délais restent à implémenter explicitement. |
| `gortsplib/v5`, `mediacommon/v2` | H.264, AAC et encapsulation RTP/RTSP du pont. | Ne remplacent pas le protocole Netvue. |
| MediaMTX | Serveur RTSP séparé, consommable par HA. | Restreindre explicitement les écouteurs et protocoles inutiles. |
| Pion WebRTC/RTP et Opus | WebRTC HA et décodage du microphone du pont. | Ne valident pas le format AAC accepté par la caméra. |
| FFmpeg/FFprobe | Encodage AAC, conversion Opus, inspection/décodage synthétique ou autorisé. | Encodage différent de l'encodeur natif Android ; une exécution doit être bornée et supervisée. |

Les versions Go réellement utilisées sont dans [go.mod](../go.mod) ; la
construction de l'image et son FFmpeg dans [Dockerfile](../Dockerfile).
Les dépendances de l'APK et les ABI ARM ne deviennent pas automatiquement
des dépendances du pont Go.

## 11. Plan de reconstruction et tests

1. **Cadrage hors réseau :** implémenter et tester le vecteur de découverte
   ci-dessus, les lectures et écritures partielles, les tailles 0/limite,
   flags inconnus, JSON tronqué, plusieurs unités et les types inattendus.
2. **Pair TCP/UDP fictif :** simuler découverte, login accepté/refusé, absence
   ou mauvais type de `C`, ACK LIVE V1/V2, sélection audio rejetée, repli
   vidéo seule, ACK répété, déconnexion et annulation. Aucun appareil réel.
3. **Média synthétique :** tester trames tronquées, AAC/PCM invalides, Annex B,
   SPS/PPS, IDR, changements de paramètres, horloges, reset et wrap PTS ;
   vérifier le décodage RTP/RTSP avec MediaMTX et FFmpeg dans un environnement
   isolé. Vérifier les délais, la fermeture et la concurrence multi-caméra.
4. **Talkback simulé :** vérifier `Q=5`, `P=[3]`, le cadrage type 201,
   la cadence des unités, le refus éventuel, l'exclusion mutuelle et le
   retour en réception seule. Un simulateur valide l'implémentation du client,
   pas la restitution physique.
5. **Validation réelle distincte :** seulement avec une autorisation explicite
   désignant la caméra, les opérations et les limites ; vérifier les sessions
   existantes, borner la durée et les tentatives, utiliser un récepteur local
   isolé et fermer proprement. Une confirmation acoustique doit être observée
   de manière indépendante avant d'annoncer le talkback fonctionnel.

Le pont existant permet de repartir du code
[`internal/netvue`](../internal/netvue/) plutôt que de recommencer ; les
tests associés sont dans ce même dossier. Les étapes ci-dessus servent aussi
de critères d'acceptation pour une autre implémentation.

## 12. Bilan et inconnues

Le chemin LAN classique et la réception H.264/AAC sont suffisamment décrits
pour un prototype ciblé ; ils ne garantissent pas la prise en charge
universelle. Les points qui empêchent une affirmation « tout fonctionne »
sont notamment :

- Génération, modèle, firmware et capacités réels de chaque caméra ; origine
  et validité de ses informations locales.
- Algorithme/usage du CRC et de la séquence, options et flags non nuls,
  comportement d'autres versions de réponses et sens du second ACK LIVE.
- Effet et persistance de Q7, signification exacte de `samplesize`, ordre PCM,
  codecs et fréquence réellement annoncés par d'autres firmwares.
- Unité et origine des PTS pour chaque modèle, synchronisation prolongée,
  fragmentation H.264 et comportement sur reconnexion.
- Encapsulation AAC effectivement produite par Android, éventuel effet des ACK
  audio et acceptation sonore physique du talkback.

**Validation de cette phase :** empreinte APK recalculée, sources et rapports
existants recoupés ; `go test ./internal/netvue ./internal/manager` réussi en
local avec résultats en cache. Aucun APK n'a été exécuté, aucun compte ni
caméra contacté, aucun service de production modifié. Les résultats réels
cités sont historiques et décrits dans les rapports référencés.
