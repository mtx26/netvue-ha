# Netvue Bridge Go — Docker et Portainer

## Avant tout essai réel

**Ne démarrez pas encore la caméra 2 avec cette version.** Un état historique
v3 a été rapporté bloqué sur le serveur ; son état actuel et son association
exacte à l'appareil restent à vérifier. Le pont Go ne lit pas cet état. Créer
un nouveau répertoire ou utiliser Go pour éviter ce blocage le contournerait.
Il faut d'abord identifier l'état canonique et tous les producteurs, préparer
et tester hors ligne une réconciliation conservant l'historique, puis obtenir
une autorisation distincte avant toute modification des données réelles.

Le parcours HA avec `serve` supervise et peut reconnecter sans borne globale
de temps ni de nombre de tentatives. Le `max_attempts` Swarm ne borne que les
redémarrages de tâche. La commande Go `test` effectue une session bornée sans
boucle de reconnexion, mais ne consulte pas le budget historique et ne pilote
pas les entités HA. Elle ne doit pas servir à contourner l'état bloqué. Un
premier essai complet HA doit donc attendre une méthode vérifiée qui respecte
à la fois l'état historique, les limites acceptées et l'absence de producteurs
concurrents. Ne choisissez ni durée ni nombre de tentatives sans confirmation.

Les étapes ci-dessous préparent l'installation ; leur exécution sur le
serveur, tout redémarrage HA et tout contact caméra exigent chacun les
autorisations nécessaires. Une entrée HA existante peut recréer une session
dès qu'un gestionnaire répond : ne supposez pas que le démarrage de `serve`
est sans contact tant que les clients existants ne sont pas neutralisés.

Lors du contrôle passif du 17 novembre 2026, le serveur présentait déjà les
services `netvue_bridge` (ancien pont Python) et `netvue_mediamtx` actifs,
ainsi que les ports 8554 et 8765 occupés. HA utilisait le réseau host et son
répertoire `/config` correspondait à `/home/mtx26/homeassistant` ; une
intégration `custom_components/netvue_local` y existait déjà. Ces faits
peuvent évoluer : les revérifier avant toute opération. Ne pas déployer une
seconde stack concurrente, écraser l'intégration existante ni arrêter les
services sans autorisation et plan de transition.

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

Le Dockerfile épingle l'image de compilation Go 1.26.1 Linux/amd64 par digest,
compile avec `CGO_ENABLED=0`, et copie uniquement le binaire dans une image
`scratch`. L'image d'exécution n'inclut ni Python, ni FFmpeg, ni shell, ni
secret. Elle s'exécute en UID 10001, avec système de fichiers en lecture seule
et capacités supprimées. Le label OCI relie l'image au dépôt source. Le
processus traite SIGTERM et dispose de 30 secondes pour fermer ses connexions.

Le workflow [publish-go-image.yml](../.github/workflows/publish-go-image.yml)
est préparé pour tester Go et HA puis publier, à chaque push sur `main`,
`ghcr.io/mtx26/netvue-bridge-go:latest` et un tag `sha-<commit>`. Un lancement
manuel est aussi possible ; seule la branche `main` publie. Le même lancement
fournit l'artefact HA `netvue-local-<commit>` à télécharger depuis GitHub
Actions. Son résumé affiche le digest immuable de l'image. Le workflow ne
publie pas l'ancienne image Python.

**Ces changements sont locaux : la publication automatique et l'artefact ne
seront disponibles qu'après transmission autorisée des changements sur `main`
et réussite de GitHub Actions.** La version `latest` évolue à chaque publication ;
une stack déjà déployée ne se met pas automatiquement à jour. Pour figer une
version et retrouver l'intégration correspondante, utilisez le digest du
résumé du même lancement et l'artefact associé.

Le package GHCR existant est privé. Pour le télécharger depuis Portainer sans
changer sa visibilité, un compte GitHub ayant accès au package doit créer un
jeton personnel **classic** avec `read:packages`. Dans **Registries → Add
registry → Custom registry**, renseignez l'URL `ghcr.io`, activez
**Authentication** et indiquez le nom GitHub et ce jeton comme mot de passe.
Sélectionnez ce registre lors du déploiement si Portainer le propose. Ne mettez
pas le jeton dans le YAML, les variables de stack ou les journaux. Consultez
[GitHub Packages](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
et [Portainer](https://docs.portainer.io/admin/registries/add/custom).

## Préparer Portainer

Sur un Swarm Linux/amd64 partageant le réseau host avec HA, ouvrez **Stacks →
Add stack → Web editor**, collez [go-stack.yaml](./go-stack.yaml) et renseignez :

| Variable | Valeur |
|---|---|
| `NETVUE_NODE` | Nom exact du nœud Linux choisi |
| `NETVUE_SOURCE` | IPv4 LAN locale de l'hôte utilisée pour joindre les caméras |
| `NETVUE_BRIDGE_IMAGE` | Facultatif : digest ou tag Go à la place de `latest` |

Le YAML contient les réglages MediaMTX : aucun fichier n'est à copier sur le
serveur. Il garde RTSP sur loopback, désactive les autres protocoles et
l'enregistrement, et refuse le remplacement d'un publieur. Le fichier
[mediamtx.yml](./mediamtx.yml) reste la référence utilisée par les tests.

L'image Go et le YAML ne contiennent aucun identifiant Netvue ni mot de passe
RTSP. Les adresses des caméras sont fournies par les entrées HA, pas par la
stack.

Le service `serve` ouvre l'API locale sans session initiale. Un client HA
existant peut cependant créer immédiatement une session par réconciliation ;
l'ajout d'une entrée lance également la session de la caméra correspondante.
Ces contacts restent soumis à autorisation. Ce guide prépare la stack et ne
déploie aucun service.

### Séquence opérateur préparée

Après autorisation de modification du serveur, et avant tout déploiement :

1. Depuis le nœud manager Swarm, relever les faits sans contacter de caméra :
   `docker node ls`, `docker info --format '{{.OSType}}/{{.Architecture}}'`,
   `docker service ls`, `ip -4 address` et `ss -lnt`. Confirmer le nœud
   Linux/amd64, son IPv4 LAN, les ports `8554` et `8765`, les services ou
   clients existants et le partage effectif du réseau host avec HA. Une VM ou
   un conteneur HA sur un autre réseau ne partage pas automatiquement loopback.
2. Identifier et préserver les états et verrous Netvue historiques. Ne pas
   réinitialiser, déplacer, écraser ou recréer un budget pour éviter un
   blocage. Établir la réconciliation et les exclusivités avant tout essai.
3. Vérifier que le lancement GitHub Actions de la version voulue a réussi ;
   configurer l'accès GHCR privé dans Portainer comme indiqué ci-dessus.
4. Dans Portainer, choisir **Swarm → Stacks → Add stack → Web editor**,
   coller le YAML et renseigner les deux variables obligatoires du tableau.
   Ne pas ajouter de ports publiés. Ne cliquer sur **Deploy the stack**
   qu'après autorisation et neutralisation autorisée des entrées HA et autres
   producteurs susceptibles de démarrer une caméra.
5. Pour HA, sauvegarder la configuration et l'intégration existante.
   Dans GitHub **Actions → Test and publish Netvue Go bridge image → lancement
   réussi → Artifacts**, télécharger `netvue-local-<commit>` du même
   lancement que l'image. Extraire son contenu dans le dossier
   `<config HA>/custom_components/netvue_local/` ; `manifest.json` doit se
   trouver directement dans ce dossier. Remplacer l'intégration existante
   uniquement après sauvegarde et autorisation. Redémarrer HA uniquement après
   autorisation et vérification qu'aucune entrée existante ne peut relancer
   une caméra.

Après résolution du blocage et validation d'un dispositif borné, le parcours
prévu est **Paramètres → Appareils et services → Ajouter une intégration →
Netvue Local**, nom puis IPv4 de la caméra 2. Cet ajout lance immédiatement
la supervision : **ne pas l'effectuer pour l'instant**. Une fois l'essai
autorisé et borné par un mécanisme adéquat, contrôler l'image caméra, le
capteur de disponibilité RTSP, le capteur d'état de session et, uniquement
dans les limites autorisées, le bouton de reprise. Une reprise est une
nouvelle tentative, pas une simple consultation. Consulter les journaux
localement et les expurger avant partage.

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
NETVUE_SOURCE=192.0.2.10 \
docker stack config -c deployment/go-stack.yaml
```

Cette vérification ne contacte aucune caméra et ne valide pas l'accès au
registre, les permissions du serveur ni le démarrage effectif. Ne lancez
pas `docker stack deploy` dans le cadre d'une validation locale.
