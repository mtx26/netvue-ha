# Plan d'intégration distante — Netvue Vigil et Home Assistant

> État historique avant autorisation des tests actifs : voir les résultats ultérieurs dans [Tests réseau actifs](./active-network-tests.md).

## Résumé décisionnel

- **PROUVÉ (APK)** : Netvue 5.27.0_121868 contient une découverte LAN UDP, un ancien chemin LAN TCP, une branche CameraII avec plusieurs transports, des mécanismes de login propriétaires et un pipeline qui remet des frames au lecteur. Le code choisit ces voies selon les métadonnées/capacités de l'appareil.
- **PROUVÉ (serveur, lecture seule)** : le serveur Debian a une interface Ethernet active et une route directement connectée vers le sous-réseau des caméras. Home Assistant tourne en mode réseau `host`. `tcpdump` n'est pas disponible dans le PATH. Aucune connexion ni sonde n'a été envoyée aux caméras.
- **NON VÉRIFIÉ** : que ces exemplaires utilisent le chemin LAN de l'APK, que le flux soit localement accessible, que TCP 5555 relève de Netvue, ou que la session locale soit autonome sans prérequis cloud.
- **Décision** : ne pas coder un client du protocole privé ni traiter 5555 comme une source vidéo. La voie la plus sûre est d'obtenir une interface locale documentée et supportée pour la révision matérielle concernée. Toute communication active avec une caméra attend une autorisation explicite.

## 1. Sources et périmètre

- [Rapport phase 1](./phase1-report.md) : provenance/version/hash de l'APK, inventaire natif et chemin live view général.
- [Analyse LAN](./lan-protocol-analysis.md) : architecture de découverte, transport, frames et limites sur 5555.
- Sources décompilées citées ci-dessous dans `decompiled/sources/`. JADX 1.5.6 a produit les classes examinées, mais a signalé 155 erreurs ; les conclusions restent partielles.
- Les diagnostics Debian ont été effectués via `ssh home`, sans `sudo`, avec des commandes de lecture seule. La première commande composée a échoué à cause du quoting distant ; elle n'a pas touché au réseau. La seconde commande, corrigée, a réussi.
- Aucun scan, ping, handshake, login, paquet de découverte ou test vidéo n'a été exécuté. Aucun compte Netvue ni API cloud n'a été utilisé.

Les valeurs numériques des ports de découverte, les formats de paquets, les champs d'authentification, les clés et les messages qui ouvriraient un flux ne sont pas reproduits. Ce rapport reste architectural et ne constitue pas un guide d'accès au flux de surveillance.

## 2. Architecture LAN observée dans l'APK

### Découverte UDP

1. `NvCameraFinder.startSearch` demande la diffusion d'une requête à `NvCameraDiscoveryTpl`.
2. `NvCameraDiscoveryTpl` encode une enveloppe de commande, diffuse des datagrammes UDP selon des constantes du build, écoute les réponses et ferme la recherche sur temporisation. Les erreurs d'envoi/réception sont journalisées.
3. `NvCameraFinder.onReceiveMessage` utilise `NvCameraCMDPacketFactory.decodePacket` pour distinguer les réponses de découverte et crée un nœud local avec l'adresse d'écoute, la version et des capacités rapportées.
4. `NvCameraRequestMaker.makeBroadcastDiscoveryV2` construit la requête ; les définitions de `NvCameraCMDBroadcastDiscoveryAck` et sa variante V2 décrivent les objets de réponse.

**Preuves :** [NvCameraFinder.java — startSearch/onBroadcastMessage/onReceiveMessage](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java), [NvCameraDiscoveryTpl.java — broadcastMsgToCamera/startSend/startReceive](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraDiscoveryTpl.java), [NvCameraCMDPacketFactory.java — decodePacket](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/NvCameraCMDPacketFactory.java), [NvCameraRequestMaker.java — makeBroadcastDiscoveryV2](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java).

**Limite :** ceci prouve le mécanisme de découverte dans l'application, pas la réponse de ces caméras. Le code de découverte ne doit pas être rejoué sans autorisation.

### Connexion, endpoint et erreurs

- `NvCameraServiceCenter.start` choisit un service selon le type/capacités de l'appareil et l'état de joignabilité : branche CameraII, LAN, relais ou, pour certains cas, Agora RTSA. `NVLocalDeviceNode.supportP2P` et `canPlayWithRTSA` exposent des critères, mais la génération des exemplaires n'est pas connue.
- `NvCameraLANService.onCameraServiceStart` obtient un endpoint depuis la configuration du nœud, crée un connecteur TCP et tente la connexion. Le code passe ensuite au login. Une perte de connexion peut provoquer une transition vers un transport relais ; les chemins d'erreur signalent échec de connexion/login ou fermeture.
- `NvCameraIIService.connect` essaie les types `TCP_LAN`, `UDP_PUNCH` et `TCP_TRAN` dans l'ordre défini par cette branche, puis instancie le socket correspondant. `NvCameraIITransportChannel.runLoop` lit les données, relaie les erreurs, puis se déconnecte si la lecture échoue ou se termine.
- Les endpoints sont donc issus des données de l'appareil/session, et non d'un port vidéo universel démontré par les appels examinés.

**Preuves :** [NvCameraServiceCenter.java — start](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java), [NVLocalDeviceNode.java — supportP2P/canPlayWithRTSA](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/NVLocalDeviceNode.java), [NvCameraLANService.java — onCameraServiceStart/onConnectionLost/onCameraPacketReceived](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java), [NvCameraIIService.java — connect/createSocket](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java), [NvCameraIILANSocket.java — doConnect/close](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIILANSocket.java), [NvCameraIITransportChannel.java — connect/runLoop/disconnect](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIITransportChannel.java).

### Authentification et dépendance cloud

- Les deux familles de transport ont des étapes de login propriétaires. Le chemin LAN classique construit une requête à partir du nœud appareil et des informations de l'utilisateur ; la branche CameraII a une implémentation de login distincte. Les réponses et les erreurs sont traitées par les services/socket correspondants.
- Aucune preuve statique ne permet de conclure que les caméras Vigil présentes acceptent une session locale sans préparation cloud. Même si le socket LAN lui-même n'appelle pas MQTT, ses données d'identification, son endpoint ou les capacités du nœud peuvent être obtenus ailleurs dans l'application.
- Le chemin MQTT/AWS IoT et la demande de ticket de lecture sont décrits dans le [rapport phase 1](./phase1-report.md). Cela prouve un chemin de signalisation pour les branches concernées, pas un transport MQTT de la vidéo.
- Les différences de générations sont réelles dans le code (service classique vs CameraII), mais la référence et le firmware des deux appareils ne sont pas établis. Il serait imprudent de déduire leur variante depuis le nom commercial seul.

**Conclusion :** autonomie LAN **possible dans certaines branches**, mais pas démontrée pour ces exemplaires ; authentification et récupération des prérequis restent non vérifiées.

### Demande vidéo, frames et codecs

- `NvCameraServiceCenter` construit la demande de sélection de canal pour une tâche `LIVE` et délègue au service actif.
- `NvCameraIIService.selectChannel` émet une requête de sélection via son protocole ; `onDataReceived` sépare commandes, données audio et données vidéo. Les paquets vidéo traversent un pipeline d'assemblage ; `dataSunk` remet ensuite les frames au callback du lecteur.
- `NVVideoCodec` déclare des capacités H.264 et HEVC via JNI. Cela ne démontre ni le codec réellement choisi, ni son encapsulation sur le réseau pour les Vigil du propriétaire.

**Preuves :** [NvCameraServiceCenter.java — selectChannelImpl](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java), [NvCameraIIService.java — selectChannel/onDataReceived/dataSunk](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java), [NVVideoCodec.java](./decompiled/sources/com/netviewtech/client/media/NVVideoCodec.java).

### TCP 5555

- Les appels applicatifs LAN examinés n'associent pas explicitement TCP 5555 à la vidéo.
- Les endpoints des sessions sont dynamiques ou fournis par les métadonnées du nœud. L'absence de constante ne permet donc pas d'exclure absolument que cette valeur soit fournie à l'exécution.
- Les occurrences binaires déjà rapportées ne sont pas rattachées à un appel de connexion ; elles n'identifient pas un service.

**Verdict : NON IDENTIFIÉ.** Le rôle du port reste une hypothèse non vérifiée ; aucune sonde n'a été envoyée.

## 3. Diagnostic Debian — résultat en lecture seule

| Vérification | Résultat observé | Interprétation / limite |
|---|---|---|
| Interface réseau | Interface Ethernet principale active ; interfaces Wi-Fi désactivée et plusieurs bridges/veth Docker présents. | Le serveur est connecté au LAN ; les bridges Docker sont des réseaux de conteneurs. |
| Routes IPv4 | Route directement connectée vers le sous-réseau LAN et route par défaut via la passerelle. | Le serveur a une route IP vers les adresses caméra signalées ; aucune connexion vers celles-ci n'a été testée. |
| Voisins ARP existants | Des entrées correspondaient aux deux adresses caméra précédemment signalées, mais leur état était `STALE`. Les adresses MAC ont été masquées et ne sont pas consignées ici. | Un cache périmé n'établit pas la disponibilité actuelle ni le service. |
| Home Assistant | Conteneur Home Assistant observé en mode réseau `host`. | Il partage la pile réseau de l'hôte ; cela ne lui donne pas automatiquement accès aux trames unicast échangées entre caméra et téléphone. |
| Outils | Python 3.13.5, `nc`, `ss`, `nmap` et Docker disponibles ; `tcpdump` absent du PATH. | `nmap` n'a pas été utilisé. Aucun outil n'a été installé. |
| Point d'observation | Le serveur peut être un endpoint du LAN, mais ne voit pas nécessairement les échanges caméra↔téléphone via un switch/AP. | Une capture utile pourrait nécessiter un point de capture déjà disponible sur l'AP/switch ou une capture sur l'un des endpoints ; aucune configuration n'a été modifiée. |

Les états de voisinage et interfaces sont une photographie au moment du diagnostic. Ils ne constituent pas une preuve de reachability ou de fonctionnement caméra.

## 4. Tests envisagés — tous en attente d'autorisation

Les étapes ci-dessous sont des objectifs, pas des commandes à exécuter. Elles peuvent envoyer des données vers les caméras ou solliciter un service ; aucune ne sera lancée sans accord explicite.

| Test | Objectif | Méthode de haut niveau | Données / risques | Interprétation |
|---|---|---|---|---|
| Découverte LAN | Vérifier si les caméras répondent au mécanisme de l'application. | Rejouer uniquement le mécanisme documenté par Netvue, sur un seul appareil et une durée limitée, après accord. | Datagramme propriétaire de découverte ; peut révéler des métadonnées locales. | Une réponse prouve une réponse au protocole de découverte, pas l'accès vidéo. |
| Service TCP 5555 | Identifier si le service accepte une connexion ou fournit une bannière documentée. | Sonde minimale, sans authentification ni payload applicatif, après accord. | Connexion TCP active ; peut être journalisée ou perturber un service limité. Aucun scan de plage. | Une connexion réussie n'identifie pas le protocole ; une bannière ou réponse exploitable devrait être validée par une source officielle. |
| Capacités appareil | Connaître firmware/capacités annoncés. | Lire une réponse de découverte autorisée, sans conserver d'identifiants ni secrets. | Métadonnées propres à l'appareil ; ne pas archiver Device ID, clé, MAC ou SSID. | Une capacité annoncée sélectionne une branche possible, pas la preuve qu'un flux démarre. |
| Flux local | Confirmer l'existence d'une interface vidéo locale. | Priorité à un endpoint/document fabricant ; sinon test d'application après accord distinct. | Demande de session et potentiellement authentification ; risque de lecture vidéo privée. | Seul un flux décodé depuis une source explicitement autorisée confirme le fonctionnement local. |
| Relais cloud | Déterminer si la lecture dépend du relais. | D'abord comparer la documentation et le graphe statique ; observation applicative uniquement avec autorisation d'accès au compte et sans collecte de secrets. | Interaction cloud/compte et métadonnées de session ; exclue dans le présent périmètre. | MQTT/ticket seul indique la signalisation, pas le transport des images. |

Les commandes exactes, scripts de découverte, payloads, probes de service et clients de streaming ne sont pas fournis dans ce rapport. Ils nécessiteraient une autorisation ciblée et un protocole officiellement documenté ; les détails propriétaires pourraient permettre de déclencher la capture d'un flux de surveillance.

## 5. Options d'intégration Home Assistant

| Option | Faisabilité | Cloud | Difficulté / ressources | Sécurité, stabilité et maintenance |
|---|---|---|---|---|
| **A — Home Assistant directement vers caméra** | Faible tant qu'aucun endpoint standard/supporté (RTSP/ONVIF ou autre interface locale documentée) n'est confirmé. | Potentiellement nul si l'interface locale existe ; sinon inconnu. | Faible si standard, très élevée si protocole propriétaire ; charge légère. | Meilleure simplicité si support officiel. Éviter les identifiants privés codés dans une intégration. |
| **B — Service Python sur Debian** | Moyenne seulement si Netvue fournit un SDK/protocole local autorisé ou une source standard. Aucune implémentation propriétaire n'est justifiée actuellement. | Potentiellement nul avec une source locale ; sinon dépendance possible aux tickets cloud. | Effort élevé, charge CPU/réseau faible à moyenne selon le transcodage et le nombre de flux. | Nécessite gestion sûre des secrets, reconnexion, supervision et compatibilité firmware ; service de production non recommandé avant validation. |
| **C — Passerelle locale vers protocole standard** | Potentiellement la meilleure séparation si la source en amont est documentée et la sortie standard supportée par Home Assistant. | Variable selon la source ; une passerelle ne supprime pas une dépendance cloud en amont. | Effort et consommation moyens ; une copie/transcodification peut augmenter CPU et latence. | Frontière claire entre caméra et HA, mais composant supplémentaire à maintenir. Ne pas bâtir la passerelle sur des paquets privés non validés. |

**Recommandation :** rechercher d'abord une interface locale officiellement supportée pour la référence exacte. Si elle existe, privilégier A. Si seul un SDK documenté permet la réception locale, préférer un adaptateur isolé (B ou C) qui expose ensuite un format pris en charge par Home Assistant. À défaut, ne pas déployer un contournement du login propriétaire.

## 6. Informations manquantes et prochaine étape

### Manque

- Référence/révision matérielle et firmware de chaque caméra, à obtenir depuis une documentation déjà disponible ou une information fournie par le propriétaire.
- Documentation officielle précisant si cette révision expose un flux local et quels prérequis légitimes sont requis.
- Confirmation autorisée que TCP 5555 accepte une connexion minimale et, le cas échéant, une identification officielle du service.
- Éventuelle capture passive provenant d'un point d'observation réellement situé sur le chemin du trafic ; le serveur Debian ne voit pas automatiquement le trafic unicast de l'application mobile.

### Prochaine étape concrète

Avant toute communication caméra, fournir la référence/firmware connus depuis les documents disponibles et confirmer si une **unique tentative TCP sans payload applicatif** vers un seul appareil sur le port déjà observé est autorisée. Sans cette confirmation, aucune sonde, découverte UDP, authentification ou requête vidéo ne sera exécutée.

## Bilan obligatoire

- **PROUVÉ :** architecture conditionnelle LAN/CameraII/relais dans l'APK ; chemin de découverte UDP ; frames remises au lecteur ; route Debian directement connectée au LAN ; Home Assistant en réseau `host` ; absence de `tcpdump` dans le PATH au moment du diagnostic.
- **PROBABLE :** le serveur pourrait communiquer avec les adresses caméra via son interface LAN, mais cela n'a pas été testé. Un flux local est plausible pour certaines branches de l'APK.
- **NON VÉRIFIÉ :** génération des appareils, endpoint réel, fonctionnement de l'authentification hors cloud, rôle de 5555 et codec/flux effectivement utilisés.
- **Prochaine action sûre :** identifier le modèle/firmware à partir des informations déjà disponibles ; demander une autorisation explicite avant le premier test actif borné.
