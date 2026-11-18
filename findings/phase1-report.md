# Rapport phase 1 — Netvue Vigil

## Résumé décisionnel

- **APK analysé :** Netvue **5.27.0** (versionCode **121868**), téléchargé de l’URL hébergée par Netvue. SHA-256 : `9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`.
- **Résultat principal :** le code contient des chemins live conditionnels LAN/TCP, relais TCP et Agora RTSA. AWS IoT/MQTT sert notamment à demander un ticket de lecture ; sa présence ne prouve pas qu’il transporte les images. H.264 et HEVC sont pris en charge par des décodeurs natifs, sans preuve du codec utilisé par les deux Vigil.
- **TCP 5555 :** aucun appel applicatif trouvé qui lie ce port à la vidéo ou à l’un de ces chemins. Des occurrences brutes de la suite `5555` dans des données binaires ne sont pas une preuve de protocole ou d’écoute sur ce port.
- **Prochaine expérience sûre (unique) :** relever la référence/révision exacte inscrite sur l’étiquette ou la documentation des caméras, sans les connecter ni ouvrir de compte. Cela permettra de confronter la génération aux branches de code identifiées.

## Périmètre et méthode

Consignes du dépôt, `docs/observations.md`, `docs/phase1-checklist.md` et le skill `.github/skills/netvue-apk-analysis/SKILL.md` lus. La première édition de ce rapport était documentaire et constatait l’absence de l’APK ; après la demande explicite de téléchargement depuis Netvue, l’APK a été récupéré et analysé statiquement. Aucune application n’a été exécutée, aucun appareil ni compte contacté, aucune API interrogée et aucune configuration réseau ou firmware modifié.

## Observé

### Sources et versions de l’application

- La [page Android Release de Netvue](https://resource.netvue.com/android/netvue/release.html) sépare l’application **Netvue** de **Netvue Next** et de **Birdfy**. La table « Netvue Android Release » répertorie Netvue **5.27.0_121868** (21 octobre 2025), puis des versions antérieures.
- L’historique officiel documente aussi **Netvue 5.24.9.99715** (30 novembre 2022) comme « Optimizing AWS IoT connections », **5.24.10_100362** (21 décembre 2022) comme ajoutant le type d’appareil Vigil 3, et **5.25.4_102525** (21 février 2023) comme optimisant la connexion appareil. Ce sont des notes de version, pas des preuves que ces méthodes ou services sont encore utilisés par la version récente ou par ces exemplaires.
- La fiche [Google Play de l’application Netvue](https://play.google.com/store/apps/details?id=com.netviewtech) indique NetvueTechnologies comme éditeur, décrit une plateforme de produits connectés cloud et affiche « Updated on Jan 22, 2026 ». La page ne révèle pas le numéro de version dans le contenu consulté. Cette date ne permet donc pas d’identifier la version installée ni de contredire le numéro de la table d’archive.
- Le site officiel de comparaison [Netvue Compare Cameras](https://www.netvue.com/blogs/netvue-blog/compare-netvue-cameras) associe GK7101 et H.264 à certaines caméras listées (Orb Cam, Orb Mini, Orb Pro). Cette page **ne confirme pas** que les deux Vigil du propriétaire ont ce SoC ou ce codec.
- L’organisation [Netvue sur GitHub](https://github.com/netvue) publie notamment un dépôt WebRTC dérivé du SDK Amazon Kinesis Video Streams WebRTC et d’autres composants vidéo. L’existence de ces dépôts montre une activité logicielle de l’éditeur, mais ne prouve pas qu’ils sont intégrés dans l’APK Netvue, ni utilisés par Vigil ou par le port 5555.

### État local et observations antérieures du propriétaire

- **Historique :** lors de la première vérification, `input/netvue.apk` était absent ; ce blocage est maintenant levé (voir la mise à jour d’analyse ci-dessous).
- `docs/observations.md` rapporte deux caméras Vigil, sans révision matérielle confirmée, sans carte microSD, et un scan antérieur où seul `5555/tcp` était ouvert ; `23/tcp` et `554/tcp` étaient fermés. Ce sont des observations rapportées du propriétaire, non reproduites ici.
- Le résultat `freeciv?` mentionné dans les observations est une étiquette incertaine d’identification de service, pas une identification du protocole.

## Évaluation documentaire initiale (avant l’APK)

- Un parcours applicatif impliquant une authentification et une connexion cloud est **plausible**, compte tenu de la description officielle de Netvue comme plateforme cloud. Les notes anciennes sur AWS IoT renforcent l’intérêt d’examiner l’APK. Elles ne démontrent ni une authentification caméra spécifique ni un chemin vidéo cloud/direct.
- Un transport local ou P2P reste **possible mais non établi**. La fermeture observée de RTSP standard sur 554 ne prouve pas l’absence de tout flux vidéo local ; l’ouverture de 5555 ne prouve pas qu’il transporte vidéo, utilise ADB, Freeciv, RTSP ou tout autre protocole.
- H.264, WebRTC, P2P, MQTT, STUN/TURN/ICE et AWS sont des pistes à chercher dans l’APK fourni. Une chaîne, une bibliothèque ou un dépôt isolé ne suffira pas à établir un usage effectif.

## Non vérifié / bloqueurs

- Version actuellement installée sur le téléphone du propriétaire et compatibilité de l’APK analysée avec la révision matérielle/firmware des Vigil.
- Chemin effectivement choisi par les caméras : les branches dépendent des capacités, de la portée annoncée et des tickets fournis à l’application.
- Rôle de `5555/tcp` : le binaire ne fournit pas de lien statique concluant avec ce port, et aucun trafic n’a été observé.
- Utilisabilité du chemin LAN depuis Home Assistant : le code client existe, mais son protocole propriétaire, les prérequis d’authentification et l’interopérabilité ne sont pas validés.
- Aucun flux réseau n’a été observé ; le serveur Debian ne verrait pas automatiquement l’unicast entre téléphone, caméras et point d’accès.

## Questions restantes

1. Quelle révision matérielle et quel firmware portent les caméras ?
2. Le téléphone du propriétaire utilise-t-il l’application « Netvue » legacy, et quelle version y est installée ?
3. Le chemin live view de cette version est-il direct, relayé, ou hybride ?
4. Le service associé à `5555/tcp` est-il lié au contrôle, au transport vidéo ou à un autre usage ?

La mise à jour ci-dessous remplace le blocage initial par les résultats statiques obtenus. Les artefacts APK, inventaire, outils et sources décompilées sont sous des chemins ignorés par Git ; le rapport n’inclut aucun identifiant, jeton, clé ou donnée de caméra.

## Mise à jour — téléchargement et analyse statique (16 novembre 2026)

### Provenance et reproductibilité

- La page [Android Release Netvue](https://resource.netvue.com/android/netvue/release.html) répertorie **Netvue_5.27.0_121868.apk** ; l’URL directe est [resource.netvue.com/release/Netvue_5.27.0_121868.apk](https://resource.netvue.com/release/Netvue_5.27.0_121868.apk).
- Vérification de l’URL par requête HTTPS HEAD : `200 OK`, type `application/vnd.android.package-archive`, longueur annoncée **152 491 597 octets**, dernière modification serveur **21 octobre 2025**. Le téléchargement local a cette taille et la signature ZIP `PK`. Aucun checksum éditeur pour l’APK n’a été trouvé/vérifié ; le SHA-256 ci-dessus est l’empreinte locale de référence.
- Le manifeste décodé confirme `package="com.netviewtech"`, `versionName="5.27.0"`, `versionCode="121868"` : [AndroidManifest.xml](./decompiled/resources/AndroidManifest.xml#L2).
- [Inventaire JSON](./apk-inventory.json) produit par `scripts/apk_inventory.py` : **5 457** entrées, **9 DEX**, bibliothèques natives pour `arm64-v8a` et `armeabi-v7a`. Il avertit que les domaines extraits sont des indices statiques et que son échantillonnage est limité à 80 entrées de 4 MiB.
- Python 3.13.1 et Java 25 étaient déjà disponibles. JADX **1.5.6** a été téléchargé depuis la publication officielle [skylot/jadx](https://github.com/skylot/jadx/releases/tag/v1.5.6), puis son archive vérifiée avec le SHA-256 publié `545ea2be9c242511bc145755cf4bda2485ade42966e096f8b4d3da2a230e8974`. Aucun outil n’a été installé au niveau système.
- La commande de décompilation `jadx -d findings/decompiled input/netvue.apk` a généré les dossiers `sources/` et `resources/`, mais JADX a terminé avec **155 erreurs** (code retour 1, progression jusqu’à 99 %). Les classes citées ci-dessous sont présentes et lisibles ; l’analyse n’est donc pas exhaustive. Les sources décompilées restent des artefacts privés, non reproduits dans ce rapport.

### Graphe observé du live view

1. **Entrée UI.** [LivePlayerActivity](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/player/live/LivePlayerActivity.java#L106) crée un `LivePlayerPresenter`. Sa méthode `doStartCameraPlaying` lit les préférences de qualité/méthode puis appelle `device.live(...)` ([LivePlayerPresenter.java](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/player/live/LivePlayerPresenter.java#L54)). Le présentateur prépare une configuration de tâche `LIVE` / `CLIENT_CALL` ([CameraPlayerPresenter.java](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/player/CameraPlayerPresenter.java#L232)).
2. **Choix du transport.** `NvCameraServiceCenter.start` distingue une branche `CameraII`, une branche LAN si `supportP2P()`, et la branche à relais. Pour un live compatible, le code peut choisir LAN ou `AGORA_RTSA` selon les capacités de l’appareil et la préférence de transport ([NvCameraServiceCenter.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L340)). `NVLocalDeviceNode.supportP2P()` dépend de l’état `LOCAL` ou `BOTH_CLOUD_LOCAL`; `canPlayWithRTSA()` dépend des indicateurs de support/ disponibilité ([NVLocalDeviceNode.java](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/NVLocalDeviceNode.java#L1037)). Ces propriétés ne sont pas connues pour les deux exemplaires du propriétaire.
3. **Voie cloud/MQTT et ticket.** `NvIoTClientManagerImpl.requestPlayTicket` attend un client IoT connecté et lui demande un ticket de lecture ([NvIoTClientManagerImpl.java](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTClientManagerImpl.java#L133)). `NvIoTClientImpl.getPlayTicket` construit une requête `NvIoTPlayRequest` avec type de fournisseur/version, puis l’envoie via le client MQTT ([NvIoTClientImpl.java](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTClientImpl.java#L161)). `NvIoTMQTTClient` crée un `AWSIotMqttManager`, le connecte avec un fournisseur STS et s’abonne aux topics construits par le client ([NvIoTMQTTClient.java](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTMQTTClient.java#L133)). Le chemin `NvIoTAuthManager.asyncRefreshAuthToken` demande ce play ticket et transforme la réponse en `NvCameraAuthResponse` ([NvIoTAuthManager.java](./decompiled/sources/com/netviewtech/client/auth/NvIoTAuthManager.java#L34)). **Cela établit un chemin de signalisation/authentification par MQTT pour les appareils qui utilisent ce gestionnaire ; cela ne démontre pas que la vidéo passe par MQTT.**
4. **Relais TCP.** `NvCameraRelayServiceTpl` demande une autorisation avec un fournisseur de lecture et les métadonnées de l’appareil, récupère un ticket, puis utilise l’hôte et le port contenus dans ce ticket pour créer un connecteur TCP ([NvCameraRelayServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraRelayServiceTpl.java#L130), [même fichier](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraRelayServiceTpl.java#L162)). Son `doLogin` envoie au relais une requête avec identifiant client et ticket ; les valeurs sensibles ne sont pas reprises ici ([même fichier](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraRelayServiceTpl.java#L195)).
5. **Agora RTSA.** `AgoraCameraService` demande un ticket via le fournisseur `AGORA_RTSA`, lit le canal et le token du ticket et appelle `AgoraRTSA.API.joinChannel(...)` ([AgoraCameraService.java](./decompiled/sources/com/netviewtech/client/service/camera/AgoraCameraService.java#L125), [même fichier](./decompiled/sources/com/netviewtech/client/service/camera/AgoraCameraService.java#L151)). La voie est conditionnelle ; la présence du SDK Agora n’implique pas que les Vigil du propriétaire l’utilisent.
6. **Voie locale/propriétaire.** `NvCameraLANService` se connecte au host/port du `P2PServer` fourni dans les données de l’appareil, avec un délai de deux secondes, puis prépare une commande de login locale ([NvCameraLANService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L39), [même fichier](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L113)). `NvCameraRequestMaker.makeLoginUnit` forme le login à partir de la clé locale ou de la clé du nœud et de l’adresse du nœud ([NvCameraRequestMaker.java](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L91)). Pour la branche `CameraII`, le client tente dans l’ordre `TCP_LAN`, `UDP_PUNCH`, `TCP_TRAN` sur l’adresse du serveur fournie par le nœud ([NvCameraIIService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L463)). Cette branche de code ne permet pas de classer ces Vigil sans leur révision/firmware.
7. **Images et décodage.** `NvCameraServiceCenter` transmet les `NvCameraMediaFrame` au callback du lecteur, et `CameraServiceHandler.onCameraServiceData` les fournit au lecteur ([CameraServiceHandler.java](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/player/CameraServiceHandler.java#L190)). `NVVideoCodec` déclare les codecs H.264 et HEVC et appelle des fonctions JNI de décodage/encodage ([NVVideoCodec.java](./decompiled/sources/com/netviewtech/client/media/NVVideoCodec.java#L5)). Ce code établit les capacités de l’application, pas le codec effectivement émis par ces caméras.

### Bibliothèques et verdict sur les protocoles

- **Observé dans l’inventaire :** `libagora-rtc-sdk.so`, `libjingle_peerconnection_so.so`, `libijkplayer.so`, `libijkffmpeg.so`, `librtmp.so`, ainsi que les bibliothèques propriétaires `liba4xmediakit.so` et `liba4xsignal.so` dans les deux ABI. Les classes `org.webrtc` sont incluses. Ce sont des composants embarqués, pas une preuve d’exécution.
- **WebRTC / STUN / TURN :** le SDK WebRTC Java et sa bibliothèque native sont présents ; des chaînes STUN/TURN apparaissent dans le matériel RTC. La recherche dans le code applicatif Netvue n’a pas trouvé d’appel de session `PeerConnection`/SDP/ICE reliant ces symboles au live Vigil. Le chemin applicatif identifié pour RTC est l’API Agora conditionnelle ci-dessus. Conclusion : RTC embarqué confirmé, usage de WebRTC natif direct par Vigil non établi.
- **MQTT / AWS :** usage d’AWS IoT MQTT pour topics/messages, shadow et obtention de play ticket démontré statiquement dans `NvIoTClientManagerImpl`, `NvIoTClientImpl` et `NvIoTMQTTClient`. Cela étaye un contrôle/signaling cloud possible, pas un flux média MQTT ni une API publique documentée.
- **H.264 / H.265 :** capacités de décodage/encodage H.264/HEVC via JNI et codecs natifs présents. La disponibilité du codec pour un appareil précis dépend de capacités de nœud ; codec du live de ces exemplaires non vérifié.
- **RTSP :** des marqueurs RTSP sont présents dans des bibliothèques génériques (notamment IJK et EasyPusher), mais aucun appel applicatif de connexion RTSP vers une caméra n’a été identifié. Les ports 554 fermés sont une observation antérieure du propriétaire ; elle n’exclut pas les autres protocoles.
- **TCP 5555 :** la recherche de `\b5555\b` dans les sources Java applicatives n’a rien trouvé. Un scan ASCII brut de l’APK donne des sous-chaînes `5555` dans `classes8.dex` (première occurrence à `0x6347f4`), `libjingle_peerconnection_so.so` (`0x11a430`, ABI arm64) et `libjmov.so` (`0xade35`, ABI arm64), dans des blocs binaires longs/non attribués, pas dans un endpoint ou un appel réseau identifié. Le code LAN utilise des host/ports issus des données de l’appareil ; rien ne relie ces ports au `5555/tcp` observé. **Protocole et rôle de 5555 non identifiés.**

### Conséquence pour Home Assistant

- **API/cloud :** un chemin cloud est nettement plausible : authentification/STS, MQTT AWS IoT pour demandes de ticket, puis un relais TCP ou Agora RTSA selon les capacités. Les appels observés appartiennent au protocole privé de l’application, sans indication d’une API vidéo publique/supportée pour Home Assistant.
- **Flux local :** le code comporte un chemin LAN TCP pour certaines capacités/générations et un chemin UDP punch dans la branche `CameraII`. Un flux local est donc plausible en principe, mais l’endpoint est fourni par les données appareil et le login dépend de clés/identifiants privés. Aucun RTSP local générique n’est démontré. On ne peut pas conclure que le port 5555 est le transport vidéo.
- **Limite majeure :** ni la génération de caméra ni le transport choisi n’ont été vérifiés sur les appareils ; aucune capture réseau ni connexion active n’a été faite.

## Bilan de phase 1

- **Ce qui est prouvé :** l’APK officiel Netvue 5.27.0_121868 a été téléchargé et hashé ; le code décompilé montre les branches de lecture conditionnelles, le play ticket MQTT/AWS IoT, le relais TCP, Agora RTSA, un transport LAN propriétaire et des décodeurs H.264/HEVC.
- **Ce qui reste hypothétique :** la branche et le codec utilisés par ces Vigil, la possibilité d’exploiter le chemin LAN depuis Home Assistant et le rôle de `5555/tcp`.
- **Prochaine action sûre (unique) :** relever la référence/révision sur l’étiquette ou la documentation des caméras, sans les connecter ni ouvrir de compte.

## Complément statique — audio et talk (17 novembre 2026)

Le SHA-256 de `input/netvue.apk` a été recalculé : il correspond à
`9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`.
Le contrôle CRC des entrées ZIP a réussi ; aucun condensat signé par l'éditeur
n'est disponible. L'inventaire a été réexécuté sans fichier de sortie :
5 457 entrées, neuf DEX, ABI arm64-v8a et armeabi-v7a. Python 3.13.1,
Java 25 et JADX 1.5.6 sont disponibles ; les sources utilisées proviennent de
la décompilation antérieure, dont les 155 erreurs limitent l'exhaustivité.
Cette vérification n'a exécuté ni l'APK ni son code natif.

- **Capture, confiance forte pour le code :** `AndroidAECVoiceService.initAndStartRecord`
  et sa boucle de lecture ([lignes 321–364](./decompiled/sources/com/netviewtech/client/media/audio/AndroidAECVoiceService.java#L321))
  utilisent `AudioRecord` puis `onChunkRec`. `NVCameraPlayerImpl.initAudioHandler`
  ([ligne 545](./decompiled/sources/com/netviewtech/client/player/NVCameraPlayerImpl.java#L545))
  configure fréquence et canaux à partir des paramètres audio sélectionnés.
- **Encodage et émission, confiance forte pour le code :** `NVAudioHandlerV2.init`
  et `SimpleVoiceServiceCallback.onChunkRec` initialisent l'encodeur AAC JNI,
  fractionnent les données en blocs de 2048 octets et transmettent les sorties
  encodées ([lignes 62–85 et 294–310](./decompiled/sources/com/netviewtech/client/media/audio/NVAudioHandlerV2.java#L62)).
  `NVCameraPlayerImpl.onAudioRecorded` envoie les trames AAC si le micro est
  activé, ou pendant 400 ms après son arrêt, avec l'horloge système ; la méthode
  alternative basée sur le PTS de l'appareil existe mais n'est pas appelée par
  ce callback ([lignes 88–121](./decompiled/sources/com/netviewtech/client/player/NVCameraPlayerImpl.java#L88)).
  Le profil et l'encapsulation exacte produits par l'encodeur natif restent à
  établir : la réception ADTS ne prouve pas l'ADTS en émission.
- **Cadrage et transport, confiance forte pour le chemin classique :**
  `NvCameraServiceTpl.sendMediaFrame` encapsule dans `NvCameraDataPacket`
  de type 201 ([service](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L185),
  [paquet](./decompiled/sources/com/netviewtech/client/packet/camera/NvCameraDataPacket.java#L10)) ;
  `NvCameraMediaFrame.encode` construit un en-tête de cinq octets, dont un PTS
  32 bits little-endian ([ligne 218](./decompiled/sources/com/netviewtech/client/packet/camera/data/NvCameraMediaFrame.java#L218)).
  Le LAN utilise le connecteur TCP fourni par l'appareil
  ([NvCameraLANService](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L39)).
  `NvCameraIIService.sendMediaFrame` emploie au contraire un cadrage différent
  ([ligne 286](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L286)) ;
  `AgoraChannel.send` passe par l'API audio Agora
  ([ligne 92](./decompiled/sources/com/netviewtech/client/service/camera/AgoraChannel.java#L92)).
  Aucun de ces chemins statiques ne prouve lequel utilise une Vigil précise.
- **Direction, confiance forte pour le code :** après sélection d'un canal audio,
  `NvCameraServiceTpl.handleCameraSelectedChannels` demande `TWOWAY=3`
  ([ligne 532](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L532)) ;
  `NvCameraCMDUpdateAudioDirectionReq` construit la commande 5
  ([ligne 10](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/req/NvCameraCMDUpdateAudioDirectionReq.java#L10)).
  Ce n'est pas une confirmation d'acceptation par une caméra.
- **Réception et synchronisation :** le parseur classique distingue AAC et PCM
  par un bit ([`NvCameraMediaFrame.decode`](./decompiled/sources/com/netviewtech/client/packet/camera/data/NvCameraMediaFrame.java#L142)) ;
  un autre parseur reconnaît aussi PCMU et AAC/ADTS
  ([`NVTFrameKt.parserFromNVTFrame`](./decompiled/sources/com/netviewtech/sdk/avframe/nvt3/NVTFrameKt.java#L53)).
  Dans `NVAudioHandlerV2.onAudioDataSunk`, le PTS reçu n'est pas utilisé pour
  cadencer l'audio ; les données sont décodées puis placées dans un tampon
  ([ligne 203](./decompiled/sources/com/netviewtech/client/media/audio/NVAudioHandlerV2.java#L203)).
  Le pont Go calcule ses timestamps audio RTP à partir de l'origine vidéo
  ([`video.go:246`](../internal/netvue/video.go#L246)) ; la phase 37 ne rapporte
  qu'une validation synthétique. Confiance forte pour ces chemins de code,
  faible pour une synchronisation réelle sur Vigil.

**Conclusion talk : insuffisamment compris pour une implémentation fiable.**
Il faut identifier la révision et le firmware exacts, la branche choisie, le
profil/framing AAC émis par JNI, les paramètres négociés et la sémantique des
horloges, puis obtenir une validation réelle explicitement autorisée du talk et
de la synchronisation, sans conserver de secrets ou de médias bruts. Les
preuves historiques mentionnées dans `docs/netvue-protocol.md` concernant
5555 et la vidéo reçue sont distinctes de la présente analyse statique : elles
ne rendent pas ce port universel et ne valident pas l'audio.
