# Phase 10 — pilote Swarm et validation réelle bornée

## Décision et état final

**PROUVÉ :** une stack Swarm synthétique puis un pilote réel ont fonctionné sur Debian. L'unique connexion autorisée à la caméra 2 a produit du H.264 lu sur Debian et dans Home Assistant. Le budget est consommé et bloqué ; aucune seconde tentative n'a été effectuée.

**État final :** `netvue-pilot_bridge` est à **0/0**, `netvue-pilot_mediamtx` à **1/1**, sur le nœud `debian-server`. MediaMTX écoute uniquement sur `127.0.0.1:8554`, sans port publié par Swarm. Le flux caméra n'est donc plus disponible. Aucun lecteur temporaire Home Assistant ni conteneur synthétique `nv10-*` ne reste.

**NON VÉRIFIÉ :** affichage Generic Camera dans l'interface, disponibilité effective de Stream dans la configuration Home Assistant, visibilité dans l'interface Portainer, fonctionnement prolongé et reconnexions réelles. **Installation permanente non activée** : le pont est arrêté et son budget ne peut pas être réutilisé. Toute nouvelle connexion exige une autorisation distincte.

## Audit et image

- **PROUVÉ :** Docker Engine **29.7.2**, architecture `amd64`, Swarm actif ; un nœud `debian-server`, Ready/Active/Leader. Home Assistant était actif en réseau host avant et après le test. Le port 8554 était libre avant installation. Mémoire disponible observée : environ 7 462 Mio ; disque disponible : 665 Gio.
- Les services existants étaient dans le même état de réplication avant et après le test ; notamment un service d'initialisation était déjà à 0/1 avant l'intervention. Aucun service existant n'a été modifié.
- Image locale `netvue-bridge:phase9-179f7438`, ID `sha256:0132828e026ed0ab71f9ec9c3305a5c2f73fba0cbe959e59da906a2cc4f9eebc`, Python 3.13.5 / PyAV 16.1.0, UID/GID 10001. Les empreintes de toutes les sources Python, de `requirements.txt`, du Dockerfile, de la stack, de MediaMTX et du scénario synthétique concordaient avec la copie Debian de phase 9 ; aucune reconstruction nécessaire.
- MediaMTX officiel `1.21.4`, digest Linux amd64 `sha256:456791fae0607e00d4f7105fd4048e1b9bb9c5cb43e0fe0cb55e3beeea023f15`. Aucune image n'a été publiée sur un registre.
- Un nouveau répertoire dédié `/home/mtx26/netvue-pilot-phase10-179f7438` contient la configuration copiée et le budget neuf ; aucun ancien état ni donnée vidéo réelle n'a été repris. Le répertoire parent est privé ; le répertoire d'état est accessible à l'UID 10001 via son bind mount.

## Validation synthétique Swarm

**PROUVÉ :** stack temporaire `nv10synthetic`, services épinglés au nœud et réseau host réellement appliqué. Inspection des conteneurs : UID 10001, système racine en lecture seule, `cap_drop ALL`, 0,5 CPU chacun, 256 Mio pour le pont et 128 Mio pour MediaMTX. Dans le pont : `memory.max=268435456`, `cpu.max=50000 100000`, `CapEff=0`. MediaMTX écoutait `127.0.0.1:8554` ; aucune publication Swarm. La cible synthétique était exclusivement une adresse loopback.

Un premier budget fictif et un watchdog de 2 s ont produit `watchdog: duration limit reached`, arrêt du travailleur et sortie de tâche **124**. Après quatre secondes, une seule tâche avait existé : `restart_policy: none` n'a pas relancé le pont. Un second budget fictif a permis le scénario vidéo :

- **220 unités publiées**, un changement SPS, un PPS, un changement de résolution et un retour de PTS à zéro.
- FFprobe : **160 paquets horodatés**, durée **15,903 s**, écart maximal **0,103 s** ; FFmpeg : **152 images**, résolutions **64 × 64** et **128 × 64**.
- Après fermeture de la caméra synthétique, le budget était `used=1`, `limit=1` ; le pont s'est arrêté sur épuisement. Un échantillon de charge indiquait environ **38 Mio / 0,91 % CPU** pour le pont et **17,51 Mio / 0,39 % CPU** pour MediaMTX.
- La stack et le conteneur fictifs ont été supprimés ; le port 8554 a été vérifié libre avant l'installation du pilote.

## Pilote réel et budget

Le pilote a été déployé avec l'image locale et `--resolve-image never`, une seule réplique de MediaMTX et le pont initialement à zéro. Les deux services sont contraints à `node.hostname == debian-server`. **PROUVÉ :** réseau host, UID 10001, racine en lecture seule, `cap_drop ALL`, limites configurées et appliquées dans le test Swarm ; la politique du pont est `restart_policy: none`. MediaMTX écoute sur `127.0.0.1:8554` en RTSP/TCP ; les protocoles inutiles et l'enregistrement sont désactivés dans [mediamtx.yml](../deployment/mediamtx.yml). L'inspection Swarm retourne `Endpoint.Ports=null`.

Le budget persistant a été initialisé avant tout contact, avec **une tentative maximum**. Le pont avait un watchdog de **62 s** ; son parent commence l'arrêt environ 5 s avant l'échéance. Le chronomètre de l'orchestrateur, du lancement de la tâche jusqu'à l'arrêt du pont et des lecteurs, a mesuré **58,528 s**, sous les 90 s autorisées.

**PROUVÉ par les journaux expurgés :**

| Action caméra 2 | Utilisée / autorisée |
|---|---:|
| Découverte UDP unicast | 1 / 1 |
| Connexion TCP | 1 / 1 |
| Demande d'authentification | 1 / 1 |
| Demande LIVE | 1 / 1 |
| Reconnexion | 0 / 0 |

Le login et LIVE ont retourné `result_code=0`. Deux confirmations LIVE ont été reçues, mais une seule demande a été envoyée. La durée découverte / TCP / authentification observée dans le pont est **0,016 / 0,002 / 0,042 s** ; la phase LIVE a duré **56,411 s**. La réception vidéo a couvert **56,025 s** entre première et dernière unité. Le watchdog s'est déclenché, le travailleur s'est arrêté et le pont a été mis à **0 réplique**. Le code de sortie de la tâche réelle n'a pas été conservé après cette réduction ; le code 124 a été vérifié séparément sur le service synthétique.

Après l'arrêt, le budget lu depuis un conteneur sans réseau indiquait **`used=1`, `limit=1`, `status=in_progress`**. Aucun budget n'a été réinitialisé ; l'état `in_progress` interdit une reprise automatique. La durée configurée est par exécution, pas une échéance globale persistante ; ne jamais contourner la limite par redéploiement manuel.

## Vidéo et Home Assistant

| Mesure | Debian | Home Assistant |
|---|---:|---:|
| Codec initial / résolution initiale | H.264 / 640 × 360 | H.264 / 640 × 360 |
| Paquets FFprobe / avec PTS | 375 / 374 | 375 / 374 |
| Images clés observées par FFprobe | 26 | 26 |
| Durée entre PTS disponibles | 44,983 s | 44,983 s |
| PTS strictement croissants / écart maximal | oui / 0,331 s | oui / 0,331 s |
| FPS moyen calculé sur les PTS | 8,29 | 8,29 |
| Images décodées par FFmpeg | 363 | 363 |
| Codes FFprobe / FFmpeg, lignes d'erreur | 0 / 0, aucune | 0 / 0, aucune |

Le pont a reçu **467 unités vidéo** et publié **467 images**, dont **34 IDR** ; aucun échec de décodage n'a été signalé. Un changement SPS, aucun changement PPS, un changement de résolution et une réinitialisation PTS ont été observés. Le raccord temporel du PTS a été estimé à **331 ms** d'après les arrivées. Le FFprobe d'images Debian a décodé les deux résolutions **640 × 360** et **1920 × 1080** ; cette observation détaillée n'a pas été répétée côté Home Assistant.

**DÉDUIT du code** [rtsp.py](../netvue_bridge/rtsp.py) : la publication avec IDR implique la présence préalable de SPS et PPS. **NON VÉRIFIÉ :** le nombre exact d'images perdues ; les compteurs d'entrée et ceux des lecteurs couvrent des fenêtres différentes et le protocole n'a pas fourni ici de comptage de pertes fiable. Les PTS sont reconstruits au raccord et ne prouvent pas l'exactitude de l'horloge native. La durée FFmpeg de sortie n'a pas été collectée séparément ; l'intervalle de lecture demandé était de 45 s.

L'intégration Stream est présente sur disque dans le conteneur Home Assistant, ce qui ne prouve pas qu'elle est configurée. Après autorisation, ajouter **Generic Camera** avec source `rtsp://127.0.0.1:8554/netvue2`, transport **TCP** et Stream configuré si aucune URL d'image fixe n'est fournie. FFmpeg dans le conteneur ne prouve pas l'affichage dans l'interface. Home Assistant n'a pas été modifié.

## Ressources et sécurité

Huit échantillons `docker stats` pendant l'essai réel : pont **39,65–40,48 Mio**, **0,77–1,07 % CPU** ; MediaMTX **17,54–25,08 Mio**, **0,26–1,24 % CPU**. Ce sont des valeurs instantanées, pas des pics garantis ; la consommation des lecteurs Home Assistant n'est pas incluse. Les limites initiales ont tenu pendant cet essai seulement.

**PROUVÉ :** seule l'écoute RTSP loopback apparaît sur le port 8554 ; aucun port Swarm n'est publié. Aucun pare-feu, routeur, tunnel, firmware ou compte cloud n'a été modifié. Les lecteurs ont écrit leurs sorties vidéo vers `null` ; seules des métadonnées ont été collectées en mémoire et rapportées. Aucune clé ni donnée vidéo n'apparaît dans les événements et métriques rapportés ; un audit exhaustif de tous les journaux n'a pas été effectué. Les flux Netvue et RTSP locaux ne sont pas chiffrés ; les processus locaux ou conteneurs host peuvent accéder à loopback. L'accès distant éventuel de Home Assistant dépend de sa propre configuration. La mémoire, le swap ou les dumps système restent des risques.

## Diagnostic, démarrage et arrêt

Les commandes suivantes ciblent uniquement le pilote créé ; elles nécessitent SSH et les droits Docker. Les diagnostics sont en lecture seule, n'initient pas de connexion caméra et peuvent exposer des informations d'infrastructure dans le terminal ; expurger leurs sorties avant partage.

```sh
docker stack services netvue-pilot
docker service inspect --pretty netvue-pilot_bridge
docker service inspect --pretty netvue-pilot_mediamtx
docker service ps netvue-pilot_bridge
docker service logs --tail 100 netvue-pilot_bridge
ss -H -ltn 'sport = :8554'
```

Le budget courant appartient à l'UID 10001 et reste dans le répertoire dédié. Ne pas le supprimer, restaurer, modifier ou réinitialiser. **Ne pas démarrer le pont pour contourner son état épuisé.** Après nouvelle autorisation, analyser l'ancien état et créer un budget séparé correspondant au nouveau plafond ; une durée autorisée globale doit aussi être contrôlée indépendamment des redéploiements. Les méthodes existantes sont détaillées dans [README.md](../deployment/README.md).

Pour arrêter le pilote sans toucher aux autres stacks, `docker service scale --detach=true netvue-pilot_bridge=0 netvue-pilot_mediamtx=0` interrompt les deux services et libère le port. Pour redémarrer uniquement MediaMTX sans contacter la caméra : `docker service scale --detach=true netvue-pilot_mediamtx=1`. Ces opérations modifient le pilote ; vérifier l'état et le port ensuite. La remise en marche du pont nécessite un nouveau budget et une autorisation explicite.

Retour arrière, si demandé : `docker stack rm netvue-pilot`, puis vérifier sa disparition et le port. Cette commande supprime uniquement les services de la stack ; elle n'efface ni le répertoire dédié ni le budget, qu'il faut conserver pour audit. Ne pas supprimer d'images, volumes ou services préexistants.

## Production et autorisations restantes

**A. Pilote validé :** oui, pour une seule session réelle bornée et les essais synthétiques décrits. **B. Production préparée :** architecture et configuration disponibles, mais politique d'exploitation continue à définir. **C. Production activée : non.** Le pont arrêté ne fournit pas de flux continu.

Une politique future devrait conserver le verrouillage et le budget durable, les délais progressifs 5/10/20/40/60 s, l'arrêt sur refus ou état ambigu, des alertes explicites et une reprise manuelle contrôlée. Ajouter une échéance persistante et une supervision adaptée avant tout fonctionnement durable ; ne pas activer de reconnexions illimitées. Mesurer les pics et la stabilité longue en conditions autorisées. Vérifier la visibilité Portainer et l'intégration Home Assistant avec les autorisations appropriées.
