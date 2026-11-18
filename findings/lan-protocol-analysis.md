# Analyse statique du chemin LAN Netvue

## Résumé décisionnel

L’APK Netvue **5.27.0 (121868)** contient une voie de découverte locale UDP, plusieurs transports de lecture et un protocole propriétaire. Les adresses de session sont alimentées par des métadonnées de l’appareil ou par les réponses de découverte ; le code examiné ne relie pas le transport vidéo à TCP 5555. Cette analyse est statique : elle ne confirme ni la génération des deux Vigil, ni le chemin qu’elles choisiraient en fonctionnement.

Ce document décrit l’architecture et les preuves de code, mais ne publie pas les formats de trames, les champs de login, les secrets ou les étapes reproductibles pour ouvrir une session vidéo. Il ne propose donc pas de client Python parlant le protocole privé.

## Sources et méthode

- APK, provenance, SHA-256, outils et inventaire : [rapport de phase 1](./phase1-report.md) et [inventaire APK](./apk-inventory.json).
- Sources examinées : les fichiers décompilés cités ci-dessous sous `decompiled/sources/`.
- Recherche en lecture seule des appels de connexion, types de transport, paquets de commande/données et chemins de frames.
- JADX 1.5.6 a produit les sources, mais a signalé 155 erreurs en terminant avec le code 1. Les classes citées sont disponibles ; les résultats ne constituent pas une décompilation exhaustive.
- Aucun appareil, compte ou service Netvue n’a été contacté. Aucun paquet n’a été envoyé et aucun test actif n’a été effectué.

## Observé dans le code

### 1. Découverte locale

- `NvCameraFinder.startSearch` lance la recherche locale et délègue la diffusion UDP à `NvCameraDiscoveryTpl.broadcastMsgToCamera`. Cette classe encode une enveloppe de commande, l’envoie à l’adresse de diffusion et ouvre en parallèle une socket de réception. La classe `NvCameraFinder` fournit les constantes de diffusion du build analysé ; les valeurs numériques ne sont pas nécessaires pour conclure sur le transport et ne sont pas répétées ici.
- `NvCameraFinder.onBroadcastMessage` délègue la création de la requête à `NvCameraRequestMaker`. `onReceiveMessage` décode les réponses par `NvCameraCMDPacketFactory` et crée un nœud appareil à partir des informations de réponse. Le code renseigne notamment une adresse d’écoute, une version de firmware et des capacités ; ces données rendent la découverte différente d’un scan de ports générique.
- `startListenOnConfig` est un autre chemin UDP destiné à recevoir une notification de configuration. Son existence ne prouve pas qu’il soit utilisé pendant une session de live view.

**Références :** [NvCameraFinder.java — startSearch/onBroadcastMessage/onReceiveMessage](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L62), [NvCameraDiscoveryTpl.java — broadcastMsgToCamera/startSend/startReceive](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraDiscoveryTpl.java#L62), [NvCameraFinder.java — startListenOnConfig](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L68).

### 2. Sélection et établissement des transports

- `NvCameraServiceCenter.start` choisit une branche selon le type de caméra et les capacités de son nœud : service `CameraII`, service LAN pour un appareil déclaré joignable localement/P2P, ou service à relais. Pour certains appareils et préférences, la branche live peut aussi utiliser Agora RTSA. La condition dépend des données de l’appareil ; elle ne permet pas de classer les Vigil du propriétaire.
- `NvCameraLANService.onCameraServiceStart` obtient son endpoint via la configuration du nœud, crée un connecteur TCP et tente de s’y connecter. Le délai et la transition vers la gestion de login sont visibles dans cette méthode.
- `NvCameraIIService.connect` construit une adresse depuis `device.serverAddr`, puis essaye les catégories de transport `TCP_LAN`, `UDP_PUNCH` et `TCP_TRAN` dans cet ordre. Il crée ensuite une socket adaptée au type sélectionné et l’encapsule dans `NvCameraIITransportChannel`.
- Les classes `NvCameraIISocketTpl` et `NvCameraIITransportChannel` gèrent l’établissement, les échanges et la fermeture. Le chemin dépend donc d’un endpoint connu et d’un type de transport sélectionné ; il n’est pas défini par un port de service universel dans les appels observés.

**Références :** [NvCameraServiceCenter.java — start](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L340), [NVLocalDeviceNode.java — supportP2P/canPlayWithRTSA](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/NVLocalDeviceNode.java#L1037), [NvCameraLANService.java — onCameraServiceStart](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L39), [NvCameraIIService.java — connect/createSocket](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L463), [NvCameraIISocketTpl.java — connect](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIISocketTpl.java#L143), [NvCameraIITransportChannel.java — connect](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIITransportChannel.java#L50).

### 3. Ports et TCP 5555

- Le chemin LAN classique lit l’endpoint à partir de la configuration de l’appareil ; la branche `CameraII` lit `serverAddr`. Le chemin de découverte, séparément, utilise ses constantes locales de diffusion et des informations d’écoute reçues.
- Aucun appel réseau dans les classes applicatives examinées ne fixe ou ne désigne explicitement **5555** comme port vidéo. L’inventaire précédent a repéré des suites d’octets `5555` dans des régions binaires non attribuées, sans appel de connexion associé.
- Cela ne prouve pas que le port observé par le propriétaire ne puisse jamais être fourni comme endpoint dynamique. Il manque la valeur effective du nœud pour l’appareil, et aucun trafic n’a été capturé.

**Conclusion :** le lien entre le protocole LAN Netvue et `5555/tcp` reste **non établi**. L’étiquette de service d’un scanner et une chaîne binaire ne suffisent pas à identifier le protocole.

### 4. Paquets, authentification et demande de média

- Les chemins utilisent des enveloppes et codecs propriétaires (`NvCameraCommandPacket`, `NvProtocolPacket`, `NvCameraDataPacket` et des fabriques de paquets). Les méthodes de service distinguent les événements de commande et les données, puis font remonter des frames à `NvCameraServiceCenter`.
- Le service LAN comporte une étape de login après la connexion ; la branche `CameraII` possède ses propres classes de socket et de login. Les informations locales nécessaires proviennent du nœud appareil. Le code relie également la sélection du canal vidéo et le démarrage du service à la tâche `LIVE`.
- **Le champ de version de la confirmation est effectivement utilisé dans le chemin de commandes LAN classique.** `CmdUnitConstants.parseUnitVersion` lit `V` (et renvoie 0 si le champ manque) ; `NvCameraCMDPacketFactory.decodePacket` utilise cette valeur pour sélectionner le décodeur d’ACK version 1 ou 2. Pour l’action LIVE `A=3`, les deux branches ont un décodeur de confirmation de canal, et `NvCameraServiceTpl` traite les deux types. Une valeur absente ou autre que 1/2 devient un paquet inconnu. Cela prouve que `V` n’est pas ignoré par l’application dans ce chemin ; cela ne prouve pas que chaque firmware renvoie les deux valeurs.
- La requête de sélection LIVE est construite comme le type 3 version 2 (`NvCameraCMDSelectChannelReqV2` hérite de `BasicCMDUnitReqV2`). Les références montrent donc la compatibilité applicative des réponses versions 1 et 2, cohérente avec l’observation historique d’une réponse caméra `V=1` à une requête version 2.
- La suite ne permet pas à elle seule de valider la sécurité cryptographique, les limites de confiance, les contrôles d’autorisation ou les variations de protocole selon le firmware. La branche `CameraII` est distincte ; ces constats de version concernent le chemin de commandes classiques étudié.

**Références architecturales :** [NvCameraLANService.java — démarrage/login/réception](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L39), [NvCameraIILANSocket.java — cycle de login local](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIILANSocket.java#L48), [NvCameraIIService.java — sélection du canal et traitement des données](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L245), [NvCameraServiceCenter.java — demande de sélection du canal](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L617).

**Références de version LIVE :** [CmdUnitConstants.java — lecture de A/Q/V](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/CmdUnitConstants.java#L29), [NvCameraCMDPacketFactory.java — dispatch selon la version](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/NvCameraCMDPacketFactory.java#L58), [NvCameraRequestMaker.java — création de la sélection LIVE](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L107), [BasicCMDUnitReqV2.java — version 2 de requête](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/BasicCMDUnitReqV2.java#L5), [NvCameraServiceTpl.java — traitement des deux ACK de canal](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L483).

### 5. Données vidéo et codecs

- `NvCameraIIService` reçoit des données depuis son canal de transport, passe les données vidéo par un pipeline/assembleur et produit des `NvCameraMediaFrame`. `NvCameraServiceCenter` transmet ces frames au callback du lecteur.
- `NVVideoCodec` déclare les chemins natifs H.264 et HEVC. Cela prouve que l’application possède ces capacités de traitement, pas que le flux d’une Vigil donnée utilise l’un ou l’autre codec.
- L’analyse ne confirme pas le format exact du transport vidéo, le découpage des NAL, la négociation du codec ni la relation de ce chemin avec le port 5555.

**Références :** [NvCameraIIService.java — traitement des données vidéo](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L345), [NvCameraServiceCenter.java — remontée des frames](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L132), [NVVideoCodec.java — codecs déclarés](./decompiled/sources/com/netviewtech/client/media/NVVideoCodec.java#L5).

## Réponses aux questions

| Question | Conclusion statique | Confiance |
|---|---|---|
| Découverte LAN ? | Oui, une recherche UDP broadcast existe dans `NvCameraFinder`/`NvCameraDiscoveryTpl`; les réponses alimentent un nœud local. | Forte pour l’existence du code ; faible pour son usage par ces exemplaires. |
| TCP ou UDP ? | TCP LAN, UDP punch et relais TCP sont des branches de service ; la découverte est UDP. | Forte pour le code ; le chemin réel dépend du nœud. |
| Sélection des ports ? | Valeurs d’écoute et endpoints viennent de constantes de découverte ou des données de l’appareil/session ; aucun port vidéo universel n’est démontré. | Moyenne à forte. |
| Structure des paquets ? | Enveloppes et codecs propriétaires, avec traitements séparés commande/données. Le format opérationnel n’est pas détaillé dans ce rapport. | Forte pour l’existence ; non évaluée pour l’interopérabilité. |
| Authentification locale ? | Une étape de login dépend de données de nœud et de la branche de socket. Sa sécurité et sa compatibilité ne sont pas validées. | Moyenne. |
| Demande vidéo ? | Le chemin LAN classique construit une sélection de canal type 3 version 2 ; le parseur d’ACK utilise `V` pour choisir le décodeur version 1 ou 2. La réponse `V=1` est aussi confirmée par l’essai historique sur la caméra 2. | Forte pour le code APK et l’essai rapporté ; limitée aux branches et appareils étudiés. |
| H.264/H.265 sur le fil ? | H.264/HEVC sont supportés par les codecs de l’application ; leur emploi sur ces flux et leur encapsulation ne sont pas confirmés. | Forte pour la capacité, faible pour ces appareils. |
| TCP 5555 ? | Aucun rattachement au code LAN trouvé ; port dynamique possible en théorie, non démontré. | Non identifié. |

## Possibilités Home Assistant

Le code décompilé ne suffit pas à construire un client propriétaire fiable et sûr : il manque la génération matérielle exacte, l’endpoint effectif, le protocole versionné, la validation indépendante de l’authentification et une preuve que le flux est accessible localement. Un client Python qui reproduirait le login et demanderait la vidéo serait une implémentation d’accès à un flux de surveillance via un protocole privé ; il n’est pas fourni.

Voie recommandée : demander à Netvue une interface locale documentée et officiellement supportée pour le modèle précis. Si le fabricant fournit un endpoint standard documenté, l’architecture Home Assistant peut alors consommer cette source via une intégration compatible, sans réimplémenter le login propriétaire. Tant qu’un tel endpoint n’est pas confirmé, ne pas configurer le port 5555 comme source vidéo.

## Ce qui reste à établir

- Référence et révision matérielle exactes des deux Vigil, ainsi que leur famille de firmware.
- Si l’application sélectionne pour ces appareils la découverte locale, le chemin LAN, le relais ou Agora.
- Si l’endpoint LAN demeure joignable et si un flux local est officiellement pris en charge.
- Signification de `5555/tcp` et format réel du média transporté ; aucune interaction réseau n’a été effectuée dans cette phase.

### Prochaine étape sûre (unique)

Relever la référence/révision sur l’étiquette ou la documentation fournie avec les caméras, puis la comparer aux branches `CameraII` et `supportP2P` du code. Cette vérification ne requiert ni connexion réseau, ni compte Netvue, ni envoi de paquet.
