# Netvue Bridge Go — Docker et Portainer

## Architecture

La stack exécute deux services sur un même nœud Linux :

- `mediamtx`, serveur partagé, lié à `127.0.0.1:8554` et limité à RTSP/TCP ;
- `netvue-bridge`, une seule instance Go qui expose le contrôle local sur
  `127.0.0.1:8765` et lance une session/supervision indépendante par caméra.

Le réseau `host` est nécessaire avec l'architecture actuelle : il permet au
pont de découvrir les caméras depuis l'interface LAN et de publier vers
MediaMTX sur loopback. Il permet aussi à Home Assistant installé sur le même
nœud d'appeler l'API locale et de lire le RTSP. Un overlay Swarm ou le DNS
`mediamtx` ne remplace pas ce partage du loopback. Les deux tâches sont
contraintes au même nœud que Home Assistant. Aucun port Docker n'est publié ;
MediaMTX et l'API Go écoutent uniquement sur loopback.

Le réseau host partage le namespace réseau de l'hôte : les collisions de ports
et l'accès des processus locaux restent possibles. N'utilisez pas `0.0.0.0` et
n'exposez pas RTSP ni l'API sur Internet. MediaMTX n'ajoute pas
d'authentification RTSP ; les publications et lectures restent locales.
L'authentification LAN Netvue reste gérée par le programme Go.
L'API de contrôle n'ajoute pas d'authentification distincte ; tout processus
local pouvant joindre loopback peut l'appeler.

Chaque caméra ajoutée dans Home Assistant possède son propre cycle de session
et de reconnexion dans le gestionnaire Go. Une panne récupérable ne bloque pas
les autres caméras. L'API gère uniquement la liste runtime ; les entrées de
configuration Home Assistant restent la source de vérité après redémarrage.
Les chemins RTSP sont stables et dérivés de l'adresse :

- `rtsp://127.0.0.1:8554/netvue_<adresse_ipv4>`

Le `restart_policy` Swarm limité à trois échecs concerne les sorties fatales
du service ; le backoff des erreurs récupérables reste géré par chaque
superviseur caméra Go.

## Image Go

Depuis la racine du dépôt, après avoir installé Docker avec BuildKit :

```sh
docker build -f netvue-go/Dockerfile -t netvue-bridge-go:phase26 netvue-go
```

Le Dockerfile épingle l'image de compilation Go 1.26.1 Linux/amd64 par digest,
compile avec `CGO_ENABLED=0`, et copie uniquement le binaire dans une image
`scratch`. L'image d'exécution n'inclut ni Python, ni FFmpeg, ni shell, ni
secret. Elle s'exécute en UID 10001, avec système de fichiers en lecture seule
et capacités supprimées. Le processus traite SIGTERM et dispose de 30 secondes
pour fermer ses connexions.

Portainer/Swarm ne construit pas le Dockerfile d'une stack. `NETVUE_BRIDGE_IMAGE`
doit désigner une image déjà accessible au nœud choisi. Pour un Swarm
multi-nœud, utilisez une référence de registre accessible aux nœuds ; ne
supposez pas qu'une image présente uniquement dans le cache d'un worker sera
disponible ailleurs. Aucune image n'est publiée par cette phase.

## Préparer Portainer

1. Utilisez un nœud Linux Docker qui partage le LAN avec les caméras et le
   réseau host avec Home Assistant. En Swarm, relevez son nom exact et placez
   les deux services sur ce nœud.
2. Construisez ou rendez accessible l'image Go avant de créer la stack.
3. Copiez [mediamtx.yml](./mediamtx.yml) vers un chemin absolu local au nœud.
   La configuration garde RTSP sur `127.0.0.1:8554`, désactive les protocoles
   inutilisés et n'enregistre pas de vidéo.
4. Dans Portainer, ajoutez une stack depuis le contenu de
   [go-stack.yaml](./go-stack.yaml) puis définissez :

| Variable | Valeur |
|---|---|
| `NETVUE_NODE` | Nom exact du nœud Linux choisi |
| `NETVUE_BRIDGE_IMAGE` | Image Go déjà accessible, idéalement épinglée par digest |
| `NETVUE_SOURCE` | IPv4 LAN locale de l'hôte utilisée pour joindre les caméras |
| `NETVUE_MEDIAMTX_CONFIG` | Chemin absolu du fichier MediaMTX sur ce nœud |

L'image Go et le YAML ne contiennent aucun identifiant Netvue ni mot de passe
RTSP. Les adresses des caméras sont fournies par les entrées HA, pas par la
stack.

Le démarrage du service `serve` ouvre l'API locale, sans contacter de caméra.
L'ajout d'une entrée depuis Home Assistant lance la session de la caméra
correspondante ; cette opération reste soumise à l'autorisation de contact.
Ce guide prépare la stack et ne déploie aucun service.

## Home Assistant

L'intégration personnalisée `custom_components/netvue_local` conserve les
config entries, crée les entités caméra et disponibilité RTSP, et communique
avec l'API Go locale pour ajouter, réconcilier et supprimer les sessions.
L'adresse caméra se saisit manuellement pour le moment ; la découverte de
candidats reste à déplacer dans le Go si elle est réintroduite dans ce flux.
L'API Go maintient seulement l'état runtime ; HA restaure ses entrées après un
redémarrage du pont. Home Assistant doit partager le réseau host du même nœud
afin que `127.0.0.1` désigne cette API et MediaMTX. Depuis un autre namespace
réseau ou un autre hôte, loopback désigne cet autre namespace et n'est pas
joignable.

Références : [Docker host network](https://docs.docker.com/engine/network/drivers/host/),
[Docker Swarm stack deploy](https://docs.docker.com/engine/swarm/stack-deploy/).

## Valider sans déployer

La configuration YAML peut être analysée localement avec PyYAML. Sur un
manager Swarm, `docker stack config` vérifie aussi l'interpolation Compose,
sans créer de service :

```sh
NETVUE_NODE=validation-node \
NETVUE_BRIDGE_IMAGE=netvue-bridge-go:validation \
NETVUE_SOURCE=192.0.2.10 \
NETVUE_MEDIAMTX_CONFIG=/tmp/mediamtx.yml \
docker stack config -c deployment/go-stack.yaml
```

Cette vérification ne contacte aucune caméra et ne valide pas les fichiers
bind, l'image, les permissions du serveur ni le démarrage effectif. Ne lancez
pas `docker stack deploy` dans le cadre d'une validation locale.
