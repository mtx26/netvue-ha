# Phase 13 bis — installation Home Assistant

## Nouvelle tentative autorisée

**PROUVÉ :** après l'autorisation du propriétaire, Home Assistant a été arrêté proprement une deuxième fois. Une archive de `/config` a été créée à froid dans `/home/mtx26/netvue-ha-backup-13b-179f7438-retry/homeassistant-config.tar.gz`, dans un répertoire privé. L'archive est lisible en entier par `tar`, mesure 301 284 836 octets et son empreinte SHA-256 est `9ef34dab44f4f567247f2f927d3c04b66fd2619a1f2df7ef163ca3f3612829eb`. L'archivage demandait la conservation des propriétaires numériques, ACL et attributs étendus. Le fichier est maintenant mode 0600.

La vérification initiale a été interrompue : le script SSH ne pouvait pas changer directement les permissions de l'archive créée par root. Home Assistant a été redémarré automatiquement, puis les permissions et la lecture complète de l'archive ont été vérifiées dans un conteneur temporaire root isolé. **NON VÉRIFIÉ :** la comparaison de l'archive avec les fichiers à froid et une restauration effective.

**PROUVÉ :** après cette vérification, les 12 fichiers du composant ont été installés dans `/config/custom_components/netvue_local`, sans écraser d'autre composant. Le contenu a été comparé à la copie préparée par empreintes SHA-256 ; domaine `netvue_local`, version `0.1.0`, propriétaire root, répertoire mode 0755, traductions FR et EN présentes. Home Assistant est lancé et répond HTTP 200. Le pont reste à `0/1`, MediaMTX à `1/1`.

**NON VÉRIFIÉ :** la reconnaissance du composant par l'instance HA active et sa présence dans l'interface : les fichiers ont été copiés après le redémarrage, et `homeassistant/loader.py`, méthode `async_get_custom_components`, met en cache la liste des composants. Le propriétaire peut rechercher **Netvue Local** dans Paramètres → Appareils et services → Ajouter une intégration. S'il n'apparaît pas, demander l'autorisation d'un redémarrage supplémentaire de Home Assistant ; ne pas relancer le pont ni contacter la caméra.

## Première tentative — historique

**PROUVÉ :** lors de la première tentative, la sauvegarde à froid a échoué avant création de l'archive, et la procédure a donc refusé de copier le composant. Home Assistant a été redémarré dans le bloc de sécurité. La nouvelle tentative décrite ci-dessus a ensuite été autorisée.

**PROUVÉ :** Home Assistant répond à nouveau en HTTP local (`200`). Cela ne démontre pas à lui seul le fonctionnement de toutes ses intégrations. Des lignes d'erreur existaient déjà avant l'intervention et d'autres ont été comptées après ; leur cause et leur criticité n'ont pas été établies.

## Environnement et sauvegarde

- Conteneur Docker autonome `homeassistant`, version 2026.7.1, Python 3.14.6, réseau `host` ; `/config` est monté depuis `/home/mtx26/homeassistant`.
- Aucun composant `netvue_local` préexistant n'avait été observé avant la première tentative.
- Répertoire privé de sauvegarde créé : `/home/mtx26/netvue-ha-backup-13b-179f7438`, mode 0700. Il contient un journal d'erreur privé, mais **aucune archive de configuration valide**. Ne pas considérer ce répertoire comme une sauvegarde.
- Home Assistant s'est arrêté proprement. La création de l'archive a échoué avec une erreur de permission : l'image du pont employée pour `tar` utilise par défaut un utilisateur non root, qui ne peut pas écrire dans le répertoire privé. Aucune installation n'a suivi. Le bloc `finally` a redémarré Home Assistant.
- **PROUVÉ :** un essai ultérieur sur la copie de préparation, sans arrêter Home Assistant ni accéder aux caméras, a réussi avec `docker run --user 0:0` dans un conteneur temporaire isolé. L'archive fictive a été supprimée. **NON VÉRIFIÉ :** la sauvegarde complète avec cet utilisateur, sa relecture et sa comparaison à froid.
- La prochaine tentative doit vérifier au préalable les permissions du conteneur de sauvegarde, s'assurer que le conteneur HA et ses montages n'ont pas changé, vérifier l'absence de destination existante, archiver `/config` à froid en préservant propriétaires numériques, ACL et attributs étendus, puis relire et comparer l'archive avant toute installation. Prévoir le redémarrage de HA même en cas d'échec. Ne pas exposer l'archive ni le journal privé.

## Vérifications isolées

**PROUVÉ :**

- Les 11 tests ciblés [test_home_assistant_integration.py](../tests/test_home_assistant_integration.py) ont réussi avec l'image HA installée, dans un conteneur temporaire sans réseau externe.
- Le test [ha_integration_synthetic.py](../tests/ha_integration_synthetic.py), utilisant le vrai gestionnaire de configuration HA et MediaMTX isolé, a créé une entrée fictive et ses entités caméra et capteur. Il a vérifié la confirmation, l'annonce SDP H.264, un aperçu JPEG produit par Stream après une image clé, le passage indisponible après arrêt du publieur, et le déchargement.
- L'aperçu a d'abord échoué parce que le premier appel arrivait avant la première image clé ; le harnais attend maintenant explicitement celle-ci. Le test isolé n'établit pas le comportement de l'interface graphique réelle. Une erreur Zeroconf/analytics dans le HA fictif est liée à l'absence d'interface multicast dans le conteneur `--network none` ; elle ne prouve pas une erreur sur HA réel.
- La configuration effective de `netvue-pilot_mediamtx`, lue sans sonde réseau, écoute sur `127.0.0.1:8554`, contient le chemin `netvue2`, utilise la source `publisher` par défaut et ne configure aucun hook `runOn*` actif pour ce chemin ou ses paramètres par défaut.

**NON VÉRIFIÉ :** reconnaissance de Netvue Local dans l'interface réelle, assistant graphique réel, persistance de l'entrée caméra 2, affichage réel et fonctionnement complet des autres intégrations. Aucune entrée ou entité Netvue n'a été créée dans HA réel.

## Caméra, pont et sécurité

**PROUVÉ :** le pont reste à `0/1`, MediaMTX à `1/1`. Le budget réel lu en lecture seule reste `status=blocked`, `stage=BLOCKED`. Aucun budget n'a été modifié, aucune découverte réelle, connexion TCP ou authentification caméra n'a été effectuée ; le pont n'a pas été relancé. Les conteneurs de test étaient temporaires et isolés ; aucun service permanent supplémentaire n'a été lancé.

**DÉDUIT :** le flux réel ne peut pas être considéré comme disponible lorsque le pont est arrêté. La correction expérimentale du pont décrite dans [phase12b-timestamp-recovery.md](phase12b-timestamp-recovery.md) n'a pas été déployée ni validée sur la caméra. Le transport Netvue local reste non chiffré.

## Installation, mise à jour et retour arrière

Pour une mise à jour ultérieure, sauvegarder d'abord la configuration et la version précédente de [netvue_local](../custom_components/netvue_local), puis ne remplacer que ce composant avec une autorisation adaptée. Vérifier les fichiers, permissions et traductions, puis contrôler HA et son interface après le redémarrage autorisé.

Pour revenir sur l'installation, supprimer d'éventuelles entrées via l'interface officielle avant de retirer uniquement le composant ajouté, après sauvegarde et avec autorisation pour le redémarrage nécessaire. En cas de restauration complète, vérifier l'archive et préparer la restauration séparément sans écraser des données non sauvegardées ; aucune restauration effective n'a été testée.

Lorsque l'intégration sera chargée, l'ajout manuel peut associer l'IP de la caméra 2 et `rtsp://127.0.0.1:8554/netvue2`, avec confirmation explicite des sondes RTSP ; la configuration peut être enregistrée même si le flux est indisponible. Ne pas lancer la recherche UDP réelle automatiquement. L'utilisation effective de l'interface authentifiée peut nécessiter l'intervention du propriétaire.

**Prochaine étape :** vérifier la présence de l'intégration dans l'interface, puis obtenir si nécessaire l'autorisation d'un redémarrage supplémentaire de Home Assistant. La reprise du pont et toute nouvelle session caméra nécessitent une autorisation distincte et une procédure de budget sûre.
