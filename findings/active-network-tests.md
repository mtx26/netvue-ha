# Tests réseau actifs autorisés — Netvue Vigil

> Résultats de la session suivante, dont un broadcast unique : [faisabilité du login LAN](./lan-login-feasibility.md).

## Résumé

- **PROUVÉ :** la caméra 2 a répondu à une requête Netvue de découverte V2 envoyée en UDP unicast sur le port 36868. Sa réponse annonce un port d'écoute **5555** et le serveur a établi une connexion TCP sur ce port, sans envoyer de données applicatives.
- **DÉDUIT :** cette réponse relie pour la première fois 5555 à l'endpoint annoncé par la découverte Netvue pour la caméra 2. Le code de l'application utilise l'adresse et le port annoncés comme endpoint P2P/LAN. Ce lien ne prouve pas qu'une session vidéo fonctionne sur ce port.
- **NON VÉRIFIÉ :** le rôle exact de TCP 5555, l'authentification, le transport vidéo, le codec et la dépendance au cloud. La caméra 1 n'a pas répondu à la requête UDP : elle n'a pas été sollicitée à nouveau.

Les « caméras 1 et 2 » correspondent à l'ordre fourni par le propriétaire ; leurs adresses ne sont pas reproduites ici. Aucun identifiant, clé, jeton, SSID ou réponse brute n'est conservé dans ce rapport.

## 1. Autorisation et limites respectées

L'utilisateur a autorisé explicitement les tests actifs non authentifiés sur ses deux caméras, avec une limite de trois tentatives par appareil et par test. Les actions réalisées depuis Debian sont :

| Appareil | Test | Tentatives | Résultat |
|---|---|---:|---|
| Caméra 1 | Découverte UDP unicast vers 36868 | 1 | Aucune réponse sous 2 s. |
| Caméra 2 | Découverte UDP unicast vers 36868 | 1 | Réponse de découverte V2 reçue. |
| Caméra 2 | Connexion TCP à 5555, sans payload | 1 | Connexion établie, aucune bannière spontanée sous 2 s. |

Aucune diffusion UDP, aucun scan de plage, aucune authentification, aucun accès au flux, aucun compte ni API cloud. Pas d'installation, de modification de configuration ou de firmware. Les tests ont été exécutés par Python en mémoire via SSH ; aucun script n'a été déposé sur Debian.

## 2. Protocole de découverte établi par le code

- **PROUVÉ statiquement :** [NvCameraFinder.java](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L40) utilise UDP **36868** pour la découverte ; **36869** concerne séparément la notification de succès de configuration, non testée ici. L'application diffuse normalement sur l'adresse de broadcast limitée ; les essais réalisés ici ont utilisé l'unicast pour ne contacter que les cibles autorisées.
- [NvCameraDiscoveryTpl.java](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraDiscoveryTpl.java#L110) place une requête dans un tableau JSON et l'encode en `NvCameraCommandPacket`. [NvCameraCMDBroadcastDeviceDiscoveryReqV2.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/req/NvCameraCMDBroadcastDeviceDiscoveryReqV2.java#L14) et [BasicCMDUnitReq.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/BasicCMDUnitReq.java#L55) donnent l'unité de requête sans filtre : `[{"Q":8,"V":2,"P":[]}]`.
- L'enveloppe a un en-tête de **16 octets** ; longueur du corps à l'offset 0 en entier little-endian, séquence et CRC nuls, flag nul à l'offset 12 et type de commande **200** à l'offset 14 en little-endian. Le corps est le tableau JSON UTF-8. Voir [NetviewPacketHead.java](./decompiled/sources/com/netviewtech/client/packet/common/NetviewPacketHead.java#L28), [NvCameraCommandPacket.java](./decompiled/sources/com/netviewtech/client/packet/camera/NvCameraCommandPacket.java#L15) et [NetviewAbstractPacket.java](./decompiled/sources/com/netviewtech/client/packet/common/NetviewAbstractPacket.java#L35).
- La réponse de découverte `A=8`, `V=2` contient notamment version, adresse et port d'écoute, puis indicateurs de capacités ; la clé et d'autres champs peuvent être sensibles. Voir [NvCameraCMDBroadcastDiscoveryAckV2.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/ack/NvCameraCMDBroadcastDiscoveryAckV2.java#L30) et [NvCameraFinder.onReceiveMessage](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L105). Le script n'affiche qu'une liste limitée de champs non secrets et n'enregistre pas le paquet.

## 3. Réponses observées

### Caméra 1

**OBSERVÉ :** pas de réponse au datagramme unicast pendant les deux secondes d'attente. Aucun nouveau test vers cette caméra n'a été effectué.

**NON VÉRIFIÉ :** un délai dépassé ne prouve ni panne, ni absence de ce protocole ; l'appareil pourrait notamment ne pas répondre à l'unicast, être temporairement indisponible ou être filtré.

### Caméra 2

**OBSERVÉ :**

- Réponse UDP provenant de l'adresse de la cible, décodée comme unité de découverte **V2**.
- Valeur annoncée dans le champ version du firmware : `4203411735`.
- Port d'écoute annoncé : **5555**. L'adresse annoncée dans la réponse a été écartée pour protéger les données ; sa concordance avec l'adresse source n'a pas été enregistrée.
- Indicateurs V2 : audio `true`, retournement `false`, lumière `true`, carte SD `false` ; objet de capacités présent, contenu non conservé ; `clientVersion` 104. Ces valeurs sont des déclarations du protocole, pas une validation physique des fonctionnalités.
- Une connexion TCP à la cible sur 5555 a réussi. Le client n'a rien envoyé après le handshake et aucune bannière n'est arrivée en deux secondes.

**DÉDUIT :** [NvCameraFinder.onReceiveMessage](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L135) construit un nœud local à partir de l'adresse et du port contenus dans cette réponse ; [NVLocalDeviceNode.getP2PServer](./decompiled/sources/com/netviewtech/client/packet/rest/local/device/NVLocalDeviceNode.java#L890), [NvCameraServiceConfig.getP2PServer](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceConfig.java#L246) et [NvCameraLANService.onCameraServiceStart](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L38) relient cet endpoint au connecteur LAN dans l'application. La réponse annonce donc 5555 comme port de l'endpoint de découverte, et le port 5555 de la cible accepte TCP. Puisque l'adresse annoncée n'a pas été vérifiée, l'identité exacte des deux endpoints n'est pas prouvée.

**Classification limitée :** en appliquant directement la règle `isCameraII` de [NvCameraFirmwareUtils.java](./decompiled/sources/com/netviewtech/client/packet/camera/utils/NvCameraFirmwareUtils.java#L29) à la chaîne annoncée, le résultat est `false` : son identifiant produit extrait ne correspond pas aux valeurs reconnues par cette méthode. Cela ne détermine pas la génération matérielle ni le chemin finalement sélectionné si l'application utilise d'autres métadonnées.

**NON VÉRIFIÉ :** ni la réponse de découverte ni une connexion TCP sans bannière n'identifient le format applicatif de ce socket, un flux vidéo ou les droits nécessaires.

### Prérequis de connexion examinés hors ligne

Dans la branche LAN classique, [NvCameraLANService.doLogin](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L113) appelle [NvCameraRequestMaker.makeLoginUnit](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L91) avec le nœud et un nom d'utilisateur ; la méthode choisit la clé locale si elle existe, sinon une autre clé du nœud, ainsi que l'endpoint du nœud. La réponse de découverte V2 dispose d'un champ de clé, que le diagnostic n'a ni affiché ni conservé. Cela ne démontre ni l'acceptation d'un login ni l'autonomie hors cloud.

**Précaution :** le code décompilé de `doLogin` contient un appel de journalisation susceptible d'afficher des clés et informations de compte. Ne pas partager les journaux applicatifs bruts.

## 4. Commandes et méthode reproductible

Les adresses de l'utilisateur sont remplacées ci-dessous par `$SOURCE`, `$CAMERA1` et `$CAMERA2` ; les autres arguments reflètent les commandes exécutées. `findings/netvue_discovery_probe.py` est envoyé sur l'entrée standard SSH ; aucune sortie brute réseau n'est stockée.

1. **Vérification locale sans réseau :** `python findings\netvue_discovery_probe.py --self-test` — trois tests réussis : octets de la requête, analyse d'une réponse synthétique et masquage de données sensibles, rejets de trames incomplètes/chiffrées.
2. **Même vérification sur Debian, sans trafic caméra :** `Get-Content -Raw findings\netvue_discovery_probe.py | ssh -o BatchMode=yes -o ConnectTimeout=10 home python3 - --self-test` — trois tests réussis.
3. **Découverte active :** `Get-Content -Raw findings\netvue_discovery_probe.py | ssh -o BatchMode=yes -o ConnectTimeout=10 home python3 - --mode unicast --source $SOURCE --targets $CAMERA1 $CAMERA2` — un datagramme par cible, attente 2 s, pause de 0,5 s entre cibles. Résultats ci-dessus.
4. **Lecture passive après connexion TCP :** `Get-Content -Raw findings\netvue_discovery_probe.py | ssh -o BatchMode=yes -o ConnectTimeout=10 home python3 - --mode tcp --camera 2 --targets $CAMERA1 $CAMERA2` — une connexion vers la caméra 2, aucune donnée applicative envoyée, attente maximale 2 s.

Les tests 1 et 2 ont été répétés après ajout de l'option de sélection de caméra, toujours avec succès et sans réseau caméra. Autres commandes locales de vérification : `git status --short`, `Get-ChildItem -Name scripts`, `Test-Path` pour vérifier l'absence de fichiers de sortie préexistants, et `git check-ignore` pour vérifier que les deux artefacts sont ignorés par Git. Les lectures et recherches dans les sources ont utilisé les outils de lecture locale.

L'exécution de ces commandes actives est soumise à l'autorisation et aux limites du propriétaire ; relancer une commande constitue une nouvelle tentative. Les vérifications locales et Debian ne prouvent pas la compatibilité avec tous les firmwares ; le parseur signale les trames chiffrées ou mal formées sans les déchiffrer. Il reconnaît les champs de découverte, mais n'a pas conservé ni validé le code de résultat éventuel de l'ACK.

## 5. Conclusion et prochaine étape

- **PROUVÉ :** la caméra 2 parle une découverte compatible avec le format Netvue V2 ; elle annonce 5555 comme port d'écoute ; son TCP 5555 accepte une connexion. La caméra 1 n'a pas répondu en unicast.
- **PROBABLE :** 5555 est lié au chemin de connexion LAN décrit par l'APK ; cela dépend encore de la concordance de l'adresse d'écoute annoncée avec la cible et de la branche effectivement choisie.
- **NON VÉRIFIÉ :** protocole applicatif reçu sur TCP, version matérielle exacte, session vidéo locale, codec, prérequis d'identification et besoin de cloud. Pas de preuve pour la caméra 1.
- **Prochaine action sûre :** déterminer avec le propriétaire les identifiants légitimes et la méthode d'observation autorisée, sans les divulguer ; demander une autorisation distincte avant toute authentification, lecture vidéo ou interaction avec un compte. Une éventuelle capture réseau nécessitera un point d'observation sur le chemin effectif : le serveur Debian ne voit pas automatiquement l'unicast caméra–téléphone.
