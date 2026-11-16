# Installation Netvue Bridge avec Portainer CE / Docker Swarm

## Architecture et contraintes

La stack contient deux services, tous deux limités au nœud Debian qui héberge
Home Assistant :

- `mediamtx` écoute sur `127.0.0.1:8554` et ne publie aucun port Docker.
- `bridge` découvre et contacte la caméra Netvue configurée, puis publie vers
  `rtsp://127.0.0.1:8554/netvue2`.

Les services utilisent le réseau `host`. C'est nécessaire ici : le pont doit
utiliser l'interface LAN de l'hôte pour la découverte UDP et joindre la caméra
sur TCP/5555 ; Home Assistant en réseau host doit pouvoir lire l'URL loopback.
La contrepartie est que ces processus partagent le réseau de l'hôte. Aucun
port RTSP n'est publié par la stack, et MediaMTX n'écoute que sur loopback.
L'accès RTSP est anonyme, donc les autres processus locaux peuvent lire ou
publier. Ne remplacez pas `127.0.0.1` par `0.0.0.0`.

Les deux services ont `replicas: 1`, des limites CPU/RAM, l'utilisateur UID
10001, un système de fichiers racine en lecture seule et aucune capacité Linux.
Le pont n'a pas accès au socket Docker. Sa politique Swarm relance un échec au
plus trois fois ; un état v4 bloqué, corrompu ou ambigu n'est pas réinitialisé
et reste en échec pour intervention. Le superviseur gère lui-même les coupures
transitoires et attend MediaMTX sans dépendre d'un ordre de démarrage Swarm.

Le budget v4, son verrou et les audits de migration résident dans le volume
bind monté sur `/state`. MediaMTX lit son fichier par un volume bind en
lecture seule. Les chemins source des deux volumes et la configuration caméra
sont fournis dans les variables d'environnement de la stack Portainer. Aucun
mot de passe RTSP n'est utilisé ; l'authentification LAN Netvue reste dans le
protocole du pont.

## Images et prérequis

- Hôte Debian sous Docker Engine en mode Swarm, Portainer CE administrant ce
  Swarm.
- Nœud choisi identique à celui de Home Assistant en réseau host ; connaître
  son nom exact (`docker node ls --format '{{.Hostname}}'`).
- Mode Swarm actif et cgroup v2 avec quotas effectifs ; vérifiez avec
  `docker info --format 'swarm={{.Swarm.LocalNodeState}} cgroup={{.CgroupVersion}}'`.
- Adresse IPv4 LAN locale de l'hôte pour `NETVUE_SOURCE`, et adresse IPv4 de
  la caméra pour `NETVUE_TARGET`. Ces valeurs doivent être dans les réseaux
  privés autorisés par le pont et différentes.
- Port TCP/8554 libre sur la boucle locale de ce nœud. Le pont utilise la
  découverte sortante UDP vers la caméra sur 36868 et TCP vers elle sur 5555 ;
  aucune règle d'entrée Internet n'est à ajouter. Vérifiez le port avec
  `ss -ltn '( sport = :8554 )'`. Si une ligne `LISTEN` existe déjà, ne
  déployez pas cette stack à côté et n'arrêtez pas le service existant :
  organisez d'abord un basculement autorisé.

Portainer/Swarm ne construit pas le Dockerfile. Le workflow
[publish-bridge-image.yml](../.github/workflows/publish-bridge-image.yml)
construit l'image Linux amd64 et la publie dans
`ghcr.io/mtx26/netvue-ha:phase16`. Le Dockerfile épingle l'image Python de
base et PyAV 16.1.0. Pour republier après une modification du pont, lancez
**Actions → Publish Netvue Bridge image → Run workflow** sur le dépôt GitHub.
Le workflow ne démarre aucun service et ne contacte aucune caméra.

Le dépôt GitHub étant privé, le package GHCR reste privé. Dans Portainer,
ajoutez `ghcr.io` comme registry privée avec un compte/token GitHub ayant
uniquement l'accès `read:packages`, puis sélectionnez cette registry pour la
stack. Ne placez pas le token dans le YAML ou dans une variable de stack.
Rendre l'image publique faciliterait le pull anonyme, mais exposerait aussi
le code Python inclus dans l'image ; cela exige une décision explicite.

## Installation depuis Portainer

La stack utilise les fichiers et données existants comme volumes bind ; elle
ne crée, ne copie et ne réinitialise aucun état. Dans Portainer, entrez les
variables suivantes :

| Variable | Obligatoire | Valeur |
|---|---:|---|
| `NETVUE_NODE` | oui | Nom exact du nœud Swarm partageant le réseau avec Home Assistant |
| `NETVUE_BRIDGE_IMAGE` | non | Défaut : `ghcr.io/mtx26/netvue-ha:sha-73fe8737048ca2912d89e4885dbe354116bbc204` |
| `NETVUE_SOURCE` | oui | IPv4 de l'interface hôte qui rejoint le LAN caméra |
| `NETVUE_TARGET` | oui | IPv4 de la caméra 2 validée ; ne pas supposer que la caméra 1 est compatible |
| `NETVUE_MEDIAMTX_CONFIG` | oui | Chemin hôte du fichier MediaMTX monté en lecture seule |
| `NETVUE_STATE_DIR` | oui | Chemin hôte du volume persistant du budget v4 |

Les variables sont saisies dans **Stack → Environment variables** et
interpolées par Portainer ; aucun fichier `.env`, copie locale ou `env_file`
n'est requis. Utilisez les chemins de volumes réellement prévus sur l'hôte.
Le fichier de configuration doit exister et être lisible ; le répertoire
d'état doit être inscriptible par UID/GID 10001. La registry GHCR privée doit
être sélectionnée dans Portainer pour que Swarm puisse télécharger l'image.

Le répertoire d'état doit déjà contenir le budget v4 correspondant à
`NETVUE_SOURCE` et `NETVUE_TARGET`. La stack ne lance pas `init` ou `migrate` :
une initialisation automatique pourrait masquer un mauvais volume ou
contourner un ancien état bloqué. Une installation neuve ou une migration v2/v3
nécessite l'opération explicite et sauvegardée décrite plus bas.

1. Dans **Stacks → Add stack → Upload**, chargez [stack.yaml](./stack.yaml).
2. Saisissez les cinq variables obligatoires ci-dessus. L'image GHCR est
   utilisée par défaut ; `NETVUE_BRIDGE_IMAGE` peut la remplacer.
3. Vérifiez le nom du nœud, les adresses LAN, les chemins de volumes et le tag
   de l'image.

Voir aussi la [documentation Portainer sur les stacks](https://docs.portainer.io/user/docker/stacks/add)
pour l'import de YAML et les variables d'environnement.

Le bouton **Deploy** démarre le pont, qui tentera alors de joindre la caméra.
La présente phase ne clique pas sur **Deploy** et ne modifie aucune stack
existante. Vérifiez d'abord que le port est libre et qu'un basculement de la
stack déjà installée est explicitement autorisé.

## Vérifier et exploiter

- Portainer doit afficher une réplique `1/1` pour chaque service. Consultez
  leurs journaux pour les erreurs de montage, d'image ou d'état v4.
- Sur le nœud, `ss -ltn` doit montrer MediaMTX sur `127.0.0.1:8554`, jamais
  `0.0.0.0:8554`. La stack ne contient aucune section `ports:`.
- Depuis Home Assistant en réseau host, utilisez
  `rtsp://127.0.0.1:8554/netvue2`. Dans un conteneur HA en réseau bridge,
  `127.0.0.1` désignerait le conteneur HA, pas l'hôte ; ce mode n'est pas
  compatible avec cette adresse loopback.
- Les redémarrages normaux sont réconciliés par Swarm et le budget v4 permet
  une récupération vérifiée d'une tentative interrompue. Le backoff est
  préservé. Les états `blocked`, corrompus ou ambigus ne sont jamais effacés
  ni contournés.

## Migration v2/v3

Pour une copie sauvegardée d'un budget v2/v3, ne faites pas `init`. Après
avoir contrôlé son origine et créé une autre sauvegarde, lancez uniquement la
migration explicite avec le même couple source/caméra :

```sh
NETVUE_BRIDGE_IMAGE=ghcr.io/mtx26/netvue-ha:sha-73fe8737048ca2912d89e4885dbe354116bbc204
NETVUE_SOURCE=192.168.10.1
NETVUE_TARGET=192.168.10.2
NETVUE_STATE_DIR=/srv/netvue-bridge/state
docker run --rm --network none --read-only --cap-drop ALL \
  --user 10001:10001 \
  --mount "type=bind,src=$NETVUE_STATE_DIR,dst=/state" \
  "$NETVUE_BRIDGE_IMAGE" migrate \
  --source "$NETVUE_SOURCE" --target "$NETVUE_TARGET" --state-dir /state
```

Cette opération écrit un audit et peut conserver l'état en `blocked`. Ne
l'appliquez pas aux données de production pendant cette phase et ne contournez
jamais un blocage.

## Test de configuration sans déployer

Avec Docker Engine disponible sur un manager Swarm et les variables
obligatoires fictives définies dans l'environnement du shell :

```sh
docker stack config -c deployment/stack.yaml
```

Cette commande valide le rendu Compose/Swarm, mais ne télécharge pas l'image,
ne vérifie pas les bind mounts et ne crée aucun service. Exemple de validation
environnementale sans déploiement :

```sh
NETVUE_NODE=validation-node \
NETVUE_SOURCE=192.168.10.1 \
NETVUE_TARGET=192.168.10.2 \
NETVUE_MEDIAMTX_CONFIG=/tmp/validation/mediamtx.yml \
NETVUE_STATE_DIR=/tmp/validation/state \
docker stack config -c deployment/stack.yaml
```

La validation de la stack sur un manager de production doit rester une
commande de lecture seule ; ne lancez pas `docker stack deploy` pendant une
validation isolée.

## Mise à jour

Publiez une nouvelle image depuis **Actions → Publish Netvue Bridge image**
après avoir fusionné le code choisi. Le workflow ajoute les tags `phase16` et
`sha-<commit>`. Le tag SHA par défaut de la stack est immuable ; pour mettre à
jour, remplacez `NETVUE_BRIDGE_IMAGE` par le nouveau tag SHA, puis redéployez.

Ne changez ni le chemin d'état ni l'identité source/caméra sans plan de
migration. Conservez l'ancien tag jusqu'à validation, pour permettre un retour
au code précédent compatible avec budget v4.

## Sauvegarde et retour arrière

Avant une sauvegarde à froid, mettez le service `bridge` à zéro réplique dans
Portainer et attendez son arrêt. Gardez MediaMTX en service si souhaité. Puis,
sur le Debian :

```sh
BACKUP="/srv/netvue-bridge-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
test ! -e "$BACKUP" || { echo "La sauvegarde existe déjà." >&2; exit 1; }
sudo tar --numeric-owner -czf "$BACKUP" \
  -C /srv/netvue-bridge state mediamtx.yml
```

L'archive contient le budget et les audits, ainsi que la configuration
MediaMTX. Elle contient des adresses LAN ; protégez-la et copiez-la sur un
stockage distinct. Sauvegardez également le fichier de stack et les variables
de Portainer dans un emplacement protégé.

Pour revenir en arrière côté code, remettez dans Portainer le précédent tag
d'image qui prend en charge le budget v4 et redéployez, sans restaurer un
ancien budget. Ne restaurez jamais une sauvegarde de budget pour effacer un
blocage ou annuler une tentative. Un état v2/v3 doit être sauvegardé puis
migré avec la commande explicite `migrate` de l'image, sur une copie d'abord ;
la migration peut produire un état v4 bloqué. N'utilisez pas `init` sur un
répertoire contenant déjà un état.

## Erreurs courantes

- **Service en attente** : vérifier `NETVUE_NODE`, l'état Swarm du nœud et la
  présence de l'image du pont sur ce nœud.
- **Échec de montage** : vérifier que les deux chemins hôtes existent,
  l'orthographe du fichier MediaMTX et les droits UID/GID 10001 sur `state`.
- **`RTSP listener unavailable`** : vérifier que MediaMTX est `1/1`, que le
  fichier de configuration écoute sur loopback et que 8554 n'est pas déjà
  utilisé.
- **Budget `blocked`, invalide ou version non prise en charge** : ne pas
  supprimer `budget.json` et ne pas lancer `init`. Examiner l'état et utiliser
  uniquement la migration explicite v2/v3 documentée.
- **Pas de flux dans Home Assistant** : vérifier que HA partage le réseau host
  de ce même nœud et utilise exactement `rtsp://127.0.0.1:8554/netvue2`.

## Multi-caméra

La stack est volontairement mono-caméra. Pour une caméra supplémentaire,
conservez une seule instance MediaMTX et ajoutez un service bridge séparé avec
sa propre identité cible, son propre répertoire d'état et un chemin RTSP
distinct déclaré dans la configuration MediaMTX. Ne faites pas passer
`bridge` à plusieurs répliques : elles partageraient le même budget et le même
flux. La caméra 1 n'est pas considérée compatible avant vérification.
