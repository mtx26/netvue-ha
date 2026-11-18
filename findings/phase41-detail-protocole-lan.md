# Phase 41 — Détails binaires du protocole LAN Netvue

## Objectif et périmètre

Approfondir les conclusions de la phase 40 à partir des sources JADX, du DEX
et des binaires ARM64 déjà présents dans le dépôt, puis vérifier les échanges
sur la caméra autorisée. Deux sessions séquentielles ont été exécutées après
un contrôle passif préalable; l'observateur n'a conservé que des métadonnées
expurgées, jamais de PCAP ni de charge utile audio/vidéo.

Les conclusions sur les classes Android décrivent les chemins statiques de
l'APK 5.27.0 / 121868, empreinte SHA-256
`9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`.
JADX a signalé 155 erreurs; la décompilation n'est donc pas exhaustive.
Les chemins cités sont sous
[`decompiled/sources/com/netviewtech/client/`](./decompiled/sources/com/netviewtech/client/).

## 1. Séquence et CRC de l'en-tête

### Construction et interprétation Java

[`NetviewPacketHead.java`](./decompiled/sources/com/netviewtech/client/packet/common/NetviewPacketHead.java#L41)
lit et écrit la séquence comme entier little-endian aux octets 4–7. Les octets
8–11 sont nommés CRC : `setHeadCrc(null)` les met à zéro; une valeur non nulle
est copiée en ordre inversé. Le getter effectue l'opération inverse. Cela
établit le nom, l'emplacement et l'ordre de copie, mais pas un calcul CRC, son
polynôme, sa couverture ni son rôle.

[`NvProtocolPacket.withEmptyHead`](./decompiled/sources/com/netviewtech/client/packet/common/NvProtocolPacket.java#L32)
alloue un en-tête de 16 octets initialement nul. Les chemins d'encodage
[`NetviewAbstractPacket.encode`](./decompiled/sources/com/netviewtech/client/packet/common/NetviewAbstractPacket.java#L32)
et des paquets commande/média renseignent la longueur, le type et les flags;
aucun calcul de séquence ou CRC n'y apparaît.

À la réception, le décodeur TCP classique
[`NvMINADecoderTCP`](./decompiled/sources/com/netviewtech/client/connection/camera/codec/NvMINADecoderTCP.java#L20)
lit le type et la longueur. `NvProtocolPacket.read` fait de même; le parseur
`parseFromBytes` contrôle la taille du corps, mais aucun de ces chemins
n'appelle les getters de séquence ou CRC et ne les valide.

### Recherche des usages DEX et des fonctions natives

La recherche directe parmi les neuf DEX a trouvé dix appels aux deux setters :
cinq `setHeadSeqn(0)` et cinq `setHeadCrc(null)`, dans
`NvCameraIINATSocket` et le contrôle NAT CameraII déprécié. Aucun appel direct
aux getters et aucun incrément de séquence n'a été trouvé. Des références
concrètes apparaissent dans `classes6.dex` aux offsets `0x2ddb96–0x2ddba2` et
`0x2ddfc8–0x2ddfd6`. Ces résultats concernent surtout CameraII/NAT et ne
démontrent pas le comportement du firmware pour la voie LAN classique.

Huit bibliothèques ELF ARM64 exposent au moins un symbole dynamique contenant
« CRC ». Aucun symbole JNI correspondant aux classes de paquet/connecteur
étudiées n'a été établi. Le sondage des symboles dynamiques ne prouve donc pas
un lien avec cet en-tête et n'exclut pas du code natif non exporté.

### Comparaison Go

[`protocol.go`](../internal/netvue/protocol.go) écrit l'en-tête avec séquence
et CRC à zéro. À la lecture, Go valide longueur et flags, sans inspecter ces
deux champs. Cela correspond aux usages Java visibles pour les paquets
classiques, mais aucune donnée disponible ne permet d'affirmer que des valeurs
non nulles seraient acceptées ou rejetées par une caméra. Aucun changement Go
n'est justifié par cette absence de preuve.

## 2. Authentification LAN classique

### Requête : construction → sérialisation → transport

La chaîne statique est :

`NvCameraLANService.onCameraConnected` → `doLogin` →
`NvCameraRequestMaker.makeLoginUnit` →
`NvCameraCMDClientLoginReq.writeToTarget` →
`NvCameraCommandPacket.wrapCommand` → `AbsNvIoConnector.write` →
encodeur du connecteur TCP.

Références : [`NvCameraLANService.java`](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L39),
[`NvCameraRequestMaker.java`](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L91),
[`NvCameraCMDClientLoginReq.java`](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/req/NvCameraCMDClientLoginReq.java#L30)
et [`AbsNvIoConnector.java`](./decompiled/sources/com/netviewtech/client/connection/camera/AbsNvIoConnector.java#L114).

Le chemin classique envoie `Q=1,V=1`; `P` contient la clé locale, un entier
signé obtenu depuis un `Random` initialisé à partir de l'heure, l'utilisateur
local et l'hôte/port annoncés. L'entier aléatoire n'est pas démontré comme
challenge : aucun échange défi-réponse qui le réutilise n'a été trouvé. Le
connecteur examiné est TCP sans chiffrement applicatif apparent et le flag de
l'enveloppe sortante est nul. Les logs Android associés au login peuvent
exposer des données sensibles; ils ne doivent pas être activés ou reproduits
dans un rapport.

Une classe `NvCameraCMDClientLoginReqV2` ajoute des métadonnées client, mais
aucun appel applicatif à son constructeur n'a été trouvé dans les sources
décompilées. Elle ne prouve donc pas l'envoi d'un login V2 par la branche LAN.

### Réponse : décodage → dispatch → transition

Le décodeur TCP remet les paquets de type commande à
`NvCameraCMDPacketFactory.decodePacket`; l'action `A=1` est décodée selon
`V=1` ou `V=2`, puis traitée par
`NvCameraServiceTpl.handleCameraCommandUnit`. `BasicCMDUnitACK` initialise
`C` à une valeur succès et ne la remplace que si le champ existe. Le
gestionnaire LAN étudié ne vérifie pas explicitement `C` avant de marquer le
login réussi et de poursuivre vers la sélection LIVE.

L'ACK V2 lit deux chaînes décrites comme aléatoire et MD5
([`NvCameraCMDLoginToCameraAckV2.java`](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/ack/NvCameraCMDLoginToCameraAckV2.java#L18)).
Aucune comparaison, validation cryptographique ni utilisation ultérieure de
ces valeurs n'a été repérée dans les sources examinées. Ce constat ne permet
pas de conclure à leur inutilité dans le firmware ou dans d'autres versions.

Go demande un champ `C` entier explicite et ne poursuit qu'avec `C=0`. C'est
une différence volontairement plus stricte, sans défaut démontré : ne pas
supprimer cette validation au seul motif que l'APK décompilé accepte un ACK
sans `C`.

## 3. LIVE, heartbeat et fermeture

### LIVE

Après login, `NvCameraServiceTpl.doSelectChannelAndReadPlugins` prépare `Q=7`
puis `Q=3` dans un même tableau de commandes. `Q=3,V=1` encode les types de
canaux; V2 encode les couples type/niveau, la tâche et l'intervalle. La fabrique
décode les ACK selon la version reçue. Les classes ACK V1/V2 ont des parents
inversés, mais leur parseur remplace ensuite la version par celle de la
réponse; cette anomalie de hiérarchie ne démontre pas à elle seule une erreur
de traitement.

Go envoie `Q=3,V=2`, vidéo seule ou audio/vidéo selon les capacités annoncées,
puis attend un ACK avec un résultat explicite. Go n'envoie pas le réglage
`Q=7` : le code Android le nomme `audioOn`, mais son effet et sa persistance
sur l'appareil ne sont pas établis. Dans les deux sessions réelles, l'absence
de Q7 n'a pas empêché la sélection LIVE; pendant le talkback, l'ACK A/V était
présent et Go a émis les trames. Q7 n'était pas requis pour ces opérations
côté client sur cette caméra, sans conclusion pour d'autres modèles.

### Heartbeat, timers et reconnexion

`NvCameraCMDHeartBeatReq` construit `Q=15,V=1,P=[]` et une classe d'ACK
existe. Dans le dispatch LAN classique, un heartbeat reçu est journalisé;
aucun envoi périodique LAN ni ACK sortant correspondant n'a été trouvé.
L'envoi périodique retrouvé appartient à Agora et est reprogrammé à 10 s
([`AgoraCameraService.java`](./decompiled/sources/com/netviewtech/client/service/camera/AgoraCameraService.java#L480)).

Le connecteur LAN utilise un timeout de lecture de 30 s
([`NvIoConnectorFactory.java`](./decompiled/sources/com/netviewtech/client/connection/camera/NvIoConnectorFactory.java#L39)).
Une expiration peut entraîner le chemin d'échec/rechoix de transport; les
temporisations de `TCP_TRAN_RING` ne doivent pas lui être assimilées. Dans Go,
l'absence de vidéo déclenche une interruption après 3 s, ou 5 s avant la
première image; le superviseur retente séquentiellement. Ce timeout Go est une
politique du pont, pas une valeur du protocole.

`NvCameraCMDCloseChannelReq` construit `Q=13,V=2`, tandis que la fabrique de
réponses étudiée ne référence Q13 qu'en V1. Aucun appel d'émission LAN de Q13
n'a été trouvé dans le chemin d'arrêt : `stop` ferme le connecteur. La
différence de version est un indice statique, pas une preuve de tentative de
fermeture échouée. Go ferme aussi la socket sans envoyer Q13; aucune correction
n'est démontrée.

## 4. Talkback et format audio

### Chemin Android classique

Après sélection d'un canal audio, `handleCameraSelectedChannels` envoie
`Q=5,V=1,P=[3]` (bidirectionnel), sans attendre explicitement son ACK.
`A=5` est décodé par la fabrique, mais le gestionnaire commun l'observe comme
une commande inconnue. La capture passe par `AudioRecord`, produit des blocs
de 2048 octets, puis `NVAudioHandlerV2.onChunkRec` appelle le chemin
d'encodage AAC. Le callback construit une trame média type 201.

La trame d'émission comporte `0x20`, puis les 32 bits de poids faible de
`System.currentTimeMillis()` en little-endian, puis les octets AAC. Les
références sont `AndroidAECVoiceService.initAndStartRecord`,
`NVAudioHandlerV2.onChunkRec`, `NvCameraMediaFrame` et
`NvCameraServiceTpl` (voir les chemins correspondants dans le rapport de
phase 40 et les sources décompilées).

### Encodeur natif et limites

Dans `libmobileffmpeg.so` ARM64, `nvAACEncInit` se trouve à l'adresse virtuelle
`0x18a4b0`; la séquence autour de `0x18a538–0x18a540` référence `libfdk_aac`.
Le wrapper configure le profil AAC-LC (notamment autour de `0x18a810–0x18a820`
et `0x4cdb40–0x4cdb5c`). La branche de transmux choisit ADTS ou LATM selon les
options global-header/LATM autour de `0x4cde88–0x4cdeb4`. Ces chemins statiques
ne révèlent pas les options réellement passées ni le format effectivement
émis. L'acceptation et la restitution audible par une Vigil restent à prouver.

Go envoie des trames AAC/ADTS préparées à l'avance en mode talkback
expérimental; ce n'est pas une reproduction démontrée du pipeline microphone
Android. Les observations historiques de phase 39 n'ont pas établi une
restitution audible.

### CameraII n'est pas le même framing

CameraII utilise un login distinct de type 14, un ACK de type 15 qui fournit
une session/endpoint, puis une seconde socket TCP préfixée par l'identifiant
de session. Les enveloppes internes type 41 ont un type sur un octet et une
longueur 16 bits big-endian. L'audio CameraII a un en-tête interne de 13 octets
et deux PTS big-endian; son compteur de trame interne ne correspond pas au
champ séquence de l'en-tête externe. Ces faits ne doivent pas être transposés
à la branche LAN classique.

## 5. Observations réelles expurgées

Les phases historiques 3, 37 et 39 ne contiennent que des résumés : succès
login/LIVE et H.264, audio AAC-LC mono 8 kHz, puis un essai talkback sans
tonalité audible confirmée. Aucun PCAP n'a été créé pendant la phase 41.
L'observateur a analysé les octets au fil de l'eau, ne conservant que les
champs d'en-tête, les nombres Q/A/V/C, la forme des P sans leurs valeurs
sensibles, les paramètres audio non identifiants, les types, tailles et PTS
média. Les contenus vidéo et audio ont été immédiatement rejetés par un sink
mémoire; aucun RTSP ou MediaMTX n'a été utilisé par les tests.

Avant chaque session, `ss` ne montrait aucune connexion `ESTABLISHED` vers la
caméra et le service Swarm `netvue_netvue-bridge` était à `0/0`. Après la
première session, seul un socket TCP `TIME-WAIT` est apparu; le contrôle avant
talkback n'a trouvé aucune connexion établie. Après le talkback, aucun socket
vers la caméra ne restait affiché. La stack n'a pas été modifiée ni redémarrée.

### Session 1 — contrôle vidéo, 25 secondes maximum

1. Découverte ciblée UDP : requête commande Q8/V2, P vide, longueur de corps
   22; réponse commande A8/V2/C0, corps de 197 octets. Les deux en-têtes
   externes avaient séquence=0, CRC=0 et flags=0. La réponse annonçait
   l'endpoint TCP 5555 et la capacité audio; la clé n'a pas été affichée.
2. Login TCP : requête Q1/V1, corps de 85 octets; P observé uniquement comme
   forme `[string,number,string,string,number]`. ACK A1/V1/C0, corps de 28
   octets. Séquence, CRC et flags externes étaient nuls dans les deux sens.
3. LIVE : une requête Q3/V2 vidéo seule, corps de 55 octets, puis deux ACK
   A3/V1/C0 de 68 et 70 octets. Le deuxième ACK est arrivé après le début des
   médias; sa cause ou sa corrélation restent inconnues.
4. Réception : 206 paquets média de type vidéo ont traversé le parseur Go et
   été remis au sink qui les a rejetés. Le corps allait de 465 à 67 740 octets;
   le flag média valait `0x02` dans 189 paquets et `0x03` dans 17. Les champs
   séquence/CRC externes sont restés nuls pour les 206 paquets. Les PTS
   observés allaient de 0 à 13 546 avec une transition non croissante. Le
   timestamp initial/final ne permet pas, à lui seul, d'établir la base de
   temps ou la cause de la transition.
5. Aucun Q15 ni autre commande de heartbeat n'a été observé pendant cette
   fenêtre de 25 secondes. La session s'est terminée par fermeture du client;
   aucun Q13 n'a été envoyé par Go.

Le sink temporaire n'a pas exercé `videoPublisher.Push` ni la publication RTSP.
Le code Go passe le PTS à `timestampClock.update`, qui convertit collision,
retour d'horloge ou écart excessif en progression minimale d'une milliseconde;
les tests `TestTimestampClockRecoversCollisionsAndResets` et
`TestTimestampClockPreservesNaturalWrapInRTPDomain` couvrent ces cas
synthétiques. L'observation ne fournit pas l'horodatage exact de la transition
ni une sortie RTP permettant de confirmer ce comportement en direct.

### Session 2 — talkback synthétique, 30 secondes maximum

Le ton a été généré localement : sinusoïde 700 Hz, 8 kHz mono, 0,70 s et
amplitude d'entrée 3 % du plein-échelle. Après encodage AAC-LC ADTS, le
décodeur local a mesuré un pic de 0,0362; le cadrage contenait sept unités AAC
et durait 0,896 s (délai/padding d'encodeur inclus). Le fichier contenait
uniquement ce signal synthétique et a été supprimé après l'essai.

1. Découverte, login et ACK ont de nouveau réussi; la requête login avait un
   corps de 84 octets, ACK A1/V1/C0 de 28 octets.
2. Q3/V2 a demandé vidéo et audio (corps de 61 octets). ACK A3/V1/C0 de 87
   octets; les paramètres audio décodés par Go étaient `[8000,1,1]`.
3. Go a envoyé Q5/V1/P=[3] (corps de 23 octets). Aucun A5 n'a été observé en
   réponse pendant les 3,53 s de la session; l'absence d'ACK ne prouve pas à
   elle seule un refus.
4. Après le premier média vidéo, Go a écrit sept paquets type 201 audio. Les
   en-têtes ADTS observés indiquaient AAC-LC, index de fréquence 11 (8 kHz),
   mono; les longueurs ADTS étaient 370, 333, 253, 225, 235, 226 et 435
   octets. PTS émis : 3 955 116 856 à 3 955 117 628, sans régression; seq/CRC
   externes nuls et flag média nul.
5. Pendant la même session, la caméra a envoyé 24 paquets vidéo (corps 1 264
   à 13 641 octets, PTS 0 à 2 766) et 22 paquets média type audio (corps 18 à
   561 octets, PTS 111 à 2 799). La charge utile reçue n'a pas été conservée,
   analysée en codec ou écoutée. Ces paquets ne prouvent donc ni boucle de
   retour de la tonalité ni restitution au haut-parleur.

Les limites de l'observation sont importantes : aucun contenu média n'a été
enregistré/décodé, aucun PCAP n'existe, les PTS entrants n'ont pas été
corrélés à une horloge murale, et les en-têtes non nuls n'ont pas été testés.
Les sessions prouvent uniquement les séquences et champs expurgés rapportés
pour cette caméra et cette période.

## 6. Comparaison Go et décisions

| Comportement | APK statique | Go actuel | Décision |
|---|---|---|---|
| Séquence/CRC externes | Constructeur classique nul; pas de validation établie. | Émis à zéro, non validés à la lecture. | Ne pas inventer de calcul ou d'incrément. |
| ACK login sans `C` | L'objet peut garder le succès par défaut; handler sans test explicite. | Rejeté faute de résultat entier explicite. | Conserver la validation stricte. |
| `Q=7 audioOn` | Envoyé avec Q3 par la branche Android étudiée. | Absent. | Ne pas modifier une configuration d'effet inconnu. |
| Heartbeat LAN | Aucun envoi périodique retrouvé. | Aucun heartbeat applicatif. | Pas de divergence démontrée. |
| Fermeture Q13 | Constructeur V2, pas d'appel LAN trouvé; l'arrêt ferme la socket. | Fermeture de socket, sans Q13. | Pas de divergence démontrée. |
| PTS vidéo | Une transition non croissante sur 206 paquets de contrôle. | `timestampClock` force une progression d'1 ms pour doublon/retour/écart excessif. | Aucun échec RTP observé; publication réelle non testée par le sink. |
| Talkback | `Q=5,P=[3]`, AAC natif, horodatage millisecondes tronqué à 32 bits. | Mode expérimental ADTS, émis avec PTS en millisecondes. | Sept trames ADTS écrites; pas d'ACK A5 observé; restitution physique inconnue. |

Les échanges réels confirment les choix d'en-tête Go (séquence/CRC/flags à
zéro), l'acceptation d'ACK login/LIVE V1 avec C=0, les paramètres audio
`[8000,1,1]` et l'émission des sept trames AAC synthétiques sans ACK A5 observé.
La requête LIVE vidéo seule a produit deux ACK A3; aucune erreur Go n'en a
résulté. Aucun défaut Go concret n'est établi, donc aucun changement
fonctionnel ni test de régression matériel n'a été ajouté. `go test
./internal/netvue` a réussi avec le collecteur temporaire; celui-ci a été
supprimé après les sessions.

## Inconnues restantes

- Algorithme, couverture et éventuelle validation caméra du CRC externe.
- Utilité de la séquence externe sur chaque firmware et son éventuelle
  relation avec les compteurs internes CameraII.
- Utilisation par la caméra de l'entier aléatoire du login et des champs
  aléatoire/MD5 de l'ACK V2.
- Acceptation des ACK sans `C`, envoi éventuel de Q13 par d'autres branches,
  et effet réel de Q7.
- Options AAC effectivement passées, format d'émission réel et restitution
  physique du talkback.
- Sémantique complète de la réponse A3 supplémentaire, acceptation sonore
  physique, format des trames audio reçues pendant le talkback, valeur de PTS
  pour tous les modèles et comportement des champs externes non nuls.
