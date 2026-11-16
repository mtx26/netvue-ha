# Installation Netvue Bridge avec Portainer CE / Docker Swarm

## Architecture et contraintes

La stack contient deux services, tous deux limités au nœud Debian qui héberge
Home Assistant :

- `mediamtx` écoute sur `127.0.0.1:8554` et ne publie aucun port Docker.
- `bridge` expose une API de contrôle HTTP uniquement sur
  `127.0.0.1:8765`. Home Assistant lui demande de démarrer ou d'arrêter une
  session par caméra ; chaque superviseur publie son flux RTSP local.

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

Les budgets v4 et leurs verrous résident dans le volume bind monté sur
`/state`. Le budget à la racine est conservé pour la caméra dont l'adresse y
est inscrite ; les budgets suivants sont créés dans des sous-répertoires
propres à chaque caméra lors de son ajout depuis Home Assistant. Seul le
format v4 est accepté : un état d'une autre version, bloqué, corrompu ou
ambigu arrête le service et reste inchangé. MediaMTX lit son fichier via un
volume bind en lecture seule. Aucun mot de passe RTSP n'est utilisé ;
l'authentification LAN Netvue reste dans le protocole du pont.

L'API du pont n'a pas d'authentification supplémentaire et est accessible aux
processus qui partagent le réseau de l'hôte. Elle n'écoute que sur loopback ;
ne publiez pas le port 8765 et ne la liez pas à `0.0.0.0`.

## Images et prérequis

- Hôte Debian sous Docker Engine en mode Swarm, Portainer CE administrant ce
  Swarm.
- Nœud choisi identique à celui de Home Assistant en réseau host ; connaître
  son nom exact (`docker node ls --format '{{.Hostname}}'`).
- Mode Swarm actif et cgroup v2 avec quotas effectifs ; vérifiez avec
  `docker info --format 'swarm={{.Swarm.LocalNodeState}} cgroup={{.CgroupVersion}}'`.
- Adresse IPv4 LAN locale de l'hôte pour `NETVUE_SOURCE`. Cette adresse et les
  caméras doivent appartenir aux réseaux privés autorisés par le pont.
- Ports TCP/8554 et TCP/8765 libres sur la boucle locale de ce nœud. Le pont utilise la
  découverte sortante UDP vers la caméra sur 36868 et TCP vers elle sur 5555 ;
  aucune règle d'entrée Internet n'est à ajouter. Vérifiez le port avec
  `ss -ltn '( sport = :8554 or sport = :8765 )'`. Si une ligne `LISTEN`
  existe déjà sur l'un de ces ports, ne déployez pas cette stack à côté et
  n'arrêtez pas le service existant : organisez d'abord un basculement autorisé.

Portainer/Swarm ne construit pas le Dockerfile. Le workflow
[publish-bridge-image.yml](../.github/workflows/publish-bridge-image.yml)
construit l'image Linux amd64 et la publie dans
`ghcr.io/mtx26/netvue-ha:phase17` et `sha-<commit>`. Le Dockerfile épingle
l'image Python de base et PyAV 16.1.0. Pour publier la version de Phase 17,
il faut d'abord fusionner le code, puis lancer
**Actions → Publish Netvue Bridge image → Run workflow** sur le dépôt GitHub.
Le workflow ne démarre aucun service et ne contacte aucune caméra.

Le dépôt source doit rester privé. La mise en public du seul package GHCR est
autorisée, mais n'a pas encore été appliquée : l'accès GitHub disponible ne
possède pas `write:packages`. La visibilité du package et le pull anonyme
doivent être vérifiés avant de considérer l'image publique. Jusque-là,
Portainer doit utiliser une registry GHCR privée configurée avec
`read:packages`. Ne placez pas de token dans le YAML ou dans une variable de
stack. Le contenu Python inclus dans une image publique sera téléchargeable.

## Installation depuis Portainer

La stack utilise les fichiers et données existants comme volumes bind ; elle
ne crée, ne copie et ne réinitialise aucun état. Dans Portainer, entrez les
variables suivantes :

| Variable | Obligatoire | Valeur |
|---|---:|---|
| `NETVUE_NODE` | oui | Nom exact du nœud Swarm partageant le réseau avec Home Assistant |
| `NETVUE_BRIDGE_IMAGE` | oui | Tag SHA immuable du build Phase 17 publié |
| `NETVUE_SOURCE` | oui | IPv4 de l'interface hôte qui rejoint le LAN caméra |
| `NETVUE_MEDIAMTX_CONFIG` | oui | Chemin hôte du fichier MediaMTX monté en lecture seule |
| `NETVUE_STATE_DIR` | oui | Chemin hôte du volume persistant des budgets v4 |

Les variables sont saisies dans **Stack → Environment variables** et
interpolées par Portainer ; aucun fichier `.env`, copie locale ou `env_file`
n'est requis. Utilisez les chemins de volumes réellement prévus sur l'hôte.
Le fichier de configuration doit exister et être lisible ; le répertoire
d'état doit être inscriptible par UID/GID 10001. La configuration des caméras
et leurs flux sont ensuite gérés depuis Home Assistant.

Le gestionnaire valide le budget racine existant avant d'accepter des sessions.
S'il n'est pas au format v4, s'il est corrompu ou ambigu, le conteneur échoue
explicitement sans le réécrire. Ne lancez pas `init` et ne remplacez pas ce
fichier par un budget neuf. Un budget neuf est créé uniquement dans le
sous-répertoire d'une nouvelle caméra ajoutée depuis Home Assistant.

1. Dans **Stacks → Add stack → Upload**, chargez [stack.yaml](./stack.yaml).
2. Saisissez les variables obligatoires, notamment `NETVUE_BRIDGE_IMAGE` avec
   le tag SHA Phase 17 publié.
3. Vérifiez le nom du nœud, les adresses LAN, les chemins de volumes et le tag
   de l'image.

Voir aussi la [documentation Portainer sur les stacks](https://docs.portainer.io/user/docker/stacks/add)
pour l'import de YAML et les variables d'environnement.

**Deploy** démarre le gestionnaire et MediaMTX, sans contacter une caméra.
Une session démarre seulement quand une caméra est ajoutée dans Home
Assistant. Aucun déploiement n'a été effectué et aucune stack existante n'a
été modifiée. Vérifiez d'abord que les ports sont libres et qu'un basculement
est explicitement autorisé.

## Vérifier et exploiter

- Portainer doit afficher une réplique `1/1` pour chaque service. Consultez
  leurs journaux pour les erreurs de montage, d'image, API locale ou budget v4.
- Sur le nœud, `ss -ltn` doit montrer MediaMTX sur `127.0.0.1:8554`, jamais
  `0.0.0.0:8554`, et le manager sur `127.0.0.1:8765`. La stack ne contient
  aucune section `ports:`.
- Depuis Home Assistant en réseau host, l'intégration génère un chemin
  `rtsp://127.0.0.1:8554/netvue_<adresse_ipv4>` unique. Dans un conteneur HA
  en réseau bridge, `127.0.0.1` désignerait le conteneur HA, pas l'hôte ; ce
  mode n'est pas compatible avec cette adresse loopback.
- Les redémarrages normaux sont réconciliés par Swarm et le budget v4 permet
  une récupération vérifiée d'une tentative interrompue. Le backoff est
  préservé. Les états `blocked`, corrompus ou ambigus ne sont jamais effacés
  ni contournés.

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
NETVUE_BRIDGE_IMAGE=ghcr.io/mtx26/netvue-ha:sha-validation \
NETVUE_SOURCE=192.168.10.1 \
NETVUE_MEDIAMTX_CONFIG=/tmp/validation/mediamtx.yml \
NETVUE_STATE_DIR=/tmp/validation/state \
docker stack config -c deployment/stack.yaml
```

La validation de la stack sur un manager de production doit rester une
commande de lecture seule ; ne lancez pas `docker stack deploy` pendant une
validation isolée.

## Mise à jour

Publiez une nouvelle image depuis **Actions → Publish Netvue Bridge image**
après avoir fusionné le code choisi. Le workflow ajoute les tags `phase17` et
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

Pour revenir en arrière côté code, remettez dans Portainer un tag d'image
compatible avec le format v4 présent et redéployez, sans restaurer un ancien
budget. Ne restaurez jamais une sauvegarde de budget pour effacer un blocage
ou annuler une tentative. Ne lancez pas `init` sur un répertoire contenant
déjà un état. Les versions antérieures du format ne sont pas migrées par le
logiciel actuel ; conservez-les intactes pour une procédure ultérieure validée.

## Erreurs courantes

- **Service en attente** : vérifier `NETVUE_NODE`, l'état Swarm du nœud et la
  présence de l'image du pont sur ce nœud.
- **Échec de montage** : vérifier que les deux chemins hôtes existent,
  l'orthographe du fichier MediaMTX et les droits UID/GID 10001 sur `state`.
- **Pas de flux dans Home Assistant** : vérifier que HA partage le réseau
  host de ce même nœud, que le gestionnaire écoute sur `127.0.0.1:8765`, que
  MediaMTX écoute sur `127.0.0.1:8554` et que le budget de la caméra n'est pas
  bloqué.
- **`RTSP listener unavailable`** : vérifier que MediaMTX est `1/1`, que le
  fichier de configuration écoute sur loopback et que 8554 n'est pas déjà
  utilisé.
- **Budget `blocked`, invalide ou version non prise en charge** : ne pas
  supprimer `budget.json` et ne pas lancer `init`. L'état reste inchangé ; son
  traitement nécessite une procédure de migration validée ultérieurement.
- **Pas de flux dans Home Assistant** : vérifier que HA partage le réseau host
  de ce même nœud et utilise l'URL de flux affichée par l'intégration.

## Multi-caméra

Un seul service `bridge` gère plusieurs caméras simultanément sans plafond
logiciel prédéfini. Ajoutez chaque caméra séparément dans Home Assistant ; le
gestionnaire lui attribue un nom RTSP déterministe et un budget v4 dans son
propre sous-répertoire du volume d'état. La capacité pratique est bornée par
les ressources CPU/RAM du service et par les flux disponibles. MediaMTX reste
unique. Ne passez pas le service `bridge` à plusieurs répliques : elles
partageraient l'API et le volume d'état. La caméra 1 n'est pas considérée
compatible avant vérification.
