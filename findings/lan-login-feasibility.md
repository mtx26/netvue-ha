# Faisabilité du login LAN et du flux — Netvue Vigil

## Résumé décisionnel

- **OBSERVÉ :** une unique découverte UDP broadcast a reçu une réponse V2 de la caméra 2 ; son adresse d'écoute annoncée correspond à l'émetteur et son port annoncé est **5555**. La caméra 1 n'a pas répondu en deux secondes.
- **PROUVÉ dans l'APK :** une branche LAN construit un login à partir d'une clé de nœud, d'un utilisateur et d'un endpoint ; une autre branche CameraII utilise des informations et un échange distincts. Le login réel et le flux n'ont pas été demandés.
- **NON VÉRIFIÉ :** les contrôles d'authentification effectués par le firmware, l'autonomie sans cloud, le codec effectif, et l'accès vidéo sur ces appareils.
- **Prudence :** l'application peut déclencher une sélection de canal après un login associé à une tâche `LIVE`. Un futur essai limité au login devra empêcher toute demande de flux et requiert une autorisation distincte.

Contexte et tests précédents : [rapport des tests actifs](./active-network-tests.md). APK 5.27.0_121868 et limites de décompilation : [rapport phase 1](./phase1-report.md).

## 1. Broadcast — résultats

L'application diffuse vers UDP 36868 via une socket de datagrammes ([NvCameraFinder.java](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L40), [NvCameraDiscoveryTpl.java](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraDiscoveryTpl.java#L79)). L'essai précédent avait envoyé la même requête V2 directement en unicast. Un appareil peut traiter différemment ces destinations ; les absences de réponse ne permettent pas d'en identifier la raison.

| Appareil | Essai unicast précédent | Unique broadcast de cette session |
|---|---|---|
| Caméra 1 | Aucune réponse sous 2 s. | Aucune réponse sous 2 s. |
| Caméra 2 | ACK V2, port annoncé 5555 ; adresse d'écoute non comparée. | ACK V2, port 5555 ; adresse annoncée égale à l'adresse source. |

La caméra 2 annonce de nouveau le firmware `4203411735`, les indicateurs audio et lumière actifs, retournement et SD inactifs, et `clientVersion` 104. Ce sont des valeurs annoncées, pas une vérification physique. La génération de la caméra 1 reste inconnue. Les règles `isCameraII` de l'APK ne reconnaissent pas comme CameraII le firmware annoncé par la caméra 2 ; cela ne confirme pas son modèle matériel ([NvCameraFirmwareUtils.java](./decompiled/sources/com/netviewtech/client/packet/camera/utils/NvCameraFirmwareUtils.java#L29)).

**Commande exécutée**, les adresses privées étant remplacées par des variables : `Get-Content -Raw findings\netvue_discovery_probe.py | ssh -o BatchMode=yes -o ConnectTimeout=10 home python3 - --mode broadcast --source $SOURCE --broadcast $BROADCAST_DU_SOUS_RESEAU --targets $CAMERA1 $CAMERA2`. Le script a envoyé **un seul** datagramme, lié à l'interface LAN, puis attendu deux secondes. Un broadcast peut atteindre d'autres hôtes du sous-réseau ; seules les réponses provenant des deux adresses autorisées sont traitées. Aucun paquet brut, identifiant, clé ou adresse n'est publié.

**DÉDUIT :** cette fois, la concordance adresse/port renforce le lien entre TCP 5555 de la caméra 2 et l'endpoint de découverte LAN de Netvue. Elle ne prouve toujours pas le transport vidéo ni un login valide.

## 2. Login LAN classique — analyse statique

1. `NvCameraServiceCenter.start` donne priorité à CameraII lorsque sa règle correspond, puis au service LAN si le nœud supporte P2P/local ; d'autres branches choisissent un relais ou Agora selon les capacités ([NvCameraServiceCenter.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L340)).
2. `NvCameraLANService.onCameraServiceStart` récupère le host et le port du nœud, crée un connecteur TCP et tente de se connecter ; `onCameraConnected` appelle `doLogin` ([NvCameraLANService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L38)).
3. `makeLoginUnit` choisit la clé locale si elle n'est pas nulle, sinon la clé du nœud ; il utilise le nom d'utilisateur configuré, ou une valeur par défaut si celui-ci est vide, ainsi que l'adresse et le port du nœud ([NvCameraRequestMaker.java](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L91)). La réponse de découverte V2 dispose d'un champ de clé ; notre diagnostic ne l'a ni conservé ni affiché.
4. `NvCameraCMDClientLoginReq.writeToTarget` construit une commande JSON V1 contenant la clé, un entier pseudo-aléatoire, l'utilisateur et l'endpoint ; l'enveloppe de commande est sérialisée par `NvCameraCommandPacket` ([NvCameraCMDClientLoginReq.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/req/NvCameraCMDClientLoginReq.java#L14), [NvCameraCommandPacket.java](./decompiled/sources/com/netviewtech/client/packet/camera/NvCameraCommandPacket.java#L15)).
5. Le connecteur LAN est créé avec `create(false)`, ce qui sélectionne le connecteur simple plutôt que SSL ; l'enveloppe sortante positionne le flag de chiffrement à zéro ([NvIoConnectorFactory.java](./decompiled/sources/com/netviewtech/client/connection/camera/NvIoConnectorFactory.java#L17), [NvProtocolPacket.java](./decompiled/sources/com/netviewtech/client/packet/common/NvProtocolPacket.java#L47)). **Risque statique :** les informations du login de cette branche pourraient être exposées sur le réseau et dans les journaux. Ne pas partager les journaux bruts ; aucun login n'a été émis pour vérifier le trafic.
6. `NvCameraCMDPacketFactory` identifie les unités de réponse. `NvCameraServiceTpl.handleCameraCommandUnit` traite les objets ACK de login V1 ou V2 comme un succès puis appelle `handleCameraLoginSuccess` ([NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L480)). Le type ACK V2 expose deux champs supplémentaires ([NvCameraCMDLoginToCameraAckV2.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/ack/NvCameraCMDLoginToCameraAckV2.java#L12)).

**Limite importante :** dans la méthode d'aiguillage examinée, aucun contrôle explicite du champ de résultat `C` n'est visible avant l'événement de succès ; cela ne prouve pas qu'aucune validation n'existe ailleurs. Le code client ne révèle pas comment le firmware valide la clé, ni si une réponse particulière établit réellement une session autorisée. Un prototype ne doit pas assimiler n'importe quel ACK à une authentification réussie.

**Autonomie LAN :** la découverte et le chemin LAN existent et la réponse V2 possède un champ de clé. Toutefois, l'origine effective des droits et données de nœud, les règles du firmware et la nécessité éventuelle d'un compte ou d'une préparation cloud restent **non vérifiées**.

## 3. Branche CameraII — différente, non attribuée à ces caméras

- `NvCameraIISocketTpl` initialise notamment l'utilisateur depuis les credentials et son champ mot de passe depuis un jeton de ces credentials ([NvCameraIISocketTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIISocketTpl.java#L55)).
- `NvCameraIILANSocket.createLoginRequest` prépare utilisateur, champ de credential, identifiant appareil et type de tâche. La requête et la réponse appartiennent à des classes propres à cette branche ([NvCameraIILANSocket.java](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIILANSocket.java#L48), [NvCameraIILANLoginReq.java](./decompiled/sources/com/netviewtech/client/packet/camera/ii/NvCameraIILANLoginReq.java)).
- `doLogin` interprète une réponse de type attendu ou une erreur ; l'ACK décodé contient un identifiant de session et un second endpoint. `doConnect` ouvre ensuite un second socket et transmet l'identifiant de session ([NvCameraIILANLoginAck.java](./decompiled/sources/com/netviewtech/client/packet/camera/ii/NvCameraIILANLoginAck.java#L8), [NvCameraIILANSocket.java](./decompiled/sources/com/netviewtech/client/service/camera/ii/NvCameraIILANSocket.java#L84)).
- Cette voie ne doit pas être confondue avec le login classique. Le firmware annoncé par la caméra 2 ne satisfait pas la règle CameraII consultée ; les données réelles de la caméra 1 sont absentes.

## 4. Après le login : chemin vidéo

- Pour une tâche `LIVE`, `NvCameraServiceTpl.handleCameraLoginSuccess` lance la sélection de canal et la lecture de plugins ([NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L308)). `NvCameraServiceCenter.selectChannelImpl` construit une sélection selon tâche, qualité et média, puis la délègue au service actif ([NvCameraServiceCenter.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L612)).
- Dans la branche LAN classique, les paquets de type commande sont séparés des paquets de données ; `NvCameraDataPacket` décode ces dernières en `NvCameraMediaFrame` ([NvCameraLANService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L99), [NvCameraDataPacket.java](./decompiled/sources/com/netviewtech/client/packet/camera/NvCameraDataPacket.java#L8)). La méthode `NvCameraMediaFrame.decode` marque les images vidéo de ce chemin comme H.264 et extrait notamment le type de frame, le drapeau de frame clé et le temps de présentation ([NvCameraMediaFrame.java](./decompiled/sources/com/netviewtech/client/packet/camera/data/NvCameraMediaFrame.java#L139)). C'est une interprétation du code, pas une preuve du codec sur le fil de la caméra 2.
- La branche CameraII utilise une pipeline et un assembleur distincts ; ce dernier attend une première frame clé, regroupe les fragments par index et signale les frames incomplètes ([NvCameraIIService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L115), [NvCameraIIFramePacketAssemblerFilter.java](./decompiled/sources/com/netviewtech/client/service/camera/ii/filter/NvCameraIIFramePacketAssemblerFilter.java#L21)). `dataSunk` concatène les fragments avant remise au callback ([NvCameraIIService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraIIService.java#L315)).
- `CameraServiceHandler.onCameraServiceData` alimente le lecteur ; `NVVideoCodec` expose des décodeurs H.264 et HEVC ([CameraServiceHandler.java](./decompiled/sources/com/netviewtech/mynetvue4/ui/device/player/CameraServiceHandler.java#L190), [NVVideoCodec.java](./decompiled/sources/com/netviewtech/client/media/NVVideoCodec.java#L5)). Le choix effectif, la négociation et l'encapsulation des flux des Vigil restent inconnus.

**Architecture future hypothétique :** client autorisé → gestion explicite des erreurs et des secrets → extraction des unités vidéo et horodatages → remuxage, ou transcodage seulement si nécessaire, vers une source standard compatible Home Assistant. Il faudra valider codec, images clés, synchronisation et reconnexions avant toute utilisation. Aucun service vidéo n'est implémenté.

## 5. Prototype et validation

[netvue_lan_simulation.py](./netvue_lan_simulation.py) réutilise [netvue_discovery_probe.py](./netvue_discovery_probe.py) pour la découverte et le décodage sans exposer de secrets. La sélection d'endpoint exige que l'hôte annoncé corresponde à l'émetteur et que le port soit valide ; une divergence est rejetée. Le mode de découverte est actif et ne doit être relancé qu'avec une autorisation applicable.

Le mode `--demo` n'utilise que des données fictives : il sélectionne un endpoint fictif, prépare un paquet V1 et le passe à un serveur **simulé en mémoire**. Il ne contient aucun envoi d'authentification ni accès vidéo et ne permet pas de déduire qu'une caméra accepterait le login. Le serveur fictif vérifie uniquement ses valeurs d'exemple ; ce n'est pas une reproduction de la validation du firmware.

Vérifications locales sans réseau :

```powershell
python findings\netvue_discovery_probe.py --self-test
python findings\netvue_lan_simulation.py --self-test
python findings\netvue_lan_simulation.py --demo
```

Résultat : quatre tests du diagnostic et quatre tests du prototype réussis ; la simulation produit une acceptation fictive, sans émission réseau. Avant le broadcast, les quatre tests du diagnostic ont aussi réussi sur Debian en mémoire via SSH ; le test de diffusion y utilisait une socket simulée.

## 6. Risques, inconnues et prochaine étape

- Clés, jetons, identifiants, adresses et journaux doivent rester privés ; la branche LAN classique peut journaliser des secrets et ne montre pas de couche SSL dans le connecteur analysé.
- Le résultat du broadcast n'explique pas l'absence de réponse de la caméra 1. Aucune conclusion de panne.
- Aucun login, paquet de sélection de canal ni flux n'a été envoyé ; aucune validation caméra ou capture de vidéo n'a été effectuée.
- Il manque une interprétation confirmée du résultat de login côté caméra, les conditions d'autorisation, la faisabilité hors cloud et le format média effectivement émis.

**Prochain test concret, non exécuté :** après autorisation distincte, définir avec le propriétaire un essai d'authentification unique vers la caméra 2 avec des informations légitimement obtenues et conservées uniquement en mémoire ; employer un mode de tâche dont l'absence de sélection vidéo a été vérifiée au préalable, interrompre après l'ACK, et ne consigner qu'un résultat expurgé. Si le code ou la caméra ne permettent pas d'exclure une demande vidéo, ne pas lancer cet essai sans autorisation de lecture vidéo séparée.

## Bilan

- **PROUVÉ :** une réponse broadcast de la caméra 2 associe son adresse source à l'endpoint annoncé sur 5555 ; la caméra 1 ne répond pas dans le délai. Les branches et prérequis cités existent dans l'APK.
- **HYPOTHÉTIQUE :** que la clé annoncée suffise à un login autonome et que le flux puisse être remuxé pour Home Assistant.
- **PROCHAINE ACTION SÛRE :** obtenir une autorisation spécifique pour un essai de login strictement sans vidéo, après vérification de son mode opératoire.
