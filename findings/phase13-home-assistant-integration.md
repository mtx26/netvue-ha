# Phase 13 — Netvue Local

## Décision et état du pont

**PROUVÉ :** l'intégration a été préparée dans [netvue_local](../custom_components/netvue_local) et testée isolément avec l'image de Home Assistant 2026.7.1, Python 3.14.6. Elle n'a pas été installée dans Home Assistant et son interface réelle n'a pas été testée. Elle ne configure pas Docker, MediaMTX ou une passerelle : l'objectif d'un ajout sans aucune préparation préalable n'est donc **pas atteint**.

**PROUVÉ :** pendant la phase 13, `docker service ls` et `docker service ps netvue-pilot_bridge` ont montré le pont à **0/1**, tâche arrêtée en erreur, et MediaMTX à **1/1**. Le journal de la tâche indique `live: error=Duplicate video timestamp`, puis `continuous: non-retryable session failure`, après environ 765 s et 9 560 images publiées. [TimestampClock.update](../netvue_bridge/rtsp.py) rejette explicitement deux horodatages consécutifs identiques. Les journaux ne démontrent pas pourquoi la caméra ou le transport a produit cet événement. Aucun redémarrage ni modification de budget n'a été effectué.

**DÉDUIT :** le flux réel ne peut pas être considéré comme disponible tant que le pont est arrêté. **NON VÉRIFIÉ :** la cause exacte du doublon, l'état précis du budget après l'arrêt et une procédure de reprise sûre. Préserver les journaux et l'état ; ne pas relancer, éditer ou supprimer le budget. Une analyse séparée et une autorisation explicite sont nécessaires avant toute reprise.

## Architecture et fonctions

La passerelle existante reste responsable de Netvue, des quotas et de la publication. Cette intégration consomme uniquement une URL RTSP déjà fournie par l'utilisateur, sous `127.0.0.1`, avec un port explicite et un chemin simple. Le flux ne contient ni identifiant ni mot de passe dans son URL. L'accès à la boucle locale impose Home Assistant sur le même espace réseau que MediaMTX ; les conteneurs en réseau host et processus locaux peuvent également voir le flux. RTSP et transport Netvue LAN ne sont pas chiffrés.

Le formulaire propose recherche UDP explicite ou saisie manuelle, puis demande une confirmation de l'association entre IP et URL. La caméra 1 n'est jamais associée automatiquement au flux de la caméra 2 ; sa compatibilité vidéo reste non vérifiée. Après confirmation seulement, une requête RTSP `DESCRIBE` locale teste une annonce SDP H.264. La configuration peut être enregistrée même si elle est indisponible. Des sondes sont ensuite faites au démarrage et toutes les 30 s tant que l'entrée est chargée ; un serveur configuré pour démarrer une source à la demande pourrait la démarrer. Ne sélectionner qu'une source déjà publiée et dont ce comportement est connu.

Une entité caméra utilise Stream et RTSP/TCP ; un capteur binaire indique uniquement la présence d'une réponse SDP annonçant H.264. Cela ne prouve ni le décodage, ni l'association à la caméra, ni une session caméra active. L'entité caméra est indisponible quand cette sonde échoue. Résolution, FPS, date de dernière image, durée, reconnexions, erreurs de la passerelle et budget ne sont pas accessibles et ne sont pas inventés.

Les options modifient l'IP de référence et l'URL en conservant l'identifiant de l'entrée ; elles ne reconfigurent pas la passerelle. Les doublons sont contrôlés par IP courante et URL, pas par identité matérielle : aucun identifiant matériel stable n'a été démontré. Une caméra déplacée peut donc être ajoutée une seconde fois si l'ancienne entrée n'est pas mise à jour. Chaque entrée possède son appareil et ses entités ; chaque caméra supplémentaire nécessite séparément une passerelle, un budget et un chemin RTSP distincts.

Le code ne démarre pas, n'arrête pas et ne reconnecte pas la passerelle. Une future API de contrôle serait limitée à la boucle locale, authentifiée, avec contrôle d'accès par appareil, commandes bornées et respect du budget persistant ; elle ne doit exposer ni clés ni socket Docker et doit refuser tout état ambigu. Aucune API n'est développée ou activée ici ; sa sûreté et ses interactions avec les quotas restent à concevoir et tester.

## Découverte

[discovery.py](../custom_components/netvue_local/discovery.py) utilise le format documenté dans [discovery.py du pont](../netvue_bridge/discovery.py) et [protocol.py](../netvue_bridge/protocol.py) : une requête UDP V2, port 36868, limitée à l'interface sélectionnée, une diffusion dirigée et deux secondes d'attente. La sélection est limitée aux sous-réseaux RFC1918 de /23 à /30 des interfaces HA actives. Les réponses doivent être de type attendu, correctement structurées, et leur adresse annoncée doit correspondre à l'émetteur ; les réponses répétées sont dédupliquées. La clé incluse dans une réponse est vérifiée mais n'est ni conservée dans le candidat, ni journalisée, ni enregistrée dans HA.

**PROUVÉ :** le protocole unicast est utilisé par le pont et les tests ici utilisent des réponses fictives. **NON VÉRIFIÉ :** la réponse des caméras à la diffusion dirigée ; aucun paquet de découverte réel n'a été envoyé en phase 13. Une réponse UDP peut être usurpée et ne garantit ni identité matérielle ni compatibilité vidéo. Un échec laisse la saisie manuelle disponible en revenant au menu. L'adresse IP seule et le port 5555 ne prouvent pas l'identité Netvue.

## Vérifications

- **PROUVÉ :** 11 tests ciblés [test_home_assistant_integration.py](../tests/test_home_assistant_integration.py) réussis dans un conteneur temporaire `--network none`, fichiers montés en lecture seule, basé sur l'image HA installée. Les tests couvrent paquet UDP, réponses invalides et doublons, requête unique et attente simulée, sélection et formulaire, consentement, URL, options et IP, disponibilité RTSP synthétique, déchargement simulé et structure des traductions. Le serveur RTSP fictif est sur la boucle locale du conteneur isolé.
- **PROUVÉ :** 13 tests existants du superviseur continu réussis séparément dans l'image du pont, avec réseau `none` et `/tmp` temporaire. Un premier essai sans espace temporaire accessible a échoué avant exécution ; il a été corrigé pour la reprise du test. Ces tests n'examinent pas le budget réel.
- **PROUVÉ :** les classes et méthodes utilisées ont été chargées depuis l'image HA 2026.7.1 ; les tests de flux et d'entités utilisent des objets HA réels mais des gestionnaires de configuration simulés.
- **NON VÉRIFIÉ :** chargement complet par le gestionnaire d'intégrations, interface graphique, persistance après redémarrage HA, registre réel, suppression effective, rendu Stream et lecture/décodage par cette intégration. Les tests de déchargement utilisent une simulation et ne constituent pas une preuve de suppression réelle. Aucune caméra n'a été contactée ; aucun changement n'a été fait sur HA, le pont ou leurs budgets.

Fichiers principaux : [manifest.json](../custom_components/netvue_local/manifest.json), [config_flow.py](../custom_components/netvue_local/config_flow.py), [camera.py](../custom_components/netvue_local/camera.py), [binary_sensor.py](../custom_components/netvue_local/binary_sensor.py), [coordinator.py](../custom_components/netvue_local/coordinator.py), [rtsp.py](../custom_components/netvue_local/rtsp.py), [strings.json](../custom_components/netvue_local/strings.json) et les traductions [fr.json](../custom_components/netvue_local/translations/fr.json) et [en.json](../custom_components/netvue_local/translations/en.json).

## Installation, mise à jour et suppression — non exécutées

Ces opérations exigent une nouvelle autorisation : elles modifient la configuration active ou redémarrent Home Assistant. Avant toute installation, résoudre séparément l'arrêt du pont sans contourner le budget, sauvegarder la configuration HA et confirmer que l'URL RTSP correspond bien à la caméra et ne démarre pas une source à la demande. Prévoir une fenêtre de redémarrage.

1. Copier le répertoire `custom_components/netvue_local` du projet vers le répertoire `custom_components` du volume `/config` de HA, sans écraser d'autre intégration ; créer ce répertoire parent s'il n'existe pas. Vérifier propriétaire et droits de lecture du conteneur.
2. Avec autorisation, redémarrer HA et rechercher **Netvue Local** dans Paramètres → Appareils et services → Ajouter une intégration. Choisir la saisie manuelle ou une recherche UDP explicitement autorisée sur l'interface locale. Saisir l'IP et l'URL RTSP déjà provisionnée, puis confirmer explicitement leur association et les sondes.
3. Vérifier le résultat du test, puis les entités, journaux et lecture. Une annonce SDP ne prouve pas l'affichage ; la lecture réelle nécessite un pont disponible.
4. Pour une mise à jour : sauvegarder la copie précédente et la configuration HA, remplacer uniquement ce composant, puis redémarrer HA avec autorisation. En cas d'échec, restaurer la copie sauvegardée et redémarrer avec autorisation.
5. Pour supprimer une caméra : supprimer son entrée depuis HA, puis vérifier le retrait des entités. Cela ne modifie ni n'arrête la passerelle, MediaMTX ou leur budget. Pour retirer le composant, sauvegarder la configuration, supprimer uniquement son répertoire après suppression des entrées, puis redémarrer HA avec autorisation.

Ne pas lancer une découverte réelle, redémarrer le pont, modifier son budget, contacter une caméra en TCP, déployer une API ou modifier les services existants sans autorisation adaptée. L'intégration et son environnement de test ne constituent pas un déploiement prêt pour la production.
