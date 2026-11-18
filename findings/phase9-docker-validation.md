# Phase 9 — validation Docker Debian sans caméra réelle

## Décision

**PROUVÉ :** l'image du pont est construite sur Debian et fonctionne dans Docker ; la chaîne complète avec trois conteneurs et une caméra synthétique isolée a publié et décodé du H.264, y compris un changement SPS/PPS, une résolution différente et un retour du PTS à zéro. Les 36 tests automatisés ont réussi dans un conteneur Linux. Aucun appareil Netvue n'a été contacté ; aucune stack n'a été déployée.

**NON VÉRIFIÉ :** l'application effective des options par un *service Swarm*, Portainer, les contraintes host en service Swarm, la résolution Portainer de l'image locale, les pics et la stabilité prolongée, une reconnexion réelle et l'intégration Generic Camera. La stack n'est **pas encore prête à être déclarée installable en production** : il faut une validation Swarm/Portainer autorisée, une autorisation caméra distincte et une décision sur l'exploitation continue.

## Image, dépendances et environnement

Docker Debian 29.7.2, `x86_64`, Swarm actif et manager, réseau Docker `host` local. Aucun service ou stack existant n'a été modifié. Image locale `netvue-bridge:phase9-179f7438`, ID `sha256:0132828e026ed0ab71f9ec9c3305a5c2f73fba0cbe959e59da906a2cc4f9eebc`, construite depuis [Dockerfile](../deployment/Dockerfile) avec Python **3.13.5**, PyAV **16.1.0**, `pip check` réussi et utilisateur **10001:10001**. L'image n'a pas été publiée. L'image officielle MediaMTX 1.21.4 épinglée par digest a été téléchargée localement.

La copie de développement `/tmp/netvue-phase9-179f7438` est conservée sur Debian ; elle contient les sources de test et budgets synthétiques, pas d'enregistrement vidéo. Les empreintes SHA-256 de [supervisor.py](../netvue_bridge/supervisor.py) et [docker_synthetic.py](../tests/docker_synthetic.py) sont identiques entre les copies locale et Debian. Les images et outils précédents n'ont pas été supprimés.

## Limite de durée et budget

[supervisor.py](../netvue_bridge/supervisor.py) lance désormais un processus fils contrôlé par un parent indépendant. L'option obligatoire `--max-duration` borne **l'exécution entière**, initialisation et attentes comprises ; [stack.yaml](../deployment/stack.yaml) la reçoit depuis `NETVUE_MAX_DURATION`. Le parent envoie SIGTERM avant la fin, avec une marge de 25 % de la durée, plafonnée à 5 s, puis SIGKILL uniquement au fils s'il ne s'arrête pas. Échéance : code 124. Une demande SIGTERM externe est transmise au fils ; dans Docker le code observé est 143. Le travailleur ferme ses sockets et le publieur par les blocs de fermeture existants.

Le format et la gestion du budget n'ont pas été modifiés. Une tentative interrompue en cours reste `in_progress` et empêche la reprise ; l'état peut rester `ready` si aucun essai n'a commencé. En Docker sans réseau, un budget initialisé a été retrouvé par un autre conteneur ; après une tentative synthétique, `used=1`, `limit=1`, puis un nouveau processus a refusé la reprise. Un fichier absent a entraîné un échec explicite avant découverte.

**Limites :** la durée repart de zéro à chaque exécution ; elle n'est pas une échéance globale persistante. Un redéploiement manuel exige de vérifier le temps et le budget encore autorisés. L'action interne `_worker` ne doit pas être invoquée directement. L'ordonnancement ou une tâche bloquée dans le noyau peuvent retarder la fin effective ; le dispositif ne constitue pas une garantie temps réel. Le comportement en cas de perte du parent hors conteneur n'est pas établi. Le pont n'est pas redémarré automatiquement dans la stack ; la limite impose donc un arrêt de service, pas une exploitation continue sans intervention.

## Conteneurs, sécurité et arrêt

- Conteneur de contrôle pont : réseau `none`, racine réellement non inscriptible (`EROFS`), volume d'état inscriptible, UID 10001, `CapEff=0`, toutes capacités retirées. Cgroup : `memory.max=268435456`, `cpu.max=50000 100000` ; limites Docker demandées 256 Mio et 0,5 CPU.
- MediaMTX : UID/GID 10001, racine en lecture seule, toutes capacités retirées ; limites inspectées 128 Mio et 0,5 CPU. Dans son espace réseau isolé, `/proc/net/tcp`, `tcp6`, `udp`, `udp6` ne montraient qu'une écoute TCP `127.0.0.1:8554`. Les autres protocoles et l'enregistrement sont désactivés dans [mediamtx.yml](../deployment/mediamtx.yml). SIGTERM : sortie 0, `OOMKilled=false`.
- Pont réel en précontrôle isolé : PID 1 parent et fils identifiés avec `docker top`, arrêt Docker SIGTERM, messages `watchdog: stop requested` puis `supervisor: stopped`, code 143. Test séparé : fils fictif ignorant SIGTERM arrêté de force dans la limite de test ; aucun autre processus ciblé. Un test utilise une socket locale pour vérifier sa fermeture et celle du publieur.
- Les trois conteneurs de la chaîne partageaient un espace réseau **`none`**, pas le réseau de l'hôte ; aucune publication de port. L'hôte n'écoutait pas sur 8554 au contrôle final. Aucun conteneur nommé `nv9-*` ne restait après nettoyage ciblé. Les journaux du test ne contenaient que des valeurs synthétiques et des métriques ; ils doivent toujours être examinés avant partage.

Les services du projet n'enregistrent pas volontairement de vidéo ; swap, dumps et mécanismes de Home Assistant restent des risques. Les transports Netvue local et RTSP loopback ne sont pas chiffrés. En réseau host, tout processus local autorisé peut accéder à loopback : l'isolation testée ici ne prouve pas une isolation entre services de production.

## Chaîne synthétique et ressources

Caméra synthétique loopback → pont Docker → MediaMTX Docker → FFprobe/FFmpeg dans le conteneur de test, sans réseau vers le LAN. [docker_synthetic.py](../tests/docker_synthetic.py) utilise uniquement `127.0.0.1` et `127.254.9.9` ; la clé et les identifiants sont fictifs.

| Mesure | Résultat observé |
|---|---:|
| Unités reçues / publiées par le pont | 220 / 220 |
| Changements SPS / PPS / résolution | 1 / 1 / 1 |
| Retour PTS à zéro / raccord estimé | 1 / 103 ms |
| FFprobe : paquets horodatés / durée / écart maximal | 160 / 15,903 s / 0,103 s |
| FFmpeg : images décodées | 152 |
| Dimensions décodées | 64 × 64 et 128 × 64 |
| Caméra synthétique / pont : codes de sortie | 0 / 1 |

Le code 1 du pont est attendu : la caméra synthétique ferme sa socket, l'interruption est classée réessayable, puis le budget d'une tentative est épuisé. Les PTS reconstruits sont monotones ; leur raccord estimé n'est pas une preuve d'exactitude de l'horloge native.

Trois échantillons `docker stats` pendant le test final : pont **38,00–38,07 Mio**, **0,62–0,88 % CPU** ; MediaMTX **15,61–15,69 Mio**, **0,23–0,37 % CPU**. Ce sont des valeurs instantanées approximatives, pas des pics garantis ; la caméra et les lecteurs synthétiques ont leurs propres coûts non inclus. Les limites initiales 256/128 Mio et 0,5 CPU ont tenu pour ce test court seulement.

Le premier essai multi-conteneurs utilisait 27 s : la marge de 5 s a arrêté le pont pendant les dernières images synthétiques, provoquant un `BrokenPipeError` attendu côté émetteur mais un échec du scénario. Une durée de 32 s, toujours bornée et sans caméra, a permis au scénario de se terminer ; la reproduction finale a réussi avec l'image reconstruite.

## Reproductibilité

Les commandes suivantes servent uniquement aux données synthétiques ; elles nécessitent Docker sur Debian et les fichiers de phase 9 et outils portables encore présents aux chemins indiqués. Elles créent des conteneurs temporaires et fichiers de budget fictifs, consomment CPU/mémoire et n'ouvrent aucun port hôte. Ne pas substituer une adresse caméra réelle.

Tests automatisés complets, dans un conteneur isolé :

```sh
D=/tmp/netvue-phase9-179f7438
T=/tmp/netvue-phase7.vIUhl6/tools
docker run --rm --network none --read-only --cap-drop ALL \
  --memory 512m --cpus 1 --tmpfs /tmp:rw,size=64m \
  --mount type=bind,src="$D/tests",dst=/workspace/tests,readonly \
  --mount type=bind,src="$D/netvue_bridge",dst=/workspace/netvue_bridge,readonly \
  --mount type=bind,src="$D/deployment",dst=/workspace/deployment,readonly \
  --mount type=bind,src="$T/ffmpeg-btbn",dst=/ffmpeg,readonly \
  --mount type=bind,src="$T/mediamtx-1.21.4/mediamtx",dst=/mediamtx,readonly \
  -e NETVUE_MEDIAMTX=/mediamtx -e NETVUE_FFPROBE=/ffmpeg/ffprobe \
  --entrypoint python netvue-bridge:phase9-179f7438 \
  -B -W error::ResourceWarning -m unittest discover -s /workspace/tests -v
```

Le scénario multi-conteneurs utilise le même [fichier synthétique](../tests/docker_synthetic.py) : créer un budget neuf pour `127.0.0.1` → `127.254.9.9`, démarrer MediaMTX avec `--network none`, puis les conteneurs synthétique et pont avec `--network container:<conteneur-mediamtx>`, sans `-p`. Monter le fichier de configuration MediaMTX en lecture seule, le répertoire FFmpeg portable en lecture seule, et un répertoire d'état neuf sur `/state`. Initialiser avec `--limit 1`, attendre le fichier `camera.ready`, puis démarrer le pont avec `--max-duration 32`. Collecter `docker logs` et `docker stats`, puis supprimer exclusivement ces trois conteneurs temporaires. Cette méthode nécessite des noms et répertoires neufs ; elle ne doit pas être dirigée vers une caméra.

## Swarm et prochaines étapes

`docker stack config` a réussi sur Debian avec des valeurs d'exemple et la limite de durée ; l'image finale existe sur ce nœud. Les options de la stack et le réseau host externe restent des **déductions documentées**, pas une preuve d'exécution Swarm : aucun `docker stack deploy`, service Swarm de test ou changement Portainer n'a été réalisé. Les politiques de redémarrage déclarées n'ont donc pas été observées dans Swarm.

Avant toute installation permanente : obtenir l'autorisation de créer la stack, vérifier son rendu et ses contraintes sur le nœud, décider comment gérer l'arrêt à échéance sans contourner le budget, surveiller les ressources et les erreurs, puis obtenir une autorisation caméra précisant appareil, opérations, nombre de tentatives et durée. L'intégration et les modifications Home Assistant nécessitent aussi une autorisation distincte. La procédure envisagée figure dans [README.md](../deployment/README.md).
