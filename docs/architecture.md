# Architecture

```text
Home Assistant (configuration et entités)
    | HTTP sur loopback
    v
gestionnaire Go -- session + superviseur par caméra --> LAN Netvue
    | H.264 / RTSP TCP
    v
MediaMTX sur loopback --> Home Assistant Stream
```

- `cmd/netvue-bridge/` fournit `test`, `run` et `serve`.
- `internal/manager/` gère les sessions et l'API de découverte, ajout, état, arrêt,
  reprise et suppression.
- `internal/netvue/` isole découverte LAN et unicast, authentification, transport
  vidéo, publication et supervision ; il ne dépend pas de HA.
- `custom_components/netvue_local/` gère les entrées et entités HA ; seul Go
  parle le protocole caméra.
- `deployment/go-stack.yaml` lance Go et MediaMTX sur le réseau host du nœud.

## Cycle d'une caméra

HA enregistre la configuration et demande au gestionnaire Go de créer la
session. Chaque caméra possède son propre superviseur et ses opérations de
cycle de vie sont sérialisées. Le superviseur lance successivement découverte,
connexion TCP, authentification et sélection LIVE ; il réessaie les erreurs
récupérables. L'arrêt annule la session et ferme ses connexions. Les délais et
les erreurs considérées fatales sont précisés dans le [guide du pont](./bridge.md).

## Parcours média

Le client Netvue valide les trames et publie le H.264 et l'audio pris en charge
en RTP/RTSP vers MediaMTX. HA lit normalement le flux RTSP. La publication
vidéo attend notamment les paramètres SPS/PPS et une image IDR ; les détails
et limites des horloges sont documentés dans la
[spécification LAN](./netvue-lan-client-spec.md).

En mode WebRTC optionnel, HA transmet la signalisation au gestionnaire. Un pair
par caméra lit le RTSP, négocie SDP/ICE et envoie la vidéo ; le H.264 peut être
adapté par FFmpeg si nécessaire. Pour l'audio caméra AAC, FFmpeg produit de
l'Opus pour le navigateur. Le microphone expérimental suit le chemin inverse :
Opus vers AAC, puis talkback sur la session caméra active. Le talkback reste
désactivé par défaut et sa restitution réelle n'est pas établie.

Le pair WebRTC détient sa connexion RTSP, son pair Pion et son multiplexeur
ICE. La fermeture les libère et les workers média sont attendus par le
gestionnaire. L'allocation ICE est distincte de la négociation SDP et des
workers ; les candidats sont limités à l'IPv4 source configurée. Les callbacks
RTSP audio et vidéo sont configurés séparément avant la lecture, en conservant
le même cycle de vie que le pair.

## Intégration Home Assistant

L'entrée HA crée un coordinateur qui réconcilie la caméra auprès de l'API Go
et vérifie séparément la disponibilité RTSP. Les entités exposent ces états ;
la signalisation WebRTC optionnelle passe par l'API sans traiter le protocole
Netvue en Python. Le déchargement retire les plateformes puis arrête la session
du pont, avec restauration des plateformes si le déchargement échoue.

HA conserve les entrées ; le registre Go est volatil. Après redémarrage du
pont, HA réajoute une entrée absente, mais ne relance pas une session présente
arrêtée ou en échec. La suppression HA retire la session. HA peut lancer une
recherche sur le LAN du pont, sélectionner un candidat ou saisir une IPv4.

Le gestionnaire expose l'état du superviseur et, en cas d'échec définitif, une
catégorie d'erreur non sensible. HA fournit un capteur de session, un bouton
de reprise explicite et un capteur RTSP indépendant : une session supervisée
ne garantit pas un flux, et les causes transitoires ne sont pas exposées.

L'API `127.0.0.1:8765` et MediaMTX `127.0.0.1:8554` n'ont pas
d'authentification distincte ; tout processus du même espace réseau peut y
accéder. Le protocole Netvue observé n'est pas chiffré. Détails et limites :
[pont](./bridge.md), [protocole](./netvue-protocol.md),
[déploiement](../deployment/go-portainer.md) et [roadmap](./roadmap.md).
