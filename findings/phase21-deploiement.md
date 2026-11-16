# Phase 21 — préparation du déploiement

## OBJECTIF

Préparer la stack Portainer à partir d'un état v4 indépendant, après abandon
et archivage vérifié des états expérimentaux, sans modifier Home Assistant ni
contacter les caméras. La mise à jour du 17 novembre confirme qu'aucune
migration v2/v3 n'est demandée.

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

- Reprise du 17 novembre : l'accès SSH fonctionne. Debian exécute Docker
  29.7.2 en Swarm sur un nœud manager. La stack `netvue-pilot` comprend un
  ancien pont en échec et MediaMTX en service. Ce dernier utilise le réseau
  host et est le processus écoutant sur `127.0.0.1:8554` ; aucun service
  n'écoute sur 8765. Portainer CE 2.39.6 est présent.
- L'ancien pont, lancé en UID/GID 10001, monte
  `/home/mtx26/netvue-phase12-179f7438/real-state` sur `/state`. Ce répertoire
  est de mode `0777` ; son budget et son verrou sont en mode `0600`, propriété
  10001:10001. D'autres budgets historiques existent sous les répertoires
  Netvue des phases 10 à 12. Le compte SSH ne peut pas lire ces fichiers et
  `sudo -n` n'est pas disponible : leur version, leur état et l'identité
  associée restent inconnus. Aucun droit n'a été contourné ni modifié.
- Résultat communiqué par l'administrateur pour le budget actif : version
  **3**, état **blocked**, étape **BLOCKED**. Cette valeur a été rapportée
  par l'utilisateur, pas lue directement par l'agent. Le code courant refuse
  les budgets non-v4 et conserve les états bloqués ; ce budget ne peut donc
  pas être utilisé pour démarrer la nouvelle stack.
- Une lecture passive des journaux de l'ancien pont montre une authentification
  réussie, une erreur vidéo classée dans les catégories « timestamp » et
  « duplicate », puis une défaillance non réessayable. Cela constitue un
  indice sur le blocage, sans prouver que le code courant corrigera le cas
  réel. Les journaux bruts et identifiants n'ont pas été exposés.
- À la demande explicite de l'utilisateur, la stack Swarm `netvue-pilot` et
  ses deux services ont été supprimés. La vérification ne montre plus de stack
  ni de service Netvue dans Swarm, et les ports 8554 et 8765 sont libres. Le
  budget bloqué et le fichier de configuration MediaMTX existent toujours ;
  aucune donnée persistante n'a été supprimée ni modifiée. Aucun contrôle de
  l'interface Portainer n'a été effectué et aucune sauvegarde supplémentaire
  n'a été créée.
- Un nouvel inventaire passif ne trouve aucun service ou conteneur Netvue
  nommé, aucun conteneur actif utilisant l'ancienne image identifiée, aucun
  processus visible du pont et aucun volume Docker nommé Netvue. Les ports
  8554 et 8765 sont libres. Huit fichiers `budget.json` ont été repérés dans
  les anciens répertoires du pilote ; leur contenu n'a pas été lu par l'agent.
  Cet inventaire est limité aux chemins accessibles inspectés.
- L'utilisateur indique souhaiter abandonner les anciens états expérimentaux
  et ne pas les migrer. Aucun état bloqué n'a été déplacé, réinitialisé ou
  contourné ; aucune archive ni nouvelle installation n'a été créée.
- Une nouvelle vérification confirme qu'aucun service, conteneur ou processus
  visible du pont n'est actif et que les ports 8554 et 8765 sont libres. Le
  budget actif reste présent, en mode `0600` et propriété de l'UID 10001 ; le
  compte SSH ne peut pas le lire et `sudo -n` reste indisponible. Une
  sauvegarde de son contenu et la vérification de cette sauvegarde n'ont donc
  pas été réalisées.
- MediaMTX utilise le fichier
  `/home/mtx26/netvue-pilot-phase10-179f7438/mediamtx.yml`, en lecture seule,
  qui ne définit que `netvue2`. Les deux services ont une limite de 0,5 CPU ;
  les limites mémoire actuelles sont 256 Mio pour le pont et 128 Mio pour
  MediaMTX. Le pont ancien ne redémarre pas automatiquement ; le nouveau
  contrat de déploiement diffère et doit être appliqué seulement après accord.
- Home Assistant est réellement en version **2026.7.1**, en réseau host sur
  le même nœud. L'intégration installée est en version 0.1.0 ; ses fichiers
  diffèrent de l'intégration locale 0.2.0, notamment pour le gestionnaire et
  les flux. L'unique entrée existante contient `host`, `name` et `stream_url`
  mais pas `stream_name` : le composant actuel la refuserait explicitement.
  Aucun champ sensible n'a été affiché ; l'entrée n'a pas été modifiée.
- Le Dockerfile copie désormais uniquement les modules Python et les
  dépendances déclarées ; le contexte exclut les autres fichiers. L'ancienne
  image locale contenait notamment des fichiers `__pycache__`, éliminés de
  la nouvelle construction. Les 13 fichiers copiés correspondent par SHA-256
  au code local ; la recherche de motifs usuels de secrets n'a rien trouvé.
  Cette recherche heuristique ne constitue pas une garantie absolue.
- L'ancienne version GHCR `phase16` a été téléchargée avec l'accès en lecture
  existant ; son répertoire applicatif contient 16 fichiers et la même
  recherche de motifs de secrets n'a rien signalé. Les deux autres versions
  GHCR non taguées sont les manifestes enfant de cette image. La mise en
  public exposerait aussi ces versions historiques.
- Le commit `cbea1534c512371cdab3598e50654a7e3991a331`, comprenant les
  changements des phases 20 et 21, a été poussé sur le dépôt privé. Le
  workflow existant a publié avec succès l'image
  `ghcr.io/mtx26/netvue-ha@sha256:3fbdc5c514d697db3dab279c165600aa9878c3fc88d24b44a3d77a80fb8eb63d`,
  également taguée `sha-cbea1534c512371cdab3598e50654a7e3991a331`. Son
  téléchargement authentifié a réussi ; les 13 fichiers applicatifs
  correspondent aux blobs Git de ce commit et au code local normalisé.
  Les fichiers HA et la configuration de stack ne sont pas dans l'image ; ils
  doivent être installés séparément lors d'un basculement autorisé.
- Le package est désormais public, tandis que le dépôt reste privé. Un pull
  Docker anonyme de l'image par digest réussit avec une configuration Docker
  vierge ; les couches étaient déjà présentes dans le cache local. Les
  versions historiques du package sont également publiques.
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
- La première image locale, avant restriction de son contenu, avait pour
  identifiant
  `sha256:417d9c0b5744d19b1b73ba2c37cb7c37b237bf198b926701ff499710df6dd091`
  et fonctionnait sous Linux amd64 avec UID 10001. Ce n'est pas une image GHCR.
  Le premier build a échoué car le helper Docker n'était pas dans le PATH ;
  le build a réussi après ajout du répertoire des outils Docker au PATH.
  L'image locale reconstruite après restriction de contenu a l'identifiant
  `sha256:5d4351df90edb4338865bc1c34468eb89fa3f4e74f53e7025f55978010e0b6c3` ;
  son démarrage et le contenu du répertoire applicatif ont été vérifiés.
- Aucun superviseur, budget, service ou identifiant RTSP supplémentaire n'a
  été ajouté. Les états v4 restent les seuls acceptés.

## TESTS

- Rendu de [stack.yaml](../deployment/stack.yaml) avec `docker stack config`
  et des variables fictives, puis avec le digest GHCR publié : réussi. Cela
  ne valide ni déploiement Swarm, bind mounts, redémarrages de services, ni
  réseau host réel. Les trois tests ciblés de configuration passent.
- Workflow GitHub Actions exécuté avec `GITHUB_TOKEN` : succès, sans nouveau
  jeton personnel. L'image téléchargée démarre. La première comparaison
  directe des octets avec Windows signalait 13 différences dues aux fins de
  ligne CRLF locales ; la comparaison des blobs Git normalisés montre
  **13 fichiers identiques sur 13**. Après publication du package, le pull
  anonyme par digest réussit avec une configuration Docker sans identifiants
  et réutilise le cache ; le téléchargement authentifié complet avait réussi
  auparavant.
- Suite Linux du pont et tests vidéo synthétiques : **79 réussis, 1 ignoré**
  sur 80, le test borné exigeant des fichiers cgroup v2 absents. Déconnexions
  et reconnexions, budgets v2/v3 conservés, H.264, timestamps et changement de
  résolution sont couverts par des tests locaux, sans caméra réelle.
- Après réception du résultat v3 bloqué, les deux tests ciblés de refus des
  anciens formats sans modification et de conservation d'un blocage après
  redémarrage ont réussi.
- Suite de l'intégration HA 2026.11.2 isolée : **14 réussis**. Avertissement
  non bloquant sur l'absence de `libturbojpeg` dans cet environnement.
- Image officielle HA 2026.11.2 sur réseau Docker interne, avec configuration
  MediaMTX de déploiement : configuration et options, entités, disponibilité
  RTSP, aperçu JPEG, transition hors ligne et déchargement réussis. L'erreur
  `Stream ended` a été journalisée à l'arrêt volontaire de la vidéo fictive.
  La découverte est couverte par les tests unitaires, pas par ce scénario HA.
- Image officielle HA **2026.7.1** correspondant à la version et au digest
  de production : scénario HA synthétique réussi sur réseau interne avec la
  configuration MediaMTX corrigée. Le premier essai échouait à l'import :
  l'image vierge contient des fichiers `simplejson` vides ; la version réelle
  dispose de `simplejson` 4.1.1. Cette dépendance manquante a été installée
  uniquement dans un volume de test temporaire, supprimé ensuite. L'aperçu
  JPEG et la transition hors ligne ont réussi ; le journal `Stream ended`
  correspond à l'arrêt volontaire du flux fictif.
- Image locale du pont en conteneur `--network none`, système de fichiers
  en lecture seule, sans capacités, limité à 0,5 CPU et 512 Mo, volume
  temporaire UID 10001 : caméra fictive, authentification simulée,
  publication RTSP, 160 paquets, 154 images décodées, changement de résolution
  et redémarrage du gestionnaire avec conservation du fichier budget réussis.
  Ce test regroupe les processus dans un conteneur : il ne valide pas les
  deux services séparés ni le redémarrage Swarm. Le volume de test a été
  supprimé, sans toucher aux volumes réels.
- Aucun test de stabilité 24 h, d'accès caméra, de déploiement Swarm réel,
  d'exposition Internet effective ni d'intégration dans HA réel n'a été
  effectué. La version et l'état du budget ont été communiqués séparément par
  l'administrateur ; les autres champs du budget et le diagnostic complet du
  blocage restent inconnus.
- Pour cette mise à jour, les tests Linux WSL de `test_bridge_manager.py`
  passent (**5 réussis**), dont la création d'un budget v4 dans un sous-dossier
  depuis un répertoire racine neuf. `test_rtsp_bridge.py` passe (**33 réussis,
  4 ignorés** faute de PyAV/outils/cgroup v2). Le rendu `docker stack config`
  de la stack mise à jour passe sur Debian avec des valeurs fictives, sans
  déploiement. L'exécution Windows native des tests du gestionnaire ne peut
  pas importer `fcntl` ; elle a été remplacée par l'exécution Linux WSL.

## DÉCISION ACTUALISÉE — ABANDON ET ÉTAT INDÉPENDANT

Le propriétaire confirme l'abandon de l'ancien pilote Debian et de ses états
expérimentaux. Les budgets v2/v3 sont à archiver tels quels ; aucune migration
vers v4 n'est demandée ni préparée. Aucune connexion caméra n'est autorisée.

Nouvelle vérification passive du 17 novembre 2026 :

- Aucun service ou conteneur Netvue n'est présent dans Swarm/Docker, aucun
  processus visible du pont n'est actif et les ports TCP 8554 et 8765 sont
  libres. Swarm est actif. Aucun changement de service n'a été effectué.
- Huit répertoires d'état ont été inventoriés dans les anciens répertoires des
  phases 10 à 12. Ils contiennent huit `budget.json` et leurs fichiers
  `budget.lock`. Les budgets/verrous sont en mode `0600`, propriété UID/GID
  10001 ; les répertoires d'état sont en mode `0777`. Le contenu des fichiers
  n'a pas été lu.
- La session SSH est UID 1000 et `sudo -n` échoue. Elle ne peut donc pas lire
  ni archiver fidèlement les fichiers `0600` appartenant à 10001. Aucun droit
  n'a été contourné ou modifié ; aucune archive vérifiée ni nouveau répertoire
  persistant n'a été créé. Les anciens états restent intacts.
- La stack Portainer cible un nouveau répertoire vide distinct. Le gestionnaire
  accepte ce répertoire sans budget racine ; à l'ajout d'une caméra, il crée
  un budget v4 dans son sous-répertoire avant de démarrer la session. Cette
  action HA déclenche donc un contact caméra et reste différée.

Le [YAML Portainer](../deployment/stack.yaml) et son
[guide](../deployment/README.md) indiquent maintenant explicitement un
répertoire d'état neuf, vide et inscriptible par UID/GID 10001. L'archive des
anciens états ne doit jamais être montée comme volume de la nouvelle stack.
L'absence actuelle de processus et la disponibilité des ports sont observées ;
le contenu exact/version des budgets et la vérification d'une archive restent
non vérifiés faute de droits de lecture.

## TRANSITION HISTORIQUE ABROGÉE

Les étapes de migration et de réconciliation précédemment envisagées sont
annulées par la décision d'abandon ci-dessus. L'ancien état reste inchangé et
doit seulement être archivé ; le nouveau déploiement utilisera un chemin v4
vide et indépendant.

## ÉTAT FINAL ET PROCHAINE ÉTAPE

La préparation locale de la stack et de sa documentation est faite ; le
comportement d'initialisation v4 d'un répertoire frais est couvert par le test
du gestionnaire. Aucun état Debian n'a été modifié, archivé ou initialisé.
Aucun déploiement Portainer, changement Home Assistant ou contact caméra n'a
été effectué.

Le seul blocage technique immédiat est l'accès en lecture privilégié requis
pour créer et vérifier l'archive tout en préservant les permissions. Une
session administrateur autorisée doit archiver les huit répertoires d'état et
vérifier l'archive ; le nouveau chemin persistant devra ensuite être créé avec
UID/GID 10001. La validation du déploiement réel reste distincte et ne doit
pas démarrer de caméra.
