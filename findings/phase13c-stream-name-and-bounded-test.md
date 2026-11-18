# Interface RTSP et préparation du test réel

## Interface

**PROUVÉ :** [config_flow.py](../custom_components/netvue_local/config_flow.py) demande un nom affiché, une adresse IPv4 de caméra et un nom technique de flux. [rtsp.py](../custom_components/netvue_local/rtsp.py) accepte uniquement les lettres ASCII, chiffres, tirets et caractères de soulignement ; il génère `rtsp://127.0.0.1:8554/{nom}`. Le nom proposé par défaut est `netvue2` ; il ne constitue pas une association automatique avec une caméra. La confirmation affiche l'adresse générée et demande l'accord explicite avant les sondes.

La sonde RTSP `DESCRIBE` recherche une annonce SDP H.264, sans se connecter directement à la caméra. Elle affiche un résultat indisponible si le chemin n'est pas publié. Un nom ne crée pas de flux ; une annonce SDP ne prouve ni le décodage ni l'identité de la caméra. Une source à la demande peut être déclenchée par une sonde : la confirmation l'indique. Le chemin indisponible peut toujours être enregistré.

Les données existantes conservent leur URL, sans migration. Les options préremplissent le nom s'il correspond à une URL simple sur le port 8554 ; pour une ancienne URL valide avec un autre port ou un chemin imbriqué, laisser le nom vide conserve l'URL. Choisir un nouveau nom constitue un remplacement explicite, affiché avant confirmation. [strings.json](../custom_components/netvue_local/strings.json) et les traductions [fr.json](../custom_components/netvue_local/translations/fr.json) et [en.json](../custom_components/netvue_local/translations/en.json) décrivent ces comportements.

## Tests

**PROUVÉ :** 13 tests ciblés [test_home_assistant_integration.py](../tests/test_home_assistant_integration.py) ont réussi avec l'image HA 2026.7.1, dans un conteneur temporaire `--network none`. Ils couvrent notamment noms valides ou interdits, génération de l'URL, confirmation, doublons, absence de flux, anciennes URL et traductions.

Le test [ha_integration_synthetic.py](../tests/ha_integration_synthetic.py) a réussi avec un Home Assistant et MediaMTX isolés : chemin synthétique publié reconnu, chemin absent refusé par la sonde, création de l'entrée et modification de ses options via les gestionnaires HA, image JPEG Stream et passage indisponible après arrêt du publieur. Les processus et la vidéo fictive étaient temporaires. Une erreur Zeroconf/analytics apparaît dans le HA de test dépourvu d'interface multicast ; elle ne concerne pas nécessairement l'instance réelle.

**NON VÉRIFIÉ :** interface réelle, flux réel avec cette version du composant et identité de la caméra associée au flux. Le composant modifié n'a pas été déployé ; aucune modification de HA ni aucun redémarrage HA n'ont été faits pendant cette étape.

## Test réel demandé : arrêté avant connexion

**PROUVÉ :** le service `netvue-pilot_bridge` est à `0/1`, la tâche existante est en échec, aucun conteneur de ce service n'est en cours, et aucun socket TCP établi vers le port caméra 2 n'a été observé sur le serveur. Cette observation ne garantit pas l'absence de sessions établies depuis d'autres hôtes. MediaMTX reste à `1/1`.

Le budget réel existant reste `blocked` / `BLOCKED`, une réservation en historique ; lors de la lecture, une réservation était dans chacune des fenêtres 24 h et 7 jours. Son empreinte est inchangée depuis le précédent contrôle. Dans [continuous.py](../netvue_bridge/continuous.py), le superviseur refuse cet état ; [supervisor.py](../netvue_bridge/supervisor.py) initialise un budget indépendant qui ne consulte pas l'état ni le verrou du budget continu. Comme expliqué dans [phase12b-timestamp-recovery.md](phase12b-timestamp-recovery.md), aucune réconciliation auditée de ce blocage n'est implémentée.

**DÉDUIT :** lancer le pont avec un nouveau répertoire ferait ignorer le blocage existant, même si les quotas numériques observés ne sont pas atteints. Cela contournerait la protection que le propriétaire demande de respecter. Conformément à sa consigne d'arrêt dans ce cas, aucun budget neuf n'a été créé et aucun test réel n'a été lancé. Aucune découverte, connexion, authentification ou demande LIVE n'a été effectuée ; l'autorisation d'une connexion reste inutilisée. Les budgets et journaux historiques sont conservés.

**NON VÉRIFIÉ :** authentification réelle, FPS, résolution, continuité temporelle, SPS/PPS, décodage et accès HA avec l'image expérimentale. La correction phase 12 bis ne constitue pas une garantie de stabilité réelle.

## Mise à jour future

Après autorisation de déploiement et de redémarrage HA, sauvegarder la configuration et la version installée de l'intégration ; copier seulement [netvue_local](../custom_components/netvue_local) vers `/config/custom_components/netvue_local`, vérifier les permissions et fichiers, redémarrer uniquement HA, puis vérifier l'assistant dans son interface. Conserver les entrées existantes et ne pas modifier directement `.storage`. En cas de problème, restaurer la version précédemment sauvegardée et redémarrer HA avec autorisation. La sauvegarde récente décrite dans [phase13b-home-assistant-installation.md](phase13b-home-assistant-installation.md) a été relue, mais sa restauration n'a pas été testée.

Avant le test caméra : concevoir et tester hors ligne une procédure de réconciliation auditée qui respecte explicitement le blocage, son historique et les quotas ; vérifier l'absence de session concurrente et obtenir une décision sur la reprise avant toute connexion. Ne pas modifier le budget bloqué ni le contourner en changeant simplement de répertoire.
