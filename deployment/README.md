# Préparation Swarm Netvue

Les phases 10 et 11 ont chacune bénéficié d'une autorisation et d'un budget distincts. La phase 12 autorise un fonctionnement continu spécifique, avec ses propres plafonds : voir le [rapport de phase 12](../findings/phase12-continuous-operation.md). Ne jamais réutiliser un budget épuisé ou bloqué ; toute modification de Home Assistant nécessite une autorisation distincte.

## Mode continu phase 12

Le superviseur continu [continuous.py](../netvue_bridge/continuous.py) est distinct du mode borné décrit ci-dessous. La surcharge [stack-continuous.yaml](./stack-continuous.yaml) s'utilise avec [stack.yaml](./stack.yaml) : même si elle remplace la commande, `NETVUE_MAX_DURATION` doit rester défini pour l'interpolation du fichier de base. Swarm ne construit pas l'image ; utiliser une image locale préalablement testée et un identifiant immuable. Ne pas déployer une seconde stack MediaMTX ou un second pont pour la même caméra.

Le budget dédié enregistre durablement chaque réservation avant découverte : maximum trois tentatives en 24 h glissantes et huit en sept jours. Le verrou exclusif couvre toute la vie du superviseur ; il ne protège que les instances partageant le **même répertoire**. Le stockage doit rester sur le même nœud, sans copie ni restauration ancienne. Au plafond ou après refus, l'arrêt est durable ; aucun redémarrage ne réinitialise les compteurs. Après interruption récupérable et fermeture du travailleur, les délais aléatoires sont au minimum 30, 120, 300 secondes puis croissent jusqu'à 1 800 secondes. Après crash, arrêt du service ou état `in_progress`, la reprise est bloquée. Un changement de boot ou un saut d'horloge suspect bloque également la reprise, même après une fermeture récupérable : c'est une limite volontaire, pas une reprise automatique après redémarrage Debian.

Le parent surveille les progrès du travailleur et de la publication ; il arrête le travailleur s'il ne progresse pas pendant 30 s, sans dépendre de la présence d'un lecteur RTSP. Il vérifie le cgroup v2 et bloque à 95 % de mémoire allouée. Les limites Docker s'appliquent aux deux processus ensemble ; leur lecture doit être disponible. Une erreur de santé ambiguë laisse le budget `in_progress` et exige un examen. Le pont utilise `restart_policy: none` : en cas de crash ou d'arrêt, Swarm ne le relance pas automatiquement. Aucune procédure de déblocage ou de réconciliation après reboot n'est encore implémentée ; ne pas modifier manuellement le budget pour contourner ce verrou.

La correction expérimentale des horodatages identiques est limitée aux images H.264 distinctes dont la séparation et la cadence peuvent être contrôlées ; les fragments ou situations ambiguës restent bloquants. Les métriques incluent `same_timestamps`, `timestamp_corrections`, `ignored_frames`, `unresolved_timestamp_frames`, `max_frame_gap_ms`, `estimated_fps` et `last_valid_timestamp`. Le rapport [phase12b-timestamp-recovery.md](../findings/phase12b-timestamp-recovery.md) détaille les limites et les validations synthétiques. Cette version n'a pas été validée sur la caméra réelle.

La stack `netvue-pilot` existante a été mise à jour par `docker service update`, sans redéployer MediaMTX ; l'ancienne spécification est conservée sur Debian. La configuration enregistrée dans Portainer peut donc être différente du service effectif : ne pas redéployer depuis Portainer sans réconcilier la configuration et préserver le budget courant. Les journaux `continuous_health` exposent uniquement des métriques ; les journaux complets doivent tout de même être expurgés avant partage. Le transport Netvue et RTSP loopback n'est pas chiffré.

## Architecture et prérequis

Sur un unique nœud Linux `amd64`, le pont Python découvre et authentifie la caméra, reçoit H.264 et publie en RTSP/TCP sur `127.0.0.1:8554/netvue2`. MediaMTX retransmet localement ; Home Assistant utilise le réseau host du même nœud. Les deux services utilisent le réseau externe Docker `host` : `network_mode: host` et `build:` ne sont pas utilisables ici avec `docker stack deploy`. Aucun port Swarm n'est publié.

Prérequis : Docker Swarm actif, Portainer connecté au même Swarm, Home Assistant sur ce nœud en réseau host, port TCP 8554 libre, accès du nœud à la caméra, système de fichiers local persistant avec `flock`, `rename` atomique et `fsync`, droits de préparation des répertoires et accès à un registre d'images autorisé. Le point de montage doit rester sur **le même nœud et le même chemin**. Ne pas utiliser plusieurs répliques ou plusieurs stacks pour la même caméra.

Le compte `10001:10001` doit pouvoir écrire dans le répertoire d'état et lire la configuration MediaMTX. Les images doivent être disponibles sur le nœud avant le déploiement. La configuration et le budget sont des bind mounts : Portainer ne les transfère pas automatiquement. Si le démon utilise un remappage d'utilisateurs, vérifier les permissions correspondantes. Les noms d'hôte et chemins de [example.env](./example.env) sont des exemples, pas des secrets ; les adresses `192.0.2.*` ne sont pas celles de la caméra.

Les services utilisent une racine en lecture seule et abandonnent leurs capacités. La conversion Swarm de Docker CLI 29.7.2 prend en charge ces champs ; `security_opt` est ignoré par `docker stack deploy` et n'est pas revendiqué ici. L'exécution non-root et ces contraintes ont été vérifiées dans des conteneurs Docker temporaires, pas dans des services Swarm. Le pont ne reçoit ni Docker socket ni privilège `privileged`.

## Budget et limites impératives

Le budget est **absent par défaut** : le pont échoue sans contacter la caméra tant qu'il n'a pas été initialisé explicitement. Il compte chaque tentative avant la découverte UDP, y compris si aucune connexion TCP n'a lieu. La réservation et l'état sont persistés sur disque sous verrou exclusif, avec `fsync` ; une interruption transitoire après découverte, connexion ou pendant LIVE peut être suivie d'un nouvel essai. Les délais sont 5, 10, 20, 40 puis 60 secondes maximum. Le délai recommence intégralement après redémarrage. Un refus ou une erreur non réessayable laisse l'état `blocked`. Un crash, SIGTERM ou une interruption pendant la tentative laisse `in_progress` : toute reprise s'arrête pour examen, même s'il reste des tentatives. Un état invalide, absent, incompatible ou destiné à une autre paire d'adresses entraîne aussi un arrêt.

Le pont n'est **pas redémarré automatiquement** par Swarm. MediaMTX dispose de trois redémarrages sur erreur ; le pont ne démarre pas ses tentatives si aucun processus n'écoute sur le port local pendant les 15 secondes initiales. Ce contrôle TCP ne prouve pas l'identité ni la bonne santé de MediaMTX : vérifier qu'aucun autre processus n'occupe le port avant déploiement. Si MediaMTX démarre trop tard, le pont s'arrête sans consommer de budget.

Le plafond est local à **un seul fichier** : plusieurs copies, une restauration d'un ancien instantané, une suppression ou modification manuelle du budget pourraient le contourner. Ne jamais le réinitialiser automatiquement. Conserver l'état courant et les éléments d'audit ; si une nouvelle autorisation accorde un nouveau budget, arrêter la stack et archiver l'ancien répertoire avant de préparer un répertoire neuf. Ne pas restaurer un ancien budget sur le chemin actif. Une tentative bloquée ne se débloque pas automatiquement.

`NETVUE_MAX_DURATION` est obligatoire : durée maximale en secondes pour une exécution entière, démarrage et tentatives compris, comprise entre 0 et 604800, sans inclure 0. Un processus parent indépendant arrête le travailleur par SIGTERM avant l'échéance, avec une marge de 25 % de la durée, plafonnée à 5 s ; il force uniquement ce processus à s'arrêter si nécessaire. Code 124 à échéance. L'arrêt normal ou forcé d'une tentative active laisse `in_progress`. La garantie dépend du système : une tâche bloquée dans le noyau ou l'ordonnancement peuvent retarder la fin effective. En conteneur, la fin de PID 1 entraîne aussi la fin du conteneur.

La limite est **par exécution**, pas une échéance absolue persistante : toute relance manuelle repart de zéro. Ne relancer qu'après examen de l'état et vérification de la durée et du budget encore autorisés ; ne pas utiliser directement l'action interne `_worker`. Une autorisation doit préciser l'appareil, les opérations, le nombre maximal de tentatives, la durée maximale et les modifications permises. Ne pas confondre nombre de tentatives et durée.

## Procédure future, uniquement après autorisation

Les commandes ci-dessous sont destinées au terminal **Debian**, depuis la racine d'une copie du projet. Elles modifient le serveur ou démarrent des services ; ne pas les exécuter pendant la présente phase.

1. **Préparation et précontrôles.** Vérifier l'identité du nœud (`docker node ls`), le réseau host (`docker network inspect host`), le mode réseau de Home Assistant et l'absence de conflit : `ss -H -ltn 'sport = :8554'`. Les précontrôles sont en lecture seule et ne contactent pas la caméra. S'arrêter si le port est occupé, si un autre pont utilise cette caméra, ou si le nœud ne correspond pas. Les commandes Docker exigent les droits d'accès au démon ; leur sortie peut révéler des informations d'infrastructure.
2. **Fichiers persistants.** Après avoir vérifié que les chemins n'existent pas déjà, créer `/srv/netvue-bridge/state` appartenant à `10001:10001`, mode `0700`, et copier [mediamtx.yml](./mediamtx.yml) vers `/srv/netvue-bridge/mediamtx.yml`, lisible par cet UID mais non modifiable par lui. Par exemple, avec les droits administrateur et après contrôle de l'absence des chemins :

   ```sh
   sudo install -d -m 0755 /srv/netvue-bridge
   sudo install -d -o 10001 -g 10001 -m 0700 /srv/netvue-bridge/state
   sudo install -m 0444 deployment/mediamtx.yml /srv/netvue-bridge/mediamtx.yml
   ```

   Risques : création de fichiers et possible écrasement si le contrôle préalable est ignoré. Ne jamais remplacer un budget existant. Vérifier également l'absence d'enregistrement et les paramètres après toute mise à jour MediaMTX.
3. **Image.** Construire séparément sur le nœud avec `docker build --pull -f deployment/Dockerfile -t netvue-bridge:phase9 .`. Prérequis : accès aux registres officiels et à PyPI ; résultat : image locale, aucune connexion caméra. Risques : téléchargement et stockage d'images sur le serveur. Le fichier `Dockerfile.dockerignore` limite le contexte aux sources Python et au Dockerfile. Le Dockerfile épingle l'image Python Linux amd64 et PyAV 16.1.0 ; la construction et `pip check` ont réussi en phase 9. Pour plusieurs nœuds, utiliser un registre privé autorisé et une image immuable, mais **ne pas déplacer cette stack ni son budget**.
4. **Budget.** Après accord sur le nombre `N` et la durée, initialiser une seule fois depuis le même nœud, en remplaçant `N` par le plafond autorisé :

   ```sh
   docker run --rm --network none --read-only --cap-drop ALL --user 10001:10001 \
     --mount type=bind,src=/srv/netvue-bridge/state,dst=/state \
     netvue-bridge:phase9 init --source 192.168.129.159 --target 192.168.129.2 \
     --state-dir /state --limit N
   ```

   Le conteneur temporaire n'a aucun réseau ; il écrit `budget.json` et `budget.lock` dans le répertoire persistant, sans contacter la caméra. Il refuse un budget déjà existant.
5. **Variables et déploiement.** Renseigner les sept variables de [example.env](./example.env) avec les vraies valeurs, notamment `NETVUE_SOURCE=192.168.129.159`, `NETVUE_TARGET=192.168.129.2`, la durée `NETVUE_MAX_DURATION`, le nom du nœud, l'image construite et les deux chemins absolus. Ne pas renseigner de mot de passe ni de clé. Le CLI `docker stack deploy` ne lit pas automatiquement ce fichier : exporter ces variables dans le shell, puis vérifier `docker stack config -c deployment/stack.yaml`. **Seulement après autorisation explicite des connexions et du déploiement**, lancer `docker stack deploy --resolve-image never -c deployment/stack.yaml netvue`. Cette commande crée des services permanents et peut contacter la caméra ; `--resolve-image never` permet au nœud unique d'utiliser l'image locale préparée.
6. **Portainer Swarm, alternative au CLI.** Dans l'environnement Swarm, créer une stack `netvue`, fournir le contenu de [stack.yaml](./stack.yaml) et renseigner les mêmes variables dans l'interface Portainer. Ne pas compter sur `env_file` ou sur la construction d'image par Portainer. Les bind mounts et images doivent déjà exister sur le nœud. Le déploiement depuis Portainer et la résolution de l'image locale restent à vérifier en conditions autorisées. Ne pas créer simultanément la stack par les deux méthodes.

## Surveillance, Home Assistant et sécurité

`docker stack services netvue`, `docker service ps netvue_bridge`, `docker service ps netvue_mediamtx` et `docker service logs --tail 100 netvue_bridge` permettent de constater l'état et les erreurs sans initier d'essai supplémentaire. Portainer et `docker stats --no-stream <ID_DU_CONTENEUR>` permettent de suivre CPU, mémoire et redémarrages. Configurer une alerte externe sur service arrêté, budget bloqué ou épuisé et absence de flux ; aucune alerte automatique n'est incluse. Examiner et expurger les journaux avant tout partage.

Les plafonds initiaux sont 0,5 CPU / 256 Mio pour le pont et 0,5 CPU / 128 Mio pour MediaMTX. Ce sont des hypothèses : la phase 7 courte mesurait environ 51 284 et 54 284 Kio RSS au maximum, sans mesurer les pics longs ni les décodeurs Home Assistant. Surveiller les limites cgroup, OOM, redémarrages et CPU en situation autorisée avant ajustement.

Après autorisation séparée, configurer Generic Camera avec `rtsp://127.0.0.1:8554/netvue2` et transport TCP. Le décodage H.264 réel depuis le conteneur Home Assistant a été observé en phase 7 ; la disponibilité actuelle du flux, l'intégration Generic Camera, l'aperçu et Stream ne sont pas validés. Sans URL d'image fixe, Stream est nécessaire pour les aperçus selon la documentation Home Assistant. Tester l'interface, le démarrage et le changement de résolution après autorisation, sans supposer que FFprobe suffit.

MediaMTX n'écoute que sur loopback pour RTSP/TCP ; les autres protocoles, dont MoQ, et l'enregistrement sont désactivés. Tout processus local ou conteneur en réseau host peut néanmoins lire ou publier sur ce chemin sans mot de passe. Le réseau Netvue local et ce RTSP ne sont pas chiffrés. Home Assistant peut exposer le flux à ses utilisateurs autorisés, y compris à distance selon sa configuration. Le swap, les dumps, les journaux ou les fonctions Home Assistant peuvent conserver des données : contrôler ces risques séparément. Aucun port Internet n'est volontairement publié, mais la configuration réseau et l'accès distant de l'hôte restent sous la responsabilité de l'administrateur.

## Mise à jour, arrêt et retour arrière

Avant une mise à jour, conserver les versions d'images, la configuration et **l'état courant** ; vérifier les changements de paramètres par rapport à la version épinglée. Construire une nouvelle image avec un nouveau tag, tester hors production, puis modifier la variable de stack uniquement après accord. `stop-first` évite le chevauchement pendant la mise à jour ; l'arrêt d'une session active laisse toutefois le budget `in_progress`, donc la nouvelle tâche refuse de reconnecter sans nouvelle décision autorisée. Un rollback ne doit pas restaurer un ancien budget.

Après accord, `docker stack rm netvue` supprime les services de cette stack sans supprimer le bind mount ; vérifier ensuite leur disparition et l'écoute du port. Risque : interruption du flux ; une session arrêtée reste bloquée pour examen. Pour revenir en arrière, conserver la stack arrêtée, analyser l'état, les journaux expurgés et l'ancienne configuration, puis ne restaurer les services antérieurs et ne préparer un nouveau budget que selon une autorisation explicite. Ne pas modifier ni arrêter les autres services.

Références : [Docker — services et réseau host](https://docs.docker.com/engine/swarm/services/#connect-the-service-to-an-overlay-network), [Docker — stack deploy](https://docs.docker.com/engine/swarm/stack-deploy/), [Portainer — ajout d'une stack](https://docs.portainer.io/user/docker/stacks/add), [Home Assistant — Generic Camera](https://www.home-assistant.io/integrations/generic/).
