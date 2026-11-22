# Protocole LAN Netvue : faits et limites

Synthèse des preuves du code Android analysé et des tests
autorisés antérieurs. Ce n'est ni une spécification fabricant ni une garantie
de compatibilité. Le code actif est `internal/netvue/`; les analyses
historiques locales peuvent rester dans `findings/` (ignoré par Git).

## Confirmé par les preuves disponibles

- Le code Android analysé contient une découverte UDP broadcast ; une
  requête unicast et sa réponse ont aussi été testées.
- La recherche utilisateur Go envoie une requête V2 UDP/36868 à l'adresse
  broadcast du sous-réseau de l'interface configurée, la renvoie toutes les
  500 ms et collecte pendant deux secondes les réponses compatibles avec la
  session Go, dédupliquées par adresse. La découverte unicast d'une session
  renvoie aussi sa requête. Choix Go : en production, une caméra derrière un
  répéteur Wi-Fi a manqué 1 recherche sur 17 avec une requête unique. L'adresse
  annoncée doit correspondre à l'émetteur ; les autres paquets sont ignorés.
  Une recherche réelle autorisée a trouvé la caméra 2 depuis le serveur.
  Ce filtrage n'établit pas à lui seul la compatibilité vidéo du modèle.
- Les réponses de découverte sont encadrées et contiennent des unités JSON.
  Une réponse observée sur la caméra 2 annonçait son adresse source, un
  endpoint TCP et des informations locales d'authentification.
- Le cadrage historique comporte un en-tête binaire fixe de 16 octets, une
  longueur de corps bornée, un indicateur et un type de paquet. Les commandes
  sont des unités JSON ; les médias utilisent un type distinct.
- L'application possède plusieurs chemins de transport/authentification :
  ne pas confondre la branche LAN classique et CameraII.
- Lors d'essais antérieurs explicitement autorisés sur la caméra 2, le login
  et la demande LIVE ont réussi ; du H.264 a été reçu et une publication
  RTSP/TCP décodée.
- La caméra 2 a répondu à une demande LIVE version 2 avec une confirmation
  `V=1` et un résultat explicite égal à zéro. Le code accepte `V=1` et `V=2`,
  mais seule la réponse `V=1` est documentée comme observée sur une caméra.
  Le code Android analysé utilise lui aussi `V` pour choisir un décodeur
  d’ACK version 1 ou 2 sur le chemin LAN classique. Il mappe une version absente
  ou inconnue vers un paquet non reconnu. Conserver les deux valeurs admises
  et rejeter les versions absentes ou inconnues ; les tests hors ligne couvrent
  ces cas. Cette observation APK ne décrit pas la branche CameraII.

## Enveloppe binaire et échanges LAN classiques

Les éléments ci-dessous décrivent la branche LAN classique de l'APK, pas
CameraII. Ils rapprochent le code statique, les paquets fabriqués des tests Go
et l'implémentation actuelle; ils ne constituent pas une spécification
fabricant.

### Enveloppe

L'APK sérialise les commandes en paquets de type `200` et les médias en
paquets de type `201`. Sur TCP, le Go lit les 16 octets d'en-tête puis
exactement le nombre d'octets annoncé; un datagramme UDP doit contenir un
paquet complet.

| Offset | Taille | Champ | Encodage et constat |
|---:|---:|---|---|
| 0 | 4 | Longueur du corps | Entier little-endian, sans l'en-tête. |
| 4 | 4 | Séquence | Entier little-endian dans l'APK; signification/usage LAN non établis. Go émet zéro. |
| 8 | 4 | CRC | Champ 32 bits; algorithme et couverture non établis. Go émet zéro. |
| 12 | 2 | Flags | Little-endian; valeur nulle dans les paquets décrits. Go rejette une valeur non nulle. |
| 14 | 2 | Type | Little-endian; `200` commande, `201` média. |
| 16 | variable | Corps | Tableau JSON UTF-8 pour une commande; corps média ci-dessous pour type 201. |

Go borne le corps à moins de 1 Mio. L'APK possède une borne de cette taille
dans un parseur de paquets, mais cela ne démontre pas que son décodeur TCP
l'applique aussi. Go vérifie longueur et flags, mais pas les champs séquence
et CRC : ceux-ci sont émis à zéro, et leur sémantique n'est pas établie.

### Découverte et authentification

| Étape | Message | Comportement établi |
|---|---|---|
| Découverte | UDP/36868, requête `Q=8`, `V=2`, `P=[]`; réponse `A=8`, `V=2`. | Le Go vérifie l'émetteur et la concordance de l'adresse annoncée avec la cible. `P[0]` (série), `P[1]` (firmware) et les propriétés de `P[5]` sont conservés comme metadata optionnelles; `P[2]` est l'hôte, `P[3]` le port TCP, `P[4]` la clé locale. `C` peut manquer; s'il est présent, Go exige un entier nul. |
| Login LAN classique | TCP, commande `Q=1`, `V=1`; `P` contient clé locale, entier aléatoire 32 bits, nom d'utilisateur local, hôte et port annoncés. | L'entier est présent dans la requête Android; sa fonction côté caméra n'est pas connue. Le code Android utilise un login TCP sans chiffrement applicatif apparent. Go garde la clé en mémoire et ne la journalise pas. |
| ACK login | `A=1`, `V=1` ou `V=2`, résultat `C`. | Les objets ACK Android peuvent garder une valeur « succès » par défaut si `C` manque; aucune validation explicite de `C` n'est visible dans le gestionnaire LAN étudié. Go exige un résultat entier explicite et ne poursuit qu'avec `C=0`. Il s'agit d'une validation stricte volontaire, pas d'une reproduction exacte de l'APK. |

CameraII emploie une autre authentification, un autre transport et des données
de session distinctes; ces détails ne sont pas représentés par le client Go.

L'API manager ajoute `connection_port` et `metadata` à la réponse discovery;
le statut des caméras fournit les mêmes champs après une découverte de session.
Cette information vient de la réponse UDP déjà nécessaire à la connexion, sans
requête supplémentaire ni accès cloud. Une recherche LAN explicite rafraîchit
le cache. Série et firmware conservent leur dernière valeur non vide; les
capacités, le SSID et le signal reflètent la dernière réponse valide et sont
effacés si cette réponse omet ces champs. Un booléen ou niveau reçu
explicitement, même `false` ou `0`, est conservé.
L'API expose uniquement la série, le firmware, les capacités audio/flip/lumière/
carte SD, le SSID et le niveau Wi-Fi brut. Elle n'expose ni clé locale,
`offlineToken`, `ability` ni `clientVersion`; le SSID n'est jamais journalisé.

### LIVE, sélection et direction audio

| Étape | Message | Champs et traitement |
|---|---|---|
| Sélection LIVE | Requête `Q=3`, `V=2`, `P=[1, canaux, [début, fin]]`. | `1` est la tâche LIVE. Go choisit vidéo `[1,0]`, et ajoute audio `[2,0]` si la découverte annonce cette capacité. L'intervalle Go est en secondes Unix, de maintenant à un an plus tard. |
| Confirmation LIVE | Réponse `A=3`, `V=1` ou `V=2`, résultat `C`, canaux sous `P`. | Une entrée de canal est `[canal, tâche, typeMédia, paramètres]`; type vidéo `1`, audio `2`. Les paramètres audio sont `[samplerate, samplesize, channels]`; l'unité de `samplesize` n'est pas établie. Go accepte V1/V2, exige `C=0`, et rejette version ou résultat absents/invalides. |
| Direction audio | `Q=5`, `V=1`, `P=[direction]`. | Valeurs dans le code Android : `0` arrêt, `1` caméra vers client, `2` client vers caméra, `3` bidirectionnel. Go demande `1` pour recevoir l'audio et `3` dans son mode expérimental talkback. |
| Réglage audio APK | `Q=7`, `V=1`, `P=[[0,1,[true]]]` avant la sélection LIVE dans le tableau de commandes Android. | Le code l'associe à `audioOn`. Son effet matériel et sa persistance sont inconnus; Go ne l'envoie pas car cela pourrait modifier une configuration de la caméra. |

Après l'ACK de sélection audio, le chemin Android de talkback envoie
`Q=5,V=1,P=[3]` sans attendre explicitement son ACK. Pour la réception audio,
Go envoie `P=[1]`; cette direction caméra-vers-client diffère du réglage
bidirectionnel utilisé par le talkback.

### Paquets média

Le corps de type `201` commence par un en-tête média de cinq octets :

| Offset dans le corps | Taille | Champ | Interprétation |
|---:|---:|---|---|
| 0 | 1 | Type/indicateurs | Demi-octet haut : type (`1` vidéo, `2` audio). Bit `0x01` : indicateur d'image clé dans le parseur Android. Bit `0x02` : OSD pour vidéo ou PCM pour audio. |
| 1 | 4 | PTS | Entier little-endian 32 bits; cohérent avec des millisecondes dans le code et les observations disponibles, sans garantie tous modèles. |
| 5 | variable | Charge utile | Vidéo LAN classique traitée comme H.264; audio avec bit PCM comme PCM, sinon AAC dans le code étudié. |

Go distingue audio/vidéo via le demi-octet haut. Pour la publication vidéo, il
valide Annex B, NAL, SPS/PPS et IDR; l'image-clé est déterminée par le NAL IDR,
pas par le seul bit protocolaire. Pour le PCM, il publie du L16 16 bits et
convertit l'ordre des octets; l'ordre PCM de tous les firmwares n'est pas
confirmé. Les réponses et codecs peuvent varier selon le modèle.

### Heartbeat, fin et reconnexion

- Dans le connecteur Android LAN classique, le délai d'inactivité de lecture
  est de 30 secondes. Le traitement de `Q=15` est visible à la réception, mais
  aucun envoi périodique LAN n'est démontré; l'envoi périodique trouvé
  concerne Agora. À l'arrêt, l'application ferme le connecteur. La requête de
  fermeture `Q=13` est construite en version 2, alors que le dispatch de
  réponse examiné ne référence que la version 1. Cette incohérence DEX ne
  prouve pas qu'une fermeture protocolaire soit utilisée.
- Go n'envoie pas de heartbeat applicatif. Il traite l'absence de vidéo
  pendant 30 secondes comme une interruption (15 secondes pour la première
  image), ferme la session et la remet au superviseur. `RunContinuous` réessaie
  séquentiellement avec un délai exponentiel de 2 à 30 secondes et une
  variation aléatoire ; l'attente repart du début après 5 minutes de session
  stable. Après une authentification réussie, une reconnexion tente d'abord
  l'endpoint et la clé gardés en mémoire avant de redécouvrir. L'annulation
  ferme les sockets et interrompt les lectures/écritures bloquées.
- Le choix Android entre LAN, Agora et relais ainsi que les reconnexions
  dépendent de l'état de login, du transport et du compteur d'échecs; les délais
  de `TCP_TRAN_RING` ne doivent pas être généralisés au LAN classique. La
  reconnexion durable multi-modèles n'est pas établie par les tests hors ligne.
- Les classes ACK LIVE V1/V2 ont des parents inversés, mais leur lecture
  sélectionne la version reçue. Le dispatch Q13 montre aussi une différence
  version requête/réponse; ces anomalies statiques n'établissent pas un échec
  effectif sur les caméras.

## Probable ou limité

- Les unités média ont été interprétées comme H.264 Annex B avec un timestamp
  32 bits en millisecondes. C'est cohérent avec les observations et les tests,
  sans être une spécification universelle.
- Réponses et chemin effectif peuvent dépendre du modèle, firmware, capacités,
  endpoint dynamique et choix de service de l'application.
- Le port 5555 a été annoncé et utilisé lors d'essais historiques, mais n'est
  pas un port universel.

## Audio LAN — analyse statique de l'APK

Les éléments ci-dessous proviennent principalement du code Android décompilé,
pas d'une capture réseau. Ils décrivent le chemin LAN classique de l'application
et ne garantissent pas la compatibilité des modèles Vigil.

- Les trames média audio ont un en-tête de 5 octets : type dans le demi-octet
  supérieur du premier octet, timestamp little-endian 32 bits en millisecondes,
  puis charge utile. Le type 2 désigne l'audio ; le bit `0x02` indique le PCM,
  sinon le chemin analysé traite la charge utile comme AAC.
- La sélection V2 demande la vidéo avec `[1, 0]` et peut ajouter l'audio avec
  `[2, 0]`. La réponse audio expose un identifiant de canal, le type média et
  les paramètres `samplerate`, `samplesize` et `channels`. L'unité de
  `samplesize` dans cette réponse n'est pas établie : le lecteur Android
  utilise la fréquence et les canaux, mais pas ce champ pour configurer le
  son. Il ne faut pas l'interpréter comme une profondeur en bits. L'APK
  contient des formats AAC à 8/16 kHz et PCMU à 8 kHz ; leur présence dans
  l'application ne prouve pas qu'une caméra donnée les annonce ou les utilise.
- La commande `Q=5` utilise les directions `OFF=0`, `DEVICE2CLIENT=1`,
  `CLIENT2DEVICE=2` et `TWOWAY=3`. Le pont demande normalement
  `DEVICE2CLIENT` ; son mode expérimental `talk` demande `TWOWAY` et envoie un
  fichier AAC-LC/ADTS. L'APK utilise statiquement AAC-LC; le choix effectif
  ADTS/LATM reste inconnu tant que les options exécutées et le flux émis ne
  sont pas observés.
- Dans le code décompilé, `AndroidAECVoiceService.initAndStartRecord` démarre
  la capture, `NVAudioHandlerV2.onChunkRec` traite les blocs micro en AAC et le
  chemin les encapsule dans `NvCameraDataPacket` pour le transport LAN classique.
  Il demande `Q=5`, `V=1`, `P=[3]` (direction `TWOWAY`) après la sélection
  audio, sans attendre explicitement un ACK. L'enveloppe type 201 contient
  `0x20`, un PTS 32 bits little-endian issu de `System.currentTimeMillis()`,
  puis les octets retournés par l'encodeur natif. Dans `libmobileffmpeg.so`
  ARM64 de l'APK 5.27.0, le symbole `nvAACEncInit` (`0x18a4b0`) et une
  référence à `libfdk_aac` sont vérifiés; l'analyse native confirme le profil
  AAC-LC et les branches conditionnelles ADTS/LATM/global-header du wrapper
  FDK (`0x4cdeb0`). Le réglage effectif de ces options et le format réellement
  émis restent inconnus. L'acceptation et la restitution par une Vigil ne
  sont pas confirmées.
  CameraII utilise une enveloppe différente et ne doit pas servir de preuve
  pour le LAN classique.
- Le pont sélectionne l'audio seulement si la découverte annonce la capacité
  correspondante et retombe sur une sélection vidéo seule si l'appareil rejette
  la sélection A/V. Il publie AAC/ADTS et PCM 16 bits en RTP avec l'horloge
  audio dérivée de l'origine temporelle vidéo. Le lecteur Android standard
  utilise aussi PCM 16 bits indépendamment de `samplesize` ; l'ordre des octets
  PCM sur cette caméra reste non vérifié. Le décodeur PCMU de l'application
  n'est pas implémenté par le pont.
- La caméra 2 a répondu `[8000, 1, 1]` pour son canal audio. Le rejet initial
  venait de l'interprétation incorrecte de `1` comme une profondeur en bits.
  Une trame sans indicateur PCM contenait de l'ADTS ; avec la correction,
  FFmpeg a décodé H.264 et AAC-LC mono 8 kHz en parallèle. La synchronisation
  perceptuelle et la stabilité prolongée restent à mesurer.

L'analyse statique et les tests synthétiques du pont sont consignés dans
[le rapport de phase 37](../findings/phase37-audio-ha.md). L'encodage exact des
trames reçu par chaque modèle, la synchronisation réelle et l'audio bidirectionnel
restent à valider séparément.

## Preuves DEX et ARM64 complémentaires

L'analyse complémentaire du DEX et des bibliothèques ARM64 précise ce que le
code construit et traite, sans lever les inconnues côté firmware :

- `NetviewPacketHead` place la séquence à l'offset 4 et le CRC à l'offset 8.
  `NvProtocolPacket.withEmptyHead` part de 16 octets nuls; le chemin Java
  classique renseigne la longueur, les flags et le type, sans calcul visible de
  séquence ou de CRC. Le décodeur TCP classique consomme la longueur et le type,
  mais ne vérifie pas ces deux champs. L'examen des références DEX n'a trouvé
  que des mises à zéro dans les classes NAT CameraII; aucun incrément ni appel
  aux getters n'a été établi. La portée du CRC, son algorithme et l'usage de la
  séquence par le firmware restent inconnus.
- Le chemin de login classique construit `Q=1,V=1` depuis
  `NvCameraLANService.doLogin`, via `NvCameraRequestMaker.makeLoginUnit` et
  `NvCameraCMDClientLoginReq.writeToTarget`, puis l'encodeur et le connecteur
  TCP. L'entier aléatoire provient d'un générateur initialisé avec l'heure ;
  aucun défi-réponse qui l'utilise n'a été trouvé. L'ACK V2 expose deux chaînes
  nommées aléatoire et MD5, mais aucune utilisation ultérieure n'a été établie.
  Le gestionnaire Android ne vérifie pas explicitement `C` avant le succès;
  Go conserve volontairement l'exigence plus stricte d'un résultat explicite.
- L'APK construit une classe de heartbeat `Q=15,V=1`, mais aucun envoi
  périodique LAN classique n'a été trouvé; le chemin LAN journalise seulement
  un heartbeat reçu. Le timer de 10 s observé appartient à Agora. La classe de
  fermeture construit `Q=13,V=2`, mais aucun envoi LAN n'a été trouvé et le
  dispatch étudié ne connaît que Q13 V1. L'arrêt LAN ferme le connecteur.
- Q7 n'a pas été envoyé pendant ces deux sessions. Les réponses de sélection
  vidéo puis A/V ont tout de même été observées; lors du talkback, l'ACK A/V
  était présent et Go a pu émettre les trames. Q7 n'était donc pas nécessaire
  à ces opérations côté client sur cet exemplaire, sans établir son effet
  éventuel sur d'autres modèles.
- Pour le talkback classique, après l'ACK de sélection audio, le service
  demande `Q=5,V=1,P=[3]` puis envoie les trames sans attendre explicitement
  l'ACK de cette commande. La capture microphone est traitée par blocs de 2048
  octets, encodée par le chemin AAC natif puis encapsulée en type 201. Le
  premier octet média vaut `0x20`; les quatre suivants portent les 32 bits de
  poids faible de `System.currentTimeMillis()`, little-endian. L'APK ARM64
  référence FDK AAC/AAC-LC; la branche ADTS ou LATM dépend d'options dont
  l'état effectif n'est pas établi.
- CameraII suit un protocole distinct : login/ACK de session puis socket média
  avec identifiant de session et enveloppes internes. Sa séquence de trame
  interne n'est pas le champ séquence de l'en-tête externe; ces faits ne
  permettent pas d'inférer le comportement LAN classique.

Les appels, offsets, limites de recherche et comparaison Go sont détaillés
dans [le rapport de phase 41](../findings/phase41-detail-protocole-lan.md).

## Observation réelle autorisée — caméra 2

Deux sessions séquentielles ont été observées depuis le serveur Debian après
confirmation préalable de l'absence de connexion établie; le service de
production `netvue_netvue-bridge` était et reste à `0/0`. L'observateur
temporaire a décodé les en-têtes et métadonnées en mémoire, sans PCAP, contenu
JSON brut, charge utile audio/vidéo, publication RTSP ni modification de
service. La vidéo et l'audio reçus ont été transmis à un sink mémoire qui les
a immédiatement rejetés.

| Échange observé | Résultat et interprétation |
|---|---|
| Découverte UDP ciblée | Requête Q8/V2, `P=[]`, corps de 22 octets; réponse A8/V2, C=0, corps de 197 octets. L'appareil annonçait l'endpoint TCP 5555 et l'audio. Le port est propre à cette observation, pas universel. |
| Login TCP | Requête Q1/V1, corps de 85 octets (contrôle) puis 84 (talkback); ACK A1/V1/C=0, corps de 28 octets dans les deux sessions. Les valeurs de P, dont clé et nonce, ont été omises. |
| Sélection vidéo seule | Q3/V2, corps de 55 octets; deux réponses A3/V1/C=0 (corps de 68 et 70 octets) ont été observées pour une requête. La seconde est arrivée après le début du média; sa signification n'est pas établie. |
| Sélection A/V et direction talk | Q3/V2 avec deux canaux, corps de 61 octets; ACK A3/V1/C=0, corps de 87 octets. Les paramètres audio observés étaient `[8000,1,1]`. Le client a ensuite envoyé Q5/V1/P=[3], corps de 23 octets. Aucun ACK A5 n'a été observé pendant les 3,53 s de cette session. |
| Médias descendants | Contrôle de 25 s : 206 paquets vidéo type 201, corps de 465 à 67 740 octets; PTS initial/final 0/13 546, une transition non croissante. Talkback de 3,53 s : 24 paquets vidéo et 22 paquets média type audio; aucun contenu n'a été conservé. |
| Trames talkback émises | Sept paquets média audio type 201, avec ADTS AAC-LC mono 8 kHz (index de fréquence ADTS 11), longueurs ADTS 370, 333, 253, 225, 235, 226 et 435 octets. Le signal généré était une sinusoïde 700 Hz de 0,70 s à 3 % du plein-échelle; le cadrage AAC a duré 0,896 s. Cela confirme l'émission TCP, pas la restitution audible par le haut-parleur. |
| En-tête externe | Séquence et CRC étaient tous deux nuls sur les datagrammes, commandes et médias observés. Flags externes nuls. Cela confirme les valeurs émises/acceptées pendant ces sessions, pas le comportement avec des valeurs non nulles. Aucun Q15 n'est apparu pendant la fenêtre de contrôle de 25 s; la durée ne prouve rien au-delà. |

Go a mené les échanges jusqu'au LIVE et a transmis le signal talkback sans
erreur de protocole détectée. L'observation ne démontre ni l'acceptation
acoustique ni le son au haut-parleur. Les corps reçus n'ont pas été enregistrés
ou décodés indépendamment; la transition PTS non croissante doit être suivie,
mais une occurrence sans horodatage exact ni échec de publication ne suffit pas
à justifier une correction. Le sink de test n'a pas exercé la publication
RTSP; le normaliseur Go traite déjà collisions et retours d'horloge en avançant
d'un milliseconde, avec des tests synthétiques dédiés. Aucun défaut Go démontré
n'a donc été corrigé.

## Non vérifié

Le caractère universel de TCP/5555 (la caméra 2 a historiquement annoncé cet
endpoint et accepté une session); l'authentification sans cloud et le chemin
choisi pour tous les modèles; le protocole/codec de la caméra 1; le sens de
certains champs séquence/CRC et de `samplesize`; le transport audio de chaque
firmware; l'acceptation et la restitution physique du talkback; les fragments
génériques, B-frames et stabilité 24/7; le résultat d'un ACK audio `A=5` après
la demande Go `Q=5,P=[1]`; la fermeture effective `Q=13` malgré l'incohérence
de version observée.

## Garde-fous et sécurité

- Accepter la découverte seulement si source et endpoint correspondent à
  l'adresse ciblée (ou à l'émetteur sur le sous-réseau pour une recherche
  broadcast) ; borner longueurs, nombre de réponses et délais.
- Exiger un résultat entier explicite à chaque étape login/sélection LIVE ;
  rejeter `null` ainsi que les paquets incomplets ou inattendus. En découverte,
  le résultat reste facultatif, mais doit être un entier nul s'il est présent.
- Ne pas journaliser clé locale, identifiant, token, média ou trame brute.
- Le login TCP observé envoie la clé locale sans chiffrement applicatif
  apparent ; l'implémentation Go conserve ce comportement nécessaire à
  l'interopérabilité. La clé reste en mémoire et hors journaux, mais un
  observateur du LAN peut la voir. Sa réutilisabilité n'est pas établie.
  N'utiliser que sur un LAN de confiance ; ce transport ne protège pas le
  secret en transit.
- Les tests Go utilisent des réponses fabriquées ou un pair local.
