# Phase 8 — préparation Docker Swarm / Portainer

## Décision

**PROUVÉ :** le client local Docker accepte le rendu de la stack ; la configuration MediaMTX finale accepte un flux H.264 synthétique, et l'exécutable Linux testé n'écoute que sur `127.0.0.1:8554` en TCP. Le budget persistant et les arrêts ont été testés hors ligne. La phase 7 a déjà prouvé une lecture H.264 réelle courte, pas une stabilité longue.

**NON VÉRIFIÉ :** construction de l'image, lancement effectif sous Docker Swarm ou Portainer, fonctionnement non-root et lecture seule *dans les conteneurs*, limites cgroup, pics longs, disponibilité actuelle du flux et intégration Generic Camera/Stream. Aucun nouveau contact caméra, déploiement ou service permanent n'a été effectué. La stack est préparée pour revue, pas encore certifiée pour production.

## Architecture et fichiers

- [Dockerfile](../deployment/Dockerfile) : Python 3.13.5 Debian slim, image Linux amd64 épinglée par digest, PyAV 16.1.0, `pip check`, UID/GID 10001, exécution directe en PID 1, aucun secret embarqué. [Dockerfile.dockerignore](../deployment/Dockerfile.dockerignore) réduit le contexte aux sources utiles. La contrainte existante [requirements.txt](../netvue_bridge/requirements.txt) autorise cette version ; PyPI publie une wheel `cp313` manylinux x86-64.
- [stack.yaml](../deployment/stack.yaml) : deux services, une réplique chacun, affinité même nœud, réseau Docker `host` externe, aucun port publié ; bind mount de configuration en lecture seule et état persistant séparé. Le pont est construit séparément. Le client Docker 29.9.2 a rendu la stack avec `docker stack config` ; ce test ne réalise aucun déploiement.
- [mediamtx.yml](../deployment/mediamtx.yml) : RTSP/TCP loopback uniquement, MoQ, RTMP, HLS, WebRTC, SRT, API, métriques, pprof et playback désactivés ; enregistrement désactivé, chemin limité à `netvue2`, remplacement du publieur actif interdit. Aucun mot de passe : tout processus de l'hôte autorisé à joindre loopback reste capable de lire ou publier.
- [example.env](../deployment/example.env) : paramètres non secrets et adresses documentaires. [README.md](../deployment/README.md) : étapes exactes, Portainer, surveillance, arrêt et retour arrière.
- [supervisor.py](../netvue_bridge/supervisor.py) : budget et contrôle des reprises. [stream.py](../netvue_bridge/stream.py) : chaque appel du superviseur n'effectue qu'une tentative et signale uniquement les interruptions transitoires hors authentification.

## Compatibilité et réseau

**OBSERVÉ :** Debian avait un Swarm actif avec un seul manager `debian-server`, architecture `x86_64`, et un réseau `host` local ; aucune stack n'a été créée. Docker autorise les services Swarm sur ce réseau ; son CLI 29.7.2 convertit `read_only`, `cap_drop`, ressources et délai d'arrêt dans la spécification Swarm, tandis que `network_mode`, `build` et `security_opt` sont non pris en charge. Ces deux éléments sont documentés dans le [code officiel de conversion](https://raw.githubusercontent.com/docker/cli/v29.7.2/cli/compose/convert/service.go) et les [options non prises en charge](https://raw.githubusercontent.com/docker/cli/v29.7.2/cli/compose/types/types.go).

**DÉDUIT :** l'espace réseau host commun permet la découverte UDP unicast, puis le partage de l'URL loopback avec Home Assistant placé sur le même nœud. L'écoute locale n'isole pas des autres utilisateurs et conteneurs host. La présence d'un autre service sur 8554 peut tromper le contrôle de disponibilité TCP du pont : contrôler ce port avant lancement. Portainer ne transfère ni image locale ni bind mount ; la résolution d'image par Portainer reste à vérifier.

**Rectification historique :** MediaMTX 1.21.4 active MoQ par défaut ; l'ancienne configuration de phase 7 ne le désactivait pas. Les fichiers `auto.crt` et `auto.key` ont été observés après les essais, sans mesure de l'accessibilité des ports MoQ. La correction est ajoutée dans [mediamtx.local.yml](../netvue_bridge/mediamtx.local.yml), et le [rapport de phase 7](./phase7-real-validation.md) est annoté. Aucune exposition externe passée n'est affirmée.

## Budget, fermeture et risques

Le fichier `budget.json`, absent initialement, doit être créé explicitement avec le maximum autorisé ; le verrou `flock` exclut deux superviseurs sur le même fichier. Chaque tentative est réservée et synchronisée avant la découverte. Après erreur transitoire, délais de 5, 10, 20, 40 puis 60 s au maximum ; le compteur persiste entre processus. Un refus ou une erreur terminale reste `blocked`. Un crash ou arrêt ambigu laisse `in_progress`, empêchant toute reprise automatique ; un budget absent, corrompu ou sur une autre paire d'adresses échoue fermé. Le service pont a `restart_policy: none`.

Le budget ne protège ni contre une copie ancienne restaurée, ni contre la suppression ou modification manuelle, ni contre un second fichier ou nœud : interdire ces contournements. Les sockets sont fermées par leurs contextes et le publieur par `finally` ; SIGTERM est converti en interruption. Une opération native bloquée, un SIGKILL ou le délai Swarm peuvent empêcher une fermeture gracieuse, mais l'état de tentative reste bloquant. Les tampons H.264 de démarrage et de changement sont plafonnés à 4 Mio / 128 unités chacun ; les paquets protocole à moins de 1 Mio. Ils ne constituent pas une limite de mémoire globale pour les décodeurs.

**Limite de sécurité opérationnelle :** la durée d'une session active n'est pas limitée par le superviseur. Un test réel à durée finie exige d'abord un arrêt indépendant borné et validé ou une modification testée ; le plafond de tentatives ne remplace pas cette limite. Le transport Netvue local et RTSP loopback sont non chiffrés. Pas d'enregistrement MediaMTX volontaire, mais swap, dumps et fonctionnalités Home Assistant restent des risques. Les journaux sont réduits à des étapes, types d'erreurs et métriques ; les journaux de bibliothèques et services doivent être vérifiés avant partage.

## Résultats hors ligne et ressources

- Windows, environnement PyAV 16.1.0 et outils portables : **27 tests réussis sur 32**, cinq tests Linux du superviseur ignorés. Avec la configuration de déploiement, la caméra synthétique a publié 55 images ; FFprobe a observé 20 PTS et FFmpeg a décodé le flux. Un test dédié vérifie la fermeture de la socket et du publieur lors d'une interruption.
- WSL Linux : **5 tests du superviseur réussis** : budget persistant, verrou, corruption, crash ambigu, refus, épuisement, backoff et SIGTERM. Aucun réseau caméra.
- MediaMTX 1.21.4 Linux, archive vérifiée par SHA-256 : lancement temporaire en utilisateur non-root dans un répertoire non inscriptible, configuration finale, `ss` : une seule écoute TCP `127.0.0.1:8554`, aucun fichier créé, arrêt SIGTERM code 0. Ce n'est pas un test de montage Docker en lecture seule.
- Le client Docker local ne dispose pas de moteur : l'image n'a pas été construite et les contraintes du conteneur n'ont pas été mesurées. Le rendu Swarm a été validé, pas l'orchestration réelle. Les adresses de documentation `192.0.2.*` ont été utilisées pour le rendu et les tests.
- Plafonds hypothétiques : pont 0,5 cœur / 256 Mio, MediaMTX 0,5 cœur / 128 Mio. En phase 7, pics RSS échantillonnés de 51 284 et 54 284 Kio pendant un essai court ; ces chiffres ne garantissent pas la tenue de ces limites. Surveiller CPU, mémoire cgroup, OOM, flux et redémarrages ; aucune alerte automatique n'est installée.

### Reproduction hors ligne

Depuis la racine du projet sous Windows, avec WSL et les outils portables déjà présents :

```powershell
wsl --exec python3 -B -W error::ResourceWarning -m unittest discover -s tests -p test_supervisor.py -v
$env:NETVUE_MEDIAMTX = (Resolve-Path tools\mediamtx-win64-1.21.4\extracted\mediamtx.exe).Path
$env:NETVUE_FFPROBE = (Get-ChildItem tools\ffmpeg-win64-n8.1 -Recurse -Filter ffprobe.exe | Select-Object -First 1).FullName
& tools\venv-bridge-win\Scripts\python.exe -B -W error::ResourceWarning -m unittest discover -s tests -v
```

Finalité : vérifier les reprises et le flux synthétique ; prérequis : WSL, PyAV et les exécutables portables ; résultats : messages de tests et processus locaux temporaires. Risques : utilisation temporaire de sockets loopback et de CPU, aucune caméra réelle ni fichier vidéo permanent.

## Déploiement envisagé et Home Assistant

La procédure complète et les prérequis sont dans [README.md](../deployment/README.md) : contrôler le nœud et le port, préparer les chemins persistants et permissions, construire l'image, initialiser un budget neuf avec un conteneur sans réseau, définir les variables, puis déployer via le CLI Swarm ou Portainer. Le démarrage de la stack peut immédiatement contacter la caméra. Le retour arrière arrête la stack sans supprimer l'état ; il ne restaure jamais une ancienne version du budget.

**PROUVÉ en phase 7 :** H.264 décodé depuis le conteneur Home Assistant. **NON VÉRIFIÉ :** disponibilité actuelle et affichage réel par Generic Camera, aperçu sans image fixe, Stream et gestion des changements de résolution dans l'interface. L'URL prévue est `rtsp://127.0.0.1:8554/netvue2` en TCP ; selon la [documentation Generic Camera](https://www.home-assistant.io/integrations/generic/), Stream est nécessaire pour les aperçus sans URL d'image fixe. L'accès distant éventuel de Home Assistant dépend de sa configuration et de ses utilisateurs.

## Autorisations nécessaires avant toute suite

1. Préparation et construction d'images sur Debian, création des chemins, puis tout déploiement ou modification de services.
2. Nouveau test ou fonctionnement caméra 2 : nature, durée maximale, nombre maximum de tentatives et modifications permises ; les deux essais de phase 7 sont épuisés.
3. Solution d'arrêt borné validée avant un essai limité dans le temps, puis surveillance et mesures des ressources en conditions réelles.
4. Toute modification de Home Assistant et vérification depuis son interface ; toute modification réseau, du firmware ou opération destructive nécessite son propre accord.
