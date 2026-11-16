# Déploiement du pont Netvue

## Architecture actuelle

Une seule image et un seul superviseur (`netvue_bridge.continuous`) servent
aux modes continu et test borné. Le stockage `/state` contient un unique
`budget.json` v4 ; les tentatives sont sérialisées par verrou local et écrites
atomiquement. Le format n'applique aucun quota permanent : les erreurs
transitoires utilisent un backoff de 5, 15, 60, 300 puis 900 secondes.
L'authentification refusée, les erreurs critiques et les arrêts ambigus
conservent un état bloqué ou `in_progress`.

Le mode borné est `test --max-duration N --max-attempts M`. Il partage le
même état et les mêmes contrôles que `run` ; l'échéance arrête le processus
sans effacer la tentative active. Les tests synthétiques du dépôt n'utilisent
pas de caméra réelle. Une exécution caméra ou un déploiement exige une
autorisation spécifique.

## Données d'état v2/v3

Ne pas initialiser par-dessus un état existant. La commande explicite
`migrate` écrit et synchronise un audit contenant l'original avant de
remplacer le fichier, puis vérifie le nouvel état. Les anciennes versions
incompatibles ne sont jamais ignorées par `run`.

- Un budget v2 est migré en `blocked` : sa limite et son historique ne
  permettent pas de déterminer une autorisation continue.
- Un budget v3 `in_progress`, `blocked`, ou dont l'horloge/le boot est
  ambigu reste `blocked`. Un état `ready` cohérent conserve son backoff et
  son compteur ; les anciens quotas glissants ne deviennent pas des quotas
  permanents.
- L'audit reste dans le répertoire d'état. La migration n'a pas été appliquée
  aux données de production et doit être testée/validée avant tout usage réel.

Ne pas copier, restaurer, supprimer ou réinitialiser un état actif pour
contourner un blocage. Le verrou coordonne uniquement les processus ayant
accès au même répertoire sur le même hôte.

## Configuration et installation future

La stack [stack.yaml](./stack.yaml) utilise le réseau host, une image locale
préconstruite, MediaMTX local et un répertoire d'état persistant. MediaMTX
RTSP écoute sur loopback seulement ; les processus de l'hôte partageant cet
espace réseau peuvent néanmoins lire/publier le flux. Le transport caméra
n'est pas chiffré. L'image fonctionne en utilisateur non-root, racine en
lecture seule et sans capacités Linux.

Avant déploiement autorisé, vérifier l'identité du nœud, l'absence de
services concurrents, la disponibilité du port 8554, les droits du répertoire
persistant et l'absence de secrets dans les variables. Swarm ne construit
pas l'image et le CLI `docker stack deploy` ne lit pas `example.env`
automatiquement. Tester la configuration rendue avant toute action :

```sh
docker build -f deployment/Dockerfile -t netvue-bridge:local .
docker stack config -c deployment/stack.yaml
```

Ces commandes de build/configuration n'initialisent pas de budget et ne
contactent pas de caméra. Avant l'exploitation, créer le répertoire d'état
avec accès en écriture pour UID/GID `10001`, puis initialiser une seule fois
sur ce répertoire :

```sh
docker run --rm --network none --read-only --cap-drop ALL --user 10001:10001 \
  --mount type=bind,src=/srv/netvue-bridge/state,dst=/state \
  netvue-bridge:local init --source "$SOURCE_LAN" --target "$CAMERA_IP" --state-dir /state
```

Cette initialisation crée un état v4 vide ; elle ne contacte pas la caméra.
Le déploiement Swarm, `run` et `test` peuvent contacter l'appareil et ne
doivent être lancés qu'après autorisation. Ne pas déployer cette stack, ne
pas migrer d'état de production et ne pas modifier Home Assistant pendant
une validation hors ligne.

## Home Assistant et limites

Home Assistant lit `rtsp://127.0.0.1:8554/netvue2` si son réseau permet
l'accès à la boucle locale du même hôte. Vérifier l'intégration et l'aperçu
dans Home Assistant après autorisation ; un test RTSP seul ne suffit pas.
La mémoire vidéo peut être exposée par le swap ou les dumps système. Les
journaux doivent être expurgés avant partage.

La surveillance locale n'assure pas l'exclusivité entre hôtes ni la reprise
après redémarrage. `in_progress`, une erreur bloquante ou une horloge ambiguë
nécessitent un examen explicite. Le déploiement réel, le fonctionnement
prolongé, les permissions Swarm et la migration d'un budget réel n'ont pas
été validés par les tests du dépôt.
