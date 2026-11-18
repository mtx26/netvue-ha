# Phase 12 bis — horodatages et reprise

## Décision

**PROUVÉ :** une correction expérimentale traite certains horodatages identiques entre images H.264 distinctes. Les validations synthétiques décrites ci-dessous réussissent, sans contact avec les caméras ni modification des budgets réels.

**NON VÉRIFIÉ :** la nature du doublon réel, la correction sur cette caméra, la lecture via Home Assistant avec la nouvelle image et la stabilité 24 h/24. La correction ne traite pas tous les cas ambigus ; elle ne permet donc pas d'affirmer que l'arrêt réel est éliminé. Ne pas déployer ou relancer le service.

## Analyse de l'arrêt

**PROUVÉ :** les journaux phase 13 indiquent `Duplicate video timestamp`, puis `continuous: non-retryable session failure`, après environ 765 s et 9 560 images publiées. Dans [rtsp.py](../netvue_bridge/rtsp.py), `TimestampClock.update` rejetait un delta nul ; dans [stream.py](../netvue_bridge/stream.py), le corps du paquet média commence par un type suivi d'un entier 32 bits little-endian extrait de `body[1:5]`. La vidéo `body[5:]` est passée au publieur, qui découpe les NAL Annex B et traite les NAL 1/5 comme image. Une valeur d'horloge identique est une propriété des métadonnées, pas une preuve d'image identique.

Le calcul utilise les millisecondes et accepte le rebouclage 32 bits ; le calibrage en millisecondes est cohérent avec les observations antérieures et les tests, pas une garantie pour tous les modèles. Un retour en arrière ou une discontinuité de plus de 10 s reste une erreur. La réinitialisation à zéro n'est acceptée que pour un IDR après changement de paramètres et confirmation LIVE répétée, avec raccord basé sur le temps d'arrivée ; un autre retour à zéro reste refusé. L'arrivée de confirmations LIVE supplémentaires ne prouve pas une nouvelle demande ni une nouvelle authentification.

**HYPOTHÈSES, NON VÉRIFIÉES :** image ou paquet réellement répété ; horodatage produit identique pour deux images distinctes ; plusieurs slices de la même image dans différents paquets ; réinitialisation non reconnue ; différence d'unités ou défaut de traitement. TCP peut retransmettre sur le réseau mais présente normalement un flux d'octets ordonné, sans dupliquer ces octets à l'application : cela ne démontre pas à lui seul une image dupliquée. Les journaux ne contiennent ni les deux charges H.264 ni les deux valeurs nécessaires pour trancher. Un changement SPS/PPS ou de résolution peut coïncider avec une réinitialisation, sans constituer en lui-même la cause du doublon.

## Algorithme et limites

Le chemin existant de publication, d'extraction des paramètres, d'attente IDR, de décodage initial et de remux RTSP/TCP est conservé. Pour une collision entre paquets contenant des NAL vidéo :

1. Aucun paquet n'est supprimé du seul fait de son horodatage. Des charges strictement identiques sont **ambiguës** : elles peuvent représenter une répétition ou deux images ; l'exécution s'arrête explicitement au lieu de choisir.
2. Pour deux charges différentes, le premier slice VCL de chacune doit commencer au macrobloc zéro et le parseur H.264 de PyAV/FFmpeg doit restituer exactement deux unités d'accès. Les NAL et plusieurs slices inclus dans une même unité sont conservés.
3. Les images en collision sont retenues jusqu'au prochain horodatage distinct. Au moins trois intervalles précédents sont nécessaires. La médiane des huit derniers intervalles détermine une plage admissible : la durée moyenne reconstruite doit rester entre 0,5 et 1,5 fois cette médiane. Le délai vidéo jusqu'à l'ancre doit être positif, suffisant pour placer tous les PTS et au plus 10 s.
4. Les PTS intermédiaires sont interpolés en millisecondes entre l'horodatage déjà publié et l'ancre suivante ; ils restent strictement croissants et retrouvent exactement l'ancre, sans dérive cumulée sur cette fenêtre. Leur espacement individuel reste estimé, pas connu.
5. Tampon : au plus huit images en collision et 4 Mio ; le délai de deux secondes à compter de la première collision est vérifié à l'arrivée du paquet suivant. Sans autre paquet, la détection existante d'interruption vidéo s'applique. Confirmation LIVE, changement de paramètres, délai, cadence, frontière ou données ambiguës pendant la résolution entraînent un arrêt explicite. Les règles antérieures de changements SPS/PPS, résolution et réinitialisation restent inchangées hors collision.

**Limite importante :** le parseur et le premier macrobloc ne constituent pas une preuve complète que tous les slices d'une image sont présents. Le pont supposait déjà qu'un paquet média contenant une image constitue une unité d'accès. Il ne reconstitue pas les slices d'une même image répartis sur plusieurs paquets Netvue ; un fragment reconnaissable est refusé lors d'une collision, mais un premier fragment peut avoir déjà été publié avant que le fragment suivant révèle l'ambiguïté. Les flux atypiques, trames B, découpages non standard et images identiques ne sont pas corrigés automatiquement. Une capture autorisée et une analyse du cadrage sont nécessaires avant de prétendre traiter ces cas.

Les nouvelles métriques dans `live_metrics` et `continuous_health` sont `same_timestamps`, `timestamp_corrections`, `ignored_frames`, `unresolved_timestamp_frames`, `max_frame_gap_ms`, `estimated_fps` et `last_valid_timestamp`. Le compteur d'images ignorées couvre les images explicitement écartées lors de l'attente de paramètres/IDR et des tampons de reconfiguration abandonnés ; il ne mesure pas des pertes réseau inconnues. `last_valid_timestamp` est l'horodatage de l'horloge, éventuellement interpolé ; `last_video_pts` reste la dernière valeur reçue. Aucune image ni clé n'est journalisée.

## Tests

**PROUVÉ :** 60 tests automatisés réussis dans l'image finale, réseau Docker `none`, dont les suites existantes et les nouveaux tests de collisions, multi-slices dans une unité, fragments, charges identiques, délais, limite de tampon, cadence insuffisante, discontinuité et confirmation LIVE. Les anciens tests couvrent notamment timestamps décroissants, rebouclage, réinitialisation, SPS/PPS, résolution, interruption et arrêt des processus. Les tests de compatibilité vidéo sont synthétiques.

Deux tests d'intégration supplémentaires du superviseur utilisent uniquement une caméra fictive sur la boucle locale, MediaMTX temporaire et un budget neuf en `/tmp` : dans le premier, deux collisions corrigées ne provoquent aucune reconnexion et une seule réservation est enregistrée ; dans le second, une interruption fictive provoque une deuxième réservation avant reconnexion. Pour réduire le temps du second test seulement, son délai est remplacé en mémoire par 0,1 s ; les délais de production ne sont pas modifiés. Les deux tests réussissent. La conservation de l'historique, du backoff et du plafond est également testée avec des budgets fictifs.

FFprobe et FFmpeg ont lu deux scénarios RTSP/TCP temporaires avec collisions pendant la fenêtre de lecture : **40 paquets chacun**, PTS strictement croissants avec pas de 0,1 s ; FFmpeg a décodé sans erreur. Chaque pont a publié 55 images et corrigé trois collisions. Le scénario de référence borné séparé a décodé 31 images, sans erreur ; échantillons de pointe RSS : pont 48 492 Kio, MediaMTX 40 248 Kio. Ce ne sont pas des pics réels garantis.

### Simulation accélérée prolongée

[timestamp_long_synthetic.py](../tests/timestamp_long_synthetic.py) génère 20 000 images H.264 à 10 fps, dont un doublon vers 765 s de **temps vidéo simulé**, plusieurs autres collisions, une confirmation LIVE répétée, un changement SPS/PPS, une résolution 64×64 → 128×64 et une réinitialisation à zéro. L'horloge d'arrivée est simulée : **ce n'est pas un fonctionnement réel de 33 minutes**.

Deux exécutions de l'image finale ont chacune :

- publié et fait décoder par FFmpeg **20 000 images**, sans erreur ;
- donné à FFprobe **20 000 PTS strictement croissants**, sur **1 999,9 s**, écart maximal **0,1 s** ;
- détecté et corrigé quatre collisions, un changement SPS, un PPS, une résolution et une réinitialisation ; zéro image explicitement ignorée ;
- détecté les deux résolutions avec FFprobe.

La publication prolongée est remuxée dans un MPEG-TS synthétique en `/tmp` pour permettre une analyse exhaustive accélérée ; la publication vers MediaMTX/RTSP et ses lecteurs sont testés séparément ci-dessus. Chaque exécution finale a duré environ 9,2 s de temps réel. Limites du conteneur : 0,5 CPU et 256 Mio ; utilisation cgroup environ 49,8–50,0 % d'un cœur et pic indiqué environ 127,5 Mio, pour **l'ensemble du scénario accéléré**, y compris les lecteurs et données temporaires. Le pic RSS indiqué par Python est environ 142 Mio ; ces comptabilités ne sont pas équivalentes. Ces résultats ne prédisent ni les pics sur vidéo 1080p ni la consommation en fonctionnement réel prolongé.

Les vidéos synthétiques étaient en tmpfs et ont disparu avec les conteneurs. Les mesures ne prouvent pas une absence d'intermittence en production.

## Image et compatibilité Swarm

Image locale construite sans registre externe : `netvue-bridge:phase12b-recovery-179f7438`, identifiant immuable `sha256:640d2990cf0abcc3bd146275d799c94b56db9f469402299c669caddfa8c1dfb7`. Python 3.13.5, PyAV 16.1.0, `pip check` réussi, UID/GID 10001. L'empreinte du [rtsp.py](../netvue_bridge/rtsp.py) local correspond à celle du fichier dans l'image.

`docker stack config` a fusionné les deux fichiers de phase 12 avec des valeurs **fictives** : entrée `netvue_bridge.continuous`, réseau host externe, une réplique, `restart_policy: none`, 0,5 CPU et 256 Mio. C'est une validation de syntaxe et de fusion, pas un déploiement Swarm ni une validation réelle. L'image du service réel n'a pas été changée.

Pour reproduire la simulation longue : transférer les fichiers du projet vers un nouveau dossier de développement Debian, monter [timestamp_long_synthetic.py](../tests/timestamp_long_synthetic.py) et les binaires FFmpeg/FFprobe portables en lecture seule, puis exécuter l'image immuable dans un conteneur `--rm --network none --read-only --cap-drop ALL --memory 256m --cpus 0.5`, avec `/tmp` en tmpfs de 64 Mio et `PYTHONPATH=/app`. Prérequis : Docker, PyAV/libx264 dans l'image et binaires portables compatibles. Sorties : métriques et résultats FFprobe/FFmpeg ; risques : charge CPU, mémoire et espace temporaire. Ne monter aucun budget réel et ne pas utiliser de réseau host.

## État réel et projet de reprise

**PROUVÉ :** lecture seule du budget phase 12 avant et après les tests : `status=blocked`, `stage=BLOCKED`, `total=1`, une réservation conservée ; l'empreinte du fichier est identique. Le service `netvue-pilot_bridge` reste à **0/1** avec la même tâche en échec. Aucun compteur n'a été réinitialisé et aucun service existant n'a été redémarré.

**Procédure préparatoire, non opérationnelle et non appliquée :**

1. Obtenir une autorisation distincte précisant appareil, nature des essais, nombre maximal de tentatives, durée et modifications permises. Conserver les journaux et une copie de preuve hors de tout chemin actif, sans la restaurer comme budget courant.
2. Vérifier la spécification Swarm, l'absence de tâche ou processus de pont ancien, l'absence d'une autre instance utilisant la caméra et le verrou exclusif du **même** budget. Une tâche `0/1` seule ne prouve pas l'absence de tous les processus possibles. Empêcher toute relance concurrente nécessite une intervention explicitement autorisée.
3. Vérifier l'identité de la session, son issue, l'intégrité de l'historique, le boot et les horloges. Si la fin de session ou les horloges restent ambiguës, ne pas reprendre. Le test fictif démontre uniquement que `finish_retryable` conserve l'historique, impose le backoff et respecte le plafond lorsqu'une interruption propre est déjà établie.
4. Concevoir puis tester une réconciliation sous verrou avec journal d'audit durable, preuve des conditions et de l'autorisation, mise à jour atomique et conservation de chaque réservation ; contrôler encore les limites glissantes et imposer le backoff. Tester crash avant/après audit et avant/après écriture. **Aucune telle réconciliation n'est actuellement implémentée pour `blocked` ou une fin ambiguë** : les tests confirment que les méthodes existantes refusent de débloquer cet état.
5. Seulement après validation de cette procédure et autorisation, envisager une session réelle bornée avec l'image épinglée, observation des métriques et arrêt sûr. Ne jamais copier, réinitialiser, restaurer ou éditer manuellement le budget pour contourner ces protections.

Fichiers modifiés : [rtsp.py](../netvue_bridge/rtsp.py), [README.md](../deployment/README.md), [test_rtsp_bridge.py](../tests/test_rtsp_bridge.py), [test_continuous.py](../tests/test_continuous.py). Nouveaux tests : [test_timestamp_recovery.py](../tests/test_timestamp_recovery.py), [test_continuous_timestamp_integration.py](../tests/test_continuous_timestamp_integration.py) et [timestamp_long_synthetic.py](../tests/timestamp_long_synthetic.py).
