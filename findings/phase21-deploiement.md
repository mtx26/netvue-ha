# Phase 21 — préparation du déploiement

## OBJECTIF

Préparer la mise en service de la version actuelle sans interrompre les
services existants, sans modifier Home Assistant et sans contacter les caméras.
Vérifier les contrats du pont, de MediaMTX et de l'intégration HA avec des
scénarios isolés avant de proposer un basculement réversible.

## ÉTAT INITIAL OBSERVÉ

- Le commit local est `f7ac6ec1fa13140aafcd0de225f1a402aecbca11`, un commit
  devant `origin/main`. Le code comporte aussi de nombreuses modifications
  préexistantes indexées, notamment la phase 20 ; il ne correspond donc pas
  exactement à ce commit. Aucun commit ni push n'a été effectué.
- Le dépôt GitHub est privé. Le package GHCR `mtx26/netvue-ha` est privé ;
  son image taguée provient du commit `73fe8737048ca2912d89e4885dbe354116bbc204`,
  antérieur au code local. L'authentification GitHub disponible ne comprend
  pas `write:packages`. Aucune image à jour n'a été publiée.
- La tentative passive `ssh home` a nécessité une authentification interactive
  Cloudflare et a expiré. L'état Debian réel reste inconnu : stack Portainer,
  propriétaire du port 8554, volumes, budgets, version HA et réseau HA n'ont
  pas pu être inspectés. Aucun diagnostic actif de caméra n'a été réalisé.
- Docker Desktop est accessible localement ; son Swarm est inactif. Il n'a pas
  été initialisé. Les tests isolés ne représentent pas le Swarm de production.

## MODIFICATIONS ET FONCTIONNEMENT

- [mediamtx.yml](../deployment/mediamtx.yml) utilise `all_others` : MediaMTX
  accepte les chemins attribués par le gestionnaire, notamment les noms
  `netvue_<adresse>`, ainsi que `netvue2` pour les tests bornés. L'écoute reste
  limitée à `127.0.0.1:8554`, sans authentification RTSP supplémentaire.
  Les processus locaux restent capables de lire et publier les flux.
- Le test de [test_rtsp_bridge.py](../tests/test_rtsp_bridge.py) publie
  maintenant sous un nom géré avec la configuration de déploiement réelle.
  Avant correction il échouait avec `RTSP publication failed` ; après
  correction il passe avec MediaMTX, FFprobe et FFmpeg.
- Le [guide Portainer](../deployment/README.md) précise que le fichier monté
  sur le serveur doit être vérifié : la modification locale ne change aucun
  bind mount existant.
- L'image locale `netvue-bridge:phase21-local`, construite depuis le code
  courant avec le Dockerfile, a pour identifiant
  `sha256:417d9c0b5744d19b1b73ba2c37cb7c37b237bf198b926701ff499710df6dd091`
  et fonctionne sous Linux amd64 avec UID 10001. Ce n'est pas une image GHCR.
  Le premier build a échoué car le helper Docker n'était pas dans le PATH ;
  le build a réussi après ajout du répertoire des outils Docker au PATH.
- Aucun superviseur, budget, service ou identifiant RTSP supplémentaire n'a
  été ajouté. Les états v4 restent les seuls acceptés.

## TESTS

- Rendu de [stack.yaml](../deployment/stack.yaml) avec `docker stack config`
  et des variables fictives : réussi. Cela ne valide ni déploiement Swarm,
  bind mounts, redémarrages de services, ni réseau host réel.
- Suite Linux du pont et tests vidéo synthétiques : **79 réussis, 1 ignoré**
  sur 80, le test borné exigeant des fichiers cgroup v2 absents. Déconnexions
  et reconnexions, budgets v2/v3 conservés, H.264, timestamps et changement de
  résolution sont couverts par des tests locaux, sans caméra réelle.
- Suite de l'intégration HA 2026.11.2 isolée : **14 réussis**. Avertissement
  non bloquant sur l'absence de `libturbojpeg` dans cet environnement.
- Image officielle HA 2026.11.2 sur réseau Docker interne, avec configuration
  MediaMTX de déploiement : configuration et options, entités, disponibilité
  RTSP, aperçu JPEG, transition hors ligne et déchargement réussis. L'erreur
  `Stream ended` a été journalisée à l'arrêt volontaire de la vidéo fictive.
  La découverte est couverte par les tests unitaires, pas par ce scénario HA.
- Image locale du pont en conteneur `--network none`, système de fichiers
  en lecture seule, sans capacités, limité à 0,5 CPU et 512 Mo, volume
  temporaire UID 10001 : caméra fictive, authentification simulée,
  publication RTSP, 160 paquets, 154 images décodées, changement de résolution
  et redémarrage du gestionnaire avec conservation du fichier budget réussis.
  Ce test regroupe les processus dans un conteneur : il ne valide pas les
  deux services séparés ni le redémarrage Swarm. Le volume de test a été
  supprimé, sans toucher aux volumes réels.
- Aucun test de stabilité 24 h, de port 8554 Debian, d'exposition Internet
  effective, de permissions des volumes réels ou de compatibilité avec la
  version HA installée n'a été possible.

## TRANSITION PROPOSÉE — EN ATTENTE D'ACCORD

Après rétablissement de l'accès SSH, relever passivement les services,
images et digests, configurations et variables Portainer, réseaux, ports
8554/8765 et processus associés, chemins et droits des volumes, versions et
états des budgets, version et mode réseau de HA. Ne pas afficher ni publier
les secrets ou les budgets complets. Identifier qui utilise les ports avant
de décider quel service arrêter ; aucune libération du port n'est supposée.

Publier d'abord une image correspondant exactement au code validé : les
modifications locales doivent être enregistrées et transmises au dépôt privé
avant l'utilisation du workflow, ou une autre méthode de build traçable doit
être convenue. La visibilité publique du seul package GHCR et le pull anonyme
doivent être vérifiés ; le dépôt source reste privé. L'image publique contient
le code du pont. Les droits GitHub actuels ne permettent pas cette publication
directe. Ne pas utiliser le tag ancien comme image à jour.

Après inventaire et autorisation expresse du basculement :

1. Conserver hors serveur ou dans un emplacement protégé une copie de la
   stack et des variables Portainer, configuration MediaMTX, version de
   l'intégration HA et références exactes des anciennes images. Vérifier la
   disponibilité des images de retour arrière.
2. Arrêter proprement les sessions et le pont ancien, puis sauvegarder à froid
   les états et budgets, métadonnées et permissions incluses, dans une archive
   distincte protégée. Ne jamais restaurer un ancien budget pour effacer une
   tentative ou un blocage.
3. Vérifier le format et l'identité de chaque état sans les modifier. Un état
   non-v4, corrompu, ambigu ou bloqué suspend la transition : aucune
   initialisation, migration non validée ou utilisation d'un chemin neuf pour
   le contourner. Une migration/réconciliation exige une procédure séparée,
   testée hors ligne et autorisée.
4. Identifier et arrêter seulement les services nécessaires pour libérer les
   ports ; installer la configuration MediaMTX corrigée et déployer la stack
   avec l'image publiée immuable, les chemins d'état validés et UID/GID 10001.
   Vérifier limites, permissions, écoute loopback et absence de ports exposés.
   Vérifier préalablement que HA partage le réseau host du même nœud.
5. Pour HA, sauvegarder l'intégration existante et examiner les entrées :
   les anciennes URL personnalisées ou entrées sans nom géré sont refusées,
   non migrées automatiquement. Toute copie du composant, modification de HA
   et tout redémarrage attendent une autorisation explicite. Aucun ajout
   d'appareil ni découverte ne doit déclencher une connexion caméra avant
   l'autorisation distincte pour celle-ci.

### Retour arrière

Si l'installation ne démarre pas, arrêter proprement la nouvelle stack,
conserver ses journaux expurgés et ses états courants, restaurer uniquement
la configuration et les images précédentes et vérifier les ports avant
redémarrage. La reprise de l'ancien pont dépend de sa compatibilité avec
l'état désormais présent : si elle est incertaine, le laisser arrêté et
demander une procédure de réconciliation. Ne pas écraser le budget courant
par la sauvegarde. Restaurer l'intégration HA uniquement avec autorisation et
après vérification de compatibilité de ses entrées.

## ÉTAT FINAL ET PROCHAINE ÉTAPE

La correction locale et les tests synthétiques décrits sont terminés ; la
publication GHCR, la validation Swarm réelle, la transition et la validation
HA réelle ne sont pas effectuées. Réactiver l'accès SSH et disposer des droits
de publication GHCR ; ensuite terminer l'inventaire et présenter un plan
adapté aux ressources réellement observées avant de demander l'accord de
basculement.

Le premier essai caméra 2 exigera une autorisation séparée indiquant le nombre
maximal de tentatives, la durée et les opérations permises : découverte,
authentification LAN, vidéo, RTSP, lecture HA, mesures et déconnexion. Les
limites de tentatives doivent être compatibles avec le mode de test choisi ;
ne pas démarrer une supervision continue sans borne pour cet essai.
