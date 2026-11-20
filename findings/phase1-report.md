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

## Enquête approfondie — données de « Device Information »

### A. Chaîne de données

L'APK analysé reste Netvue 5.27.0_121868 (SHA-256 consigné au début du rapport).
Les observations ci-dessous sont **statiques** : l'existence d'un appel dans le
code ne prouve ni son exécution pour une caméra donnée ni la réponse d'un
service.

```text
Settings → General Settings → Device Information
  → GeneralSettingPresenter.gotoDeviceInfo(serialNumber)
  → DeviceInfoActivity
  → BaseMediaActivity: DeviceNodeManager.getNode(serialNumber)
  → NVLocalDeviceNode déjà en mémoire/restauré
      ├─ liste Netvue REST (NVDevice) ──→ DeviceUtils.parse / assignFrom
      ├─ découverte caméra UDP locale ──→ NvCameraFinder / assignFromLocal
      ├─ IoT Shadow (GET et message MQTT Netvue) ──→ assignFromIoT
      ├─ refresh legacy conditionnel ──→ GET {webEndpoint}/liveinfo
      └─ MAC REST disponible, mais pas de lien Settings prouvé
  → DeviceInfoModel → Data Binding → lignes visibles/masquées
```

Le parcours et les libellés sont dans [GeneralSettingPresenter](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/preference/general/GeneralSettingPresenter.java#L299),
[BaseMediaActivity](./decompiled/sources/com/netviewtech/mynetvue4/di/base/BaseMediaActivity.java#L42),
[DeviceInfoActivity](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/preference/info/DeviceInfoActivity.java#L53),
[layout](./decompiled/resources/res/layout/activity_device_info.xml#L10) et
[strings](./decompiled/resources/res/values/strings.xml#L527).
`BaseMediaActivity` résout le numéro de série dans `DeviceNodeManager`; la page
ne lance pas elle-même une requête qui recrée toute la fiche. Le gestionnaire
peut toutefois avoir déjà chargé ou rafraîchi le nœud.

### B. Champs, producteurs et possibilités d'obtention

« Cloud ? » indique une source documentée dans l'APK, pas une obligation
universelle si une copie locale antérieure existe. « LAN ? » sépare une
possibilité constatée dans le protocole de ce que Home Assistant récupère déjà.

| Champ | Valeur affichée et producteurs | Cloud ? | LAN possible ? | Confiance / limites |
|---|---|---:|---:|---|
| **Device ID / série** | La ligne affiche `serialNumber`, pas le champ numérique `deviceID`. Le modèle REST `NVDevice` et la découverte UDP peuvent alimenter la série. | Liste REST courante, ou cache. | **Oui** : découverte `A=8,V=2`, `P[0]`. | Confirmé statiquement. Ne pas confondre l'identifiant affiché et `deviceID`. |
| **Model** | Nom produit localisé dérivé de `DeviceType`, lui-même déduit du préfixe du numéro de série. | Non requis pour ce libellé. | Classification possible à partir de la série. | Confirmé comme dérivation logicielle ; ce n'est pas une identification matérielle fiable. |
| **Device Name** | `deviceName` du nœud. La découverte initialise le nom avec la série ; si le nom manque ou correspond à la série, l'UI peut montrer un nom produit et un suffixe de série. | Le nom personnalisé arrive notamment via `NVDevice`/liste. | Pas de nom personnalisé démontré par la découverte. | Confirmé pour la chaîne d'affichage; origine d'un renommage non assimilée à la découverte. |
| **Firmware** | `currentFirmware`, rendu sous forme des quatre derniers caractères. Les sources d'écriture comprennent `NVDevice`, découverte UDP (`P[1]`), préférences générales et `reported.romVersion` du shadow. `newestFirmware` sert à l'indication de mise à jour. | REST, shadow et préférences cloud peuvent l'alimenter. | **Oui** : découverte V2 annonce une version. | Confirmé statiquement. La version affichée est raccourcie; aucune version réelle des caméras actuelles n'a été vérifiée. |
| **Hardware version** | Aucun champ ni ligne dédiée trouvé dans cette page et son modèle. | Inconnu. | Inconnu. | Non trouvé dans le parcours étudié; cela ne prouve pas son absence dans tout l'APK ou firmware. |
| **Wi-Fi IPv4** | `wifiIP`; écriture explicite depuis `reported.netStatus.wifiIp`. Les propriétés JSON existent aussi sur le nœud, sans preuve que la liste `NVDevice` les fournisse. | **Oui**, shadow observé dans le code. | Adresse d'écoute découverte ≠ preuve que cette IP d'interface soit disponible. | Confirmé pour l'écriture par shadow; présence/ fraîcheur à l'écran selon appareil inconnue. |
| **Ethernet IPv4** | `ethIP`; écriture explicite depuis `reported.netStatus.ethIp`. Même réserve sur l'origine d'un éventuel objet JSON de nœud. | **Oui**, shadow observé dans le code. | Aucune source LAN dédiée identifiée. | Confirmé pour l'écriture par shadow; support Ethernet conditionnel. |
| **Wi-Fi MAC** | `wifiMacAddress`, copié depuis les champs MAC de `NVDevice`; un helper REST peut aussi l'affecter. | Donnée du modèle liste ou endpoint REST. | Pas dans les champs de découverte identifiés. | Confirmé pour les chemins d'écriture, mais aucun appel à `NvDeviceBusiness.updateMac` retrouvé. |
| **Ethernet MAC** | `ethMacAddress`, mêmes chemins que le Wi-Fi MAC. | Donnée du modèle liste ou endpoint REST. | Pas dans les champs de découverte identifiés. | Même limite. Le test `isMACValid` s'arrête si une MAC d'interface supportée est présente; il ne garantit pas les deux. |
| **Device Owner** | `getVisibleOwnerName` préfère l'email, sinon le nom, puis met la valeur en minuscules. Le binding peut masquer la ligne pour le propriétaire. | Champs de compte fournis avec les données cloud. | Aucun champ équivalent dans la découverte. | Confirmé comme dérivation du nœud; donnée de compte à ne pas exposer sans nécessité. |
| **Connection / SSID** | Une ligne de connexion existe dans le XML, et le binding calcule un texte à partir du SSID ou du mode filaire. Mais la ligne est `gone` par défaut. | Le shadow peut contenir SSID et statut réseau. | La découverte V2 peut annoncer SSID et capacités. | Le « connection status » n'est donc pas une information visible confirmée sur cette page. Le booléen `connected` du shadow alimente l'état online du nœud, pas cette ligne masquée. |
| **Batterie, localisation, fuseau, signal** | Absents de cette page récente; présents dans l'autre écran `com.ai.addxsettings` « device info ». | Selon données/réponses de cette branche. | Non établi ici. | Écrans distincts; ne pas mélanger leurs champs avec `Device Information`. |
| **Image, badge et conseil firmware** | Image modèle chargée avec le type et la série. Le badge/texte de mise à jour dépend du propriétaire, du support IoT, du type et de la comparaison `currentFirmware`/`newestFirmware`; le texte automatique est une indication UI. | État de firmware cloud/shadow possible. | L'image n'est pas un champ caméra; firmware peut être annoncé en découverte. | Confirmé comme affichage conditionnel, pas comme preuve d'une mise à jour planifiée ou exécutée. |

Sources détaillées : [DeviceInfoModel](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/preference/info/DeviceInfoModel.java#L27),
[DeviceInfoPresenter](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/preference/info/DeviceInfoPresenter.java#L35),
[DeviceInfo binding](./decompiled/sources/com/netviewtech/mynetvue4/databinding/ActivityDeviceInfoBindingImpl.java#L512),
[NVLocalDeviceNode](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/NVLocalDeviceNode.java#L383),
[DeviceType](./decompiled/sources/com/netviewtech/client/ui/device/add/DeviceType.java#L242),
[NvCameraFinder](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L100),
[DeviceUtils](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/DeviceUtils.java#L14).

### C. Inventaire des requêtes confirmées dans le code

Les requêtes ci-dessous n'ont **pas** été exécutées pendant l'enquête. Les
URLs montrées sont les valeurs statiques de la configuration officielle de
l'APK; `EndpointPreferences` permet des endpoints de test/configurés, donc
l'URL effective d'une session n'est pas prouvée.

| But | Requête / transport | Corps et réponse | Authentification, rafraîchissement et limites |
|---|---|---|---|
| Liste appareils | HTTP **GET** `https://localweb.nvts.co/v1/devices/v3?type={type}`; `DeviceManagerImpl.getDevice` demande la liste `all` puis filtre par série. | Aucun corps GET. Réponse `NVLocalWebGetDevicesResponse.devices[]` de `NVDevice`; propriétés pertinentes : série, nom, firmware courant/dernier, propriétaire, MAC et capacités. | Intercepteur commun ajoute les en-têtes `x-nvs-*`, dont signature si authentification requise, plus `Cache-Control: no-cache`, `Content-Type`, `Accept`, langue/UA si disponibles. La signature est HMAC-SHA256 à partir des informations de session; aucune valeur secrète n'est recopiée ici. Réponse de liste ensuite fusionnée au cache mémoire. |
| MAC Wi-Fi / Ethernet | HTTP **GET** `https://api2.nvts.co/devices/{serialNumber}/mac`. Pas de query/body identifié. | Réponse directe `GetDeviceMacResponse` avec `wifiMac`, `ethMac`, `serialNumber`, `addxSn`, `addxUserSn`; pas d'enveloppe `data` dans cette chaîne. | Même intercepteur/auth Netvue. `NvDeviceBusiness.updateMac` l'appelle seulement si aucune MAC valide selon son test, mais aucun appelant à ce helper n'a été trouvé. La page Settings appelle une méthode `requestDeviceMac` vide. Un appel distinct existe dans l'écran DeviceInfo du parcours d'ajout et dans un callback AddX; cela ne prouve pas l'actualisation du nœud affiché dans Settings. |
| Détails legacy d'un appareil non-IoT | HTTP **GET** `{webEndpoint}/v1/device/{deviceID}/liveinfo`; hôte fourni dynamiquement par le nœud. | `NVLocalWebGetDeviceLiveInfoResponse.deviceNode`, objet `NVLocalDeviceNode`; pas de corps de requête identifié. | Utilisé par la branche de refresh legacy/non-IoT; ce n'est pas une requête systématique de `DeviceInfoActivity`. `assignFromRemote` fait une fusion sélective. Endpoint/valeurs effectifs non testés. |
| Credentials temporaires IoT | HTTP **GET** `{localEndpoint}/v1/sts/client?groupId={groupId}&area={cn|global}&type={type}`. `area` vaut `cn` pour une région préfixée `cn-`, sinon `global`; `type` distingue notamment owner `1` et share `2`. | Aucun corps. Réponse JSON directe `NVLocalWebGetClientSTSResponse`; le modèle contient les champs STS `accessKeyId`, `secretAccessKey`, `sessionToken`, `expireTime` (converti en millisecondes). Seuls les noms de champs sont rapportés, jamais leurs valeurs. | Authentification REST Netvue activée : headers `x-nvs-ucid`, `x-nvs-udid`, `x-nvs-uuid`, `x-nvs-time`, `x-nvs-version`, `x-nvs-userid`, `x-nvs-signature`, plus headers HTTP standards; signature HMAC-SHA256 par `INvSigner`. Hôte exact dynamique/configuré. |
| Snapshot de shadow | HTTPS **GET** logique `<iotEndpoint>/things/<serialNumber>/shadow`, via `getThingShadow(thingName=serialNumber)`. Aucun `shadowName`/query n'est envoyé; pas de corps. | Le SDK renvoie le corps dans `payload` (ByteBuffer), décodé en JSON. Le parseur Netvue attend les propriétés racine `state`, `metadata`, `version`, `timestamp`, puis traite `state.reported` et `state.desired`. | Signature AWS4 du SDK avec credentials STS; header `Content-Type: application/x-amz-json-1.0`. L'endpoint dépend de la région et peut être remplacé par celui du nœud; sa valeur effective n'est pas connue. |
| Push de shadow | MQTT sur WebSocket sécurisé signé IAM, connexion AWS IoT, port 443. Souscriptions QoS 0 `cs/<user>/#` et `cd/<user>/#`. | Le topic est interprété comme `<direction>/<user>/<action>/<serial>/…`; actions `shadow_update_document` et `shadow` sélectionnent le parseur. Le payload interprété contient `messageId`, `version`, `payload.current.state.reported`, `desired`, `metadata`, et éventuellement `previous`. | Connexion via credentials STS temporaires. Structure déduite des classes de parsing, pas d'une capture réseau réelle. Le producteur et sa provenance éventuelle d'un topic AWS Shadow ne sont pas établis. Aucun polling périodique régulier n'a été démontré. |

Références : [NVHTTPRestClient](./decompiled/sources/com/netviewtech/client/service/rest/httpclient/NVHTTPRestClient.java#L334),
[EndpointPreferences](./decompiled/sources/com/netviewtech/client/service/rest/httpclient/EndpointPreferences.java#L65),
[DeviceManagerImpl](./decompiled/sources/com/netviewtech/client/api/DeviceManagerImpl.java#L77),
[NVBaseHttpClient](./decompiled/sources/com/netviewtech/client/service/rest/httpclient/NVBaseHttpClient.java#L33),
[NvCommonHttpRequestInterceptor](./decompiled/sources/com/netviewtech/client/connection/http/NvCommonHttpRequestInterceptor.java#L117),
[GetDeviceMacResponse](./decompiled/sources/com/netviewtech/client/packet/rest/local/response/GetDeviceMacResponse.java#L9),
[NvDeviceBusiness.updateMac](./decompiled/sources/com/netviewtech/client/business/NvDeviceBusiness.java#L66),
[requête STS](./decompiled/sources/com/netviewtech/client/service/rest/httpclient/NVHTTPRestClient.java#L877),
[modèle STS](./decompiled/sources/com/netviewtech/client/packet/rest/local/response/NVLocalWebGetClientSTSResponse.java#L9),
[signataire HMAC](./decompiled/sources/com/netviewtech/client/service/rest/NvSignerJavaImpl.java#L7),
[NvSTSAuthManager](./decompiled/sources/com/netviewtech/client/auth/NvSTSAuthManager.java#L58),
[GetThingShadow marshaller](./decompiled/sources/com/amazonaws/services/iotdata/model/transform/GetThingShadowRequestMarshaller.java#L14),
[NvIoTClientImpl](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTClientImpl.java#L329),
[push MQTT](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTMQTTClient.java#L165),
[parser de topic](./decompiled/sources/com/netviewtech/client/packet/iot/NvIoTPacketFactory.java#L73).

Les erreurs REST sont transformées par les intercepteurs/réponses communs;
renouvellement d'auth et nouvelle tentative existent pour certains états de
signature/jeton. Les GET demandent `no-cache`; le cache pertinent est surtout
le nœud applicatif : `DeviceNodeManagerImpl` fusionne les nouvelles données,
et `NVApplication`/`PreferencesUtils` sérialisent et restaurent la liste via
SharedPreferences. Une valeur conservée peut donc être ancienne; le code ne
prouve pas une sauvegarde après chaque mutation shadow. Les sources sont
[intercepteur de réponse](./decompiled/sources/com/netviewtech/client/connection/http/NvCommonHttpResponseInterceptor.java#L67),
[DeviceNodeManagerImpl](./decompiled/sources/com/netviewtech/client/api/DeviceNodeManagerImpl.java#L141),
[cache application](./decompiled/sources/com/netviewtech/mynetvue4/base/NVApplication.java#L713)
et [PreferencesUtils](./decompiled/sources/com/netviewtech/mynetvue4/utils/PreferencesUtils.java#L418).

### D. Cycle de vie du Device Shadow

`DeviceNodeManagerImpl.updateNodes` met à jour/fusionne les nœuds, réévalue
asynchronement les credentials STS et déclenche les snapshots pour les appareils
IoT. `NvIoTClientManagerImpl` réutilise un client selon utilisateur, groupe et
région; l'endpoint IoT et la région sont issus du nœud. À la connexion MQTT,
le client s'abonne aux topics applicatifs `cs/<user>/#` et `cd/<user>/#`.

Deux voies sont démontrées :

1. **Snapshot initié par l'application** : `AWSIotDataClient.getThingShadow`
   reçoit le numéro de série comme `ThingName`; il est déclenché notamment
   après les mises à jour de la liste, un refresh explicite et certains
   callbacks de reconnexion.
2. **Message poussé** : le client reçoit un message Netvue sur les topics
   applicatifs, parse `shadow_update_document`/`shadow`, puis émet
   `onIoTShadowUpdated`. Son origine serveur exacte reste inconnue. Aucun
   timer de polling périodique n'a été identifié dans les chemins examinés.

Le gestionnaire trouve le nœud par série et appelle
`NVLocalDeviceNode.assignFromIoT(reported)`. Ce chemin actualise notamment :

- `romVersion` → `currentFirmware`;
- `netStatus.wifiSsid`, `wifiIp`, `wifiSignalStrength`, `wifiNoiseStrength`,
  `ethIp` et ports → champs réseau du nœud;
- `power.percent`, `power.charging`;
- `connected` → état `online`;
- état d'upgrade, stockage, sommeil, flip, lumière, codec et autres réglages.

Les champs absents/vides conservent souvent l'ancienne valeur. Aucun contrôle
d'ordre/fraîcheur par `version` n'a été identifié dans le chemin examiné. Le
code décompilé ne prouve pas l'instantané réellement retourné par le cloud ni
la fréquence effective des pushes. `DeviceInfoPresenter` actualise le firmware
et `DeviceInfoModel` pour les champs réseau lors d'un callback; le binding du
texte firmware n'est pas toujours explicitement notifié dans ce callback,
d'où une actualisation visuelle immédiate non garantie.

Références : [DeviceNodeManagerImpl shadow callback](./decompiled/sources/com/netviewtech/client/api/DeviceNodeManagerImpl.java#L78),
[gestionnaire IoT](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTClientManagerImpl.java#L66),
[client MQTT](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTMQTTClient.java#L130),
[client shadow](./decompiled/sources/com/netviewtech/client/amazon/iot/NvIoTClientImpl.java#L329),
[marshaller GetThingShadow](./decompiled/sources/com/amazonaws/services/iotdata/model/transform/GetThingShadowRequestMarshaller.java#L14),
[factory packet IoT](./decompiled/sources/com/netviewtech/client/packet/iot/NvIoTPacketFactory.java#L26),
[message shadow](./decompiled/sources/com/netviewtech/client/packet/iot/message/NvIoTShadowUpdate.java#L17),
[assignFromIoT](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/NVLocalDeviceNode.java#L643),
[présentateur page](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/preference/info/DeviceInfoPresenter.java#L68).

### E. Faisabilité sans cloud / protocole LAN

**Découverte V2 UDP, `A=8,V=2`** : le parseur APK lit l'identifiant (`P[0]`),
la version (`P[1]`), l'adresse/port d'écoute (`P[2]`,`P[3]`), puis des
capacités incluant audio, flip, lumière, SD, SSID et signal dans `P[5]`.
`P[4]` est une clé d'authentification locale; des positions facultatives
incluent `ability`, `clientVersion` et `offlineToken`; ne pas exposer la clé ni
le jeton hors du mécanisme d'authentification. L'ACK ne donne pas une
adresse IP Wi-Fi/Ethernet distincte, un MAC, un nom personnalisé, un owner ou
un modèle vérifié. Le modèle UI peut être estimé localement à partir de la
série, avec les réserves indiquées plus haut.

**État du pont Go actuel :** `internal/netvue/discovery.go` conserve seulement
l'hôte, le port, la clé locale et l'indicateur audio; `POST /api/v1/discovery`
retourne à HA la liste des `host` uniquement. La connexion LAN envoie ensuite
un login `Q=1`; son ACK vérifie le résultat `C`, puis le client lance `Q=3`
(LIVE). Ce flux n'est pas une API Device Information. Les requêtes `Q=2`
(canaux) et `Q=6` (plugins) existent dans l'APK, mais leurs données de réponse
ne sont pas établies par les tests cités; le lot qui inclut `Q=6` comporte
aussi une commande `Q=7` de réglage, donc ne pas le traiter comme une lecture
entièrement sûre.

L'APK a aussi un `APDeviceInfoMessage` (`REQUEST_GET_INFO`) via
`LocalWebSocketClient` pendant la configuration locale par point d'accès.
Cette voie peut renvoyer un objet `DeviceInfo.info` et ses réglages lorsque la
session AP est déjà connectée. Elle est distincte de la découverte/connexion
LAN normale d'une caméra déjà configurée; aucune preuve qu'elle soit
utilisable comme endpoint local général pour HA.

**Bilan par source :**

| Source | Données soutenues par preuve | Non établi |
|---|---|---|
| Découverte LAN | série, firmware annoncé, endpoint, certaines capacités et SSID/signal en V2. | MAC, owner, nom personnalisé, modèle matériel, IPv4 WLAN/Ethernet spécifique. |
| Session LAN | Authentification et LIVE; ACK login indique acceptation/refus. | Une réponse générique contenant la fiche. |
| Cache Android | Liste/nœuds et certains enrichissements déjà obtenus peuvent être restaurés. | Fraîcheur et accessibilité de ce cache par HA; HA ne partage pas les préférences Android. |
| REST Netvue signé | Liste, champs owner/name/MAC et endpoint MAC; détails legacy possibles. | Usage exact par une installation et valeurs réellement renvoyées sans requête autorisée. |
| IoT shadow/AWS | Firmware, IP/SSID/signal, alimentation, online et autres états reported. | Accès local sans credentials cloud; producteur du push. |
| Configuration SoftAP | Objet info via websocket AP dans le flux setup. | Compatibilité avec une caméra en mode LAN normal. |

Sources LAN : [spécification protocol](../docs/netvue-protocol.md#L70),
[spécification client](../docs/netvue-lan-client-spec.md#L132),
[découverte Go](../internal/netvue/discovery.go#L26),
[login Go](../internal/netvue/authentication.go#L15),
[contrat API Go](../internal/manager/http.go#L20),
[Camera API model](../internal/manager/manager.go#L31),
[DeviceConfigViewModel AP](./decompiled/sources/com/ai/addxsettings/viewmodel/DeviceConfigViewModel.java#L834).
Les tests et anciens rapports cités dans les documents sont historiques; aucune
caméra n'a été contactée pour cette enquête.

### F. Inconnues restantes et preuve nécessaire

| Inconnue | Pourquoi elle reste ouverte | Preuve sûre nécessaire |
|---|---|---|
| Valeurs réelles (série, version, IP/MAC, owner) des Vigil du propriétaire | APK statique et schémas ne prouvent pas les réponses d'un exemplaire. | Capture de diagnostic expurgée fournie volontairement par le propriétaire, ou essai explicitement autorisé et limité. |
| Visibilité effective des lignes IP/MAC selon modèle/owner | JADX signale du flux de contrôle reconstruit de manière incohérente dans `ActivityDeviceInfoBindingImpl`. | Vérification hors ligne du smali/DEX et binding compilé. |
| Caller effectif de `NvDeviceBusiness.updateMac` et fraîcheur MAC | Aucun appelant trouvé; la méthode de page Settings est vide; liste peut cependant fournir des MAC déjà présentes. | Recherche exhaustive DEX/smali (incluant lambdas/refs) et preuve du call graph. |
| Source/rythme réel du push shadow | Le code montre l'abonnement et le parseur, pas le producteur serveur ni une fréquence de production. | Docs protocoles/Cloud Netvue ou observation réseau autorisée avec expurgation; pas nécessaire pour intégration locale actuelle. |
| Format précis de réponse Q2/Q6 et métadonnées supplémentaires | Classes de requête/ACK et tests incomplets; Q6 est potentiellement groupé avec une écriture. | Test synthétique/faux appareil ou documentation APK supplémentaire; pas de caméra réelle sans autorisation. |
| Référence modèle / hardware version | Modèle UI dérivé du SN; aucune ligne HW dédiée trouvée. | Source de catalogue modèle et schéma/API vérifié, ou information firmware officielle pour la révision concernée. |
| URL/retour de chaque service en production | Endpoints et tokens peuvent être reconfigurés; aucune API n'a été appelée. | Seulement une capture/trace d'une session autorisée, sans secrets. |

### G. Recommandations pour Home Assistant (sans implémentation)

La solution la plus simple est de **ne pas réimplémenter les API cloud Netvue**
dans l'intégration HA et de garder le protocole caméra dans Go. Étendre
éventuellement le résultat de découverte Go avec les champs non secrets déjà
confirmés dans l'ACK UDP (`serialNumber`, version firmware annoncée et
capacités), sans exposer la clé locale, `offlineToken` ou des credentials AWS.
HA ne devrait pas traiter une classification `DeviceType` par préfixe comme
une référence matérielle certifiée.

N'ajouter au modèle Go puis à l'API manager que les champs effectivement lus,
optionnels et utiles. Pour HA, préférer `DeviceInfo` pour identifiant stable /
fabricant / modèle estimé et un capteur de diagnostic pour la version firmware;
ne jamais remonter email propriétaire, clé, offline token ou credentials.
L'hôte saisi/découvert peut être présenté comme adresse de connexion, sans
l'étiqueter abusivement « Wi-Fi IP » ou « Ethernet IP ». La disponibilité
actuelle et le flux RTSP restent des diagnostics distincts des attributs de
l'appareil.

Les MAC et IP d'interface restent absentes tant qu'aucune source locale fiable
n'est démontrée. Si une valeur cloud devient un jour nécessaire, ce serait une
fonctionnalité distincte exigeant une décision sur authentification, stockage,
permissions et confidentialité; ne pas intégrer une connexion Netvue au
pont local par simple réutilisation d'URL trouvée statiquement.

Dans l'état actuel, l'intégration HA stocke l'hôte/nom/URL et interroge le
gestionnaire Go en loopback; l'API manager publie `host`, stream et état de
session, et la découverte retourne seulement `host`. Elle n'a donc pas de
source de série ou de firmware aujourd'hui. Références : [coordinator](../custom_components/netvue_local/coordinator.py#L18),
[client API HA](../custom_components/netvue_local/bridge_api.py#L15),
[API manager](../internal/manager/http.go#L20),
[Camera JSON](../internal/manager/manager.go#L31).

**Bilan de confiance :** les liaisons UI, les chemins de fusion des nœuds,
les requêtes statiques et les champs décodés sont confirmés dans le code.
L'usage effectif d'un helper, les réponses de production, la fraîcheur des
caches et les possibilités non documentées du protocole restent distincts et
non confirmés. Aucune requête, authentification cloud, connexion caméra ou
modification de code de production n'a été effectuée.
