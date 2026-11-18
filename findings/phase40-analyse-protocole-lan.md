# Phase 40 — Analyse approfondie du protocole LAN Netvue

## Objectif et périmètre

Consolider les preuves existantes sur la voie LAN classique, inspecter l'APK
5.27.0 et son encodeur natif, comparer les échanges à `internal/netvue/` et
vérifier les comportements Go hors ligne. Cette phase n'a contacté aucune
caméra, aucun compte ni service Netvue; elle n'a ni envoyé de paquet ni modifié
un firmware ou service.

Les connexions et flux réels mentionnés ci-dessous sont des observations
historiques des phases antérieures, pas des essais de cette mission. Ils se
rapportent à la caméra 2 uniquement et ne s'étendent pas à tous les modèles.

## Sources et limites

- APK `input/netvue.apk`, SHA-256
  `9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`,
  version 5.27.0 / 121868 selon le manifeste. L'empreinte correspond à
  [l'inventaire APK](./apk-inventory.json). La provenance officielle est
  rapportée dans les artefacts existants, mais n'a pas été vérifiée
  indépendamment dans cette phase.
- JADX a signalé 155 erreurs; les classes décompilées citées sont disponibles,
  mais l'analyse n'est pas exhaustive. Les sources DEX utilisées se trouvent
  sous `findings/decompiled/sources/com/netviewtech/client/`.
- Le binaire ARM64 `libmobileffmpeg.so` examiné provient de l'APK local. La
  présence d'un symbole ou d'une chaîne ne prouve pas à elle seule qu'une
  branche native est exécutée.
- La spécification consolidée et les niveaux de certitude sont dans
  [docs/netvue-protocol.md](../docs/netvue-protocol.md).

## Échanges LAN classiques reconstruits

### Cadrage

L'APK emploie une enveloppe de 16 octets : longueur de corps 32 bits
little-endian à l'offset 0, séquence 32 bits à l'offset 4, CRC à l'offset 8,
flags 16 bits à l'offset 12 et type 16 bits à l'offset 14. Les commandes sont
de type 200 et les paquets média de type 201. Les types et offsets sont
étayés par [NetviewPacketHead.java](./decompiled/sources/com/netviewtech/client/packet/common/NetviewPacketHead.java#L30),
[NetviewType.java](./decompiled/sources/com/netviewtech/client/utils/NetviewType.java#L157),
[NvCameraCommandPacket.java](./decompiled/sources/com/netviewtech/client/packet/camera/NvCameraCommandPacket.java#L18)
et [NvCameraDataPacket.java](./decompiled/sources/com/netviewtech/client/packet/camera/NvCameraDataPacket.java#L10).

L'APK utilise une limite de 1 Mio dans un parseur de paquets, mais le décodeur
TCP n'établit pas cette même limite. Go borne le corps à moins de 1 Mio,
initialise séquence/CRC/flags à zéro à l'émission et vérifie longueur et flags
à la réception; il ignore séquence et CRC. L'algorithme CRC et la sémantique
de la séquence restent inconnus. Référence Go :
[protocol.go](../internal/netvue/protocol.go).

### Machine à états

| État | Échange / preuve | Correspondance Go |
|---|---|---|
| Découverte | `NvCameraFinder` diffuse la demande V2 UDP/36868; réponse d'action 8, version 2. Le nœud local est construit depuis la réponse. [NvCameraFinder.java](./decompiled/sources/com/netviewtech/client/discover/camera/NvCameraFinder.java#L40) | Découverte UDP sur l'interface configurée, adresse/émetteur vérifiés; récupération de l'endpoint TCP et capacités. [discovery.go](../internal/netvue/discovery.go) |
| Connexion | `NvCameraLANService` connecte l'endpoint du nœud en TCP, avec délai de connexion de 2 s. [NvCameraLANService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L39) | TCP vers le port fourni par la réponse; aucun port universel n'est codé. |
| Login | Requête `Q=1,V=1`; `P` contient clé locale, entier aléatoire, utilisateur et endpoint. [NvCameraRequestMaker.java](./decompiled/sources/com/netviewtech/client/service/camera/utils/NvCameraRequestMaker.java#L91), [NvCameraCMDClientLoginReq.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/req/NvCameraCMDClientLoginReq.java#L37) | Même famille de champs et délai borné. Aucun secret réel n'est publié. [authentication.go](../internal/netvue/authentication.go) |
| ACK login | Les objets ACK peuvent conserver un résultat par défaut si `C` manque; le gestionnaire LAN étudié traite l'ACK sans vérification explicite de `C`. [BasicCMDUnitACK.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/BasicCMDUnitACK.java#L20), [NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L479) | Go requiert `A=1`, `V=1/2` et un entier `C`; seul `C=0` autorise LIVE. C'est une validation volontairement plus stricte. |
| Sélection LIVE | Requête `Q=3,V=2` contenant la tâche LIVE, les canaux et une plage temporelle. [NvCameraCMDSelectChannelReqV2.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/req/NvCameraCMDSelectChannelReqV2.java#L20) | Go demande vidéo `[1,0]`, et éventuellement audio `[2,0]`, avec intervalle en secondes Unix. |
| ACK LIVE | La fabrique sélectionne un décodeur selon `V=1/2`; l'entrée de canal comprend canal, tâche, type et paramètres. [NvCameraCMDPacketFactory.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/NvCameraCMDPacketFactory.java#L58), [NvCameraChannelInfo.java](./decompiled/sources/com/netviewtech/client/packet/camera/cmd/params/channel/NvCameraChannelInfo.java#L14) | Go accepte les deux versions, exige `C=0`, rejette versions/résultats invalides et attend l'ACK avant les médias. |
| Médias | Type 201; en-tête média 5 octets : type dans le demi-octet haut, bit `0x01` image-clé, bit `0x02` OSD/PCM, PTS 32 bits little-endian, charge utile. La voie LAN classique traite la vidéo comme H.264 et l'audio sans bit PCM comme AAC. [NvCameraMediaFrame.java](./decompiled/sources/com/netviewtech/client/packet/camera/data/NvCameraMediaFrame.java#L142) | Go sépare audio/vidéo, parse Annex B et publie H.264/RTSP; l'horloge vidéo est convertie en 90 kHz. |
| Direction audio | `Q=5,V=1,P=[3]` après sélection audio pour le talkback, sans attente explicite d'ACK. [NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L532) L'ACK `A=5` est décodé puis logué comme commande inconnue; la réponse après `P=[1]` est non vérifiée. | Go demande `P=[1]` pour réception; le mode expérimental envoie `P=[3]`. Il ne réagit à un refus `A=5` que dans le mode talkback. |
| Fermeture | Le chemin LAN ferme le connecteur à l'arrêt; aucun envoi périodique LAN de heartbeat n'a été démontré. [NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L193), [NvCameraLANService.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraLANService.java#L109) | Go ferme la connexion TCP à la fin/erreur; pas de commande logout ni heartbeat applicatif. |

### Champs et réglages audio

La réponse de sélection décrit les paramètres audio comme
`[samplerate,samplesize,channels]`; `samplesize` n'est pas démontré comme une
profondeur en bits. La caméra 2 avait historiquement annoncé `[8000,1,1]`,
puis fourni de l'AAC/ADTS dans un flux décodé. Cette observation est
documentée dans [le rapport de phase 37](./phase37-audio-ha.md); elle ne
généralise pas aux autres appareils.

L'APK ajoute `Q=7,V=1,P=[[0,1,[true]]]` (`audioOn=true`) avant la sélection
LIVE dans le même tableau de commandes
([NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L358)).
Son effet matériel, sa persistance et sa nécessité ne sont pas établis. Go ne
l'envoie pas, car il pourrait modifier une configuration de la caméra.

Le chemin Android d'émission enregistre des blocs microphone, les encode et
les encapsule en type 201. `nvAACEncInit` est présent à `0x18a4b0` dans
`libmobileffmpeg.so` ARM64; l'analyse confirme le profil AAC-LC et les
branches conditionnelles ADTS/LATM/global-header du wrapper FDK (`0x4cdeb0`).
Le réglage effectif des options et le format réellement émis restent inconnus.
Une restitution audible du talkback n'a pas été confirmée par les observations historiques, cf.
[phase39-talkback.md](./phase39-talkback.md).

### Heartbeat, timeouts et reconnexion

Le connecteur Android LAN classique a un délai d'inactivité de lecture de
30 s ([NvIoConnectorFactory.java](./decompiled/sources/com/netviewtech/client/connection/camera/NvIoConnectorFactory.java#L39)).
`Q=15` est reconnu à réception et journalisé
([NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L525));
l'envoi périodique trouvé concerne Agora, pas le LAN classique. Le choix entre
LAN, Agora et relais ainsi que les reconnexions dépendent de l'état de login,
du transport et du compteur d'échecs
([NvCameraServiceTpl.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceTpl.java#L245),
[NvCameraServiceCenter.java](./decompiled/sources/com/netviewtech/client/service/camera/NvCameraServiceCenter.java#L643));
les règles de `TCP_TRAN_RING` ne doivent pas être appliquées au LAN classique.

Les classes d'ACK LIVE V1/V2 ont des parents inversés, mais leur lecture
remplace `V` par la valeur reçue. La requête de fermeture Q13 est construite en
V2 alors que le dispatch examiné apparaît en V1. Ces incohérences DEX ne
démontrent pas le comportement effectif de fermeture ou de reconnexion.

Go ne transmet pas de heartbeat applicatif. Le timeout d'absence vidéo est
3 s après démarrage (5 s pour la première image), puis le superviseur réessaie
séquentiellement avec backoff 2–30 s et réinitialise le délai après 5 min de
session stable. Ces décisions sont testées hors ligne, pas sur caméra dans
cette phase. L'APK utilise une temporisation LAN plus longue; aucune preuve
disponible ne démontre que le timeout Go provoque des coupures sur une caméra
compatible.

## Points corrigés et décisions

- La spécification de référence a été enrichie dans
  [docs/netvue-protocol.md](../docs/netvue-protocol.md) avec offsets, commandes,
  réponses, médias, différences APK/Go et limites de preuve.
- Les anciens rapports restent des comptes rendus historiques. Le lien du
  port TCP annoncé de la caméra 2 à une session réussie est prouvé par les
  phases antérieures; il n'est pas universel.
- Aucun défaut Go concret n'a été démontré par l'analyse hors ligne. Le
  contrôle Go plus strict des résultats d'authentification/LIVE est conservé;
  Q7 n'est pas ajouté; aucun heartbeat ou protocole de fermeture spéculatif
  n'est implémenté.
- Aucun code Go ni test n'a été modifié dans cette phase. Les tests existants
  vérifient les échanges synthétiques, versions d'ACK, erreurs, trames audio,
  émission simultanée et annulation.

## Validation et limites restantes

Tests exécutés sans accès caméra :

- `go test ./internal/netvue` — réussi.
- `go test -race ./internal/netvue` — réussi.
- `go test ./...` — réussi pour `cmd/netvue-bridge`, `internal/manager` et
  `internal/netvue`.
- `git diff --check` — aucune erreur d'espacement; seuls des avertissements
  locaux de conversion LF/CRLF préexistants ont été affichés.

À établir hors ligne : la portée du timeout du décodeur TCP Android et
l'interprétation complète des incohérences de dispatch Q13. Le résultat d'un
ACK audio `A=5` après la demande Go `Q=5,P=[1]`, le format natif effectivement
émis et l'acceptation du talkback restent non vérifiés. À établir par une
expérience autorisée distincte seulement : talkback audible, comportement
après reconnexion réelle, variantes de firmware et compatibilité d'autres
modèles.
