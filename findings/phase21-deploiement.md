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
- Le package est toujours privé. Son passage en public s'effectue dans
  **Package settings → Change visibility**, séparément du dépôt privé.
  Il expose les versions historiques ; vérifier ensuite le pull anonyme
  avant d'utiliser l'image dans Portainer sans registre authentifié.
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
  **13 fichiers identiques sur 13**. Aucun pull anonyme n'a réussi : le
  package est encore privé.
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
  effectué. Les permissions relevées ne révèlent pas le contenu du budget.

## TRANSITION PROPOSÉE — EN ATTENTE D'ACCORD

L'inventaire passif a identifié les services et chemins ci-dessus. Il reste
à examiner, avec des droits de lecture légitimes, la version et l'état des
budgets réels ainsi que les variables Portainer nécessaires. Ne pas afficher
ni publier les secrets ou les budgets complets. Un administrateur disposant
des droits peut relever uniquement la version et l'état du budget actif,
sans modifier le fichier :

```sh
sudo python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print({k:d.get(k) for k in ("version","status","stage")})' \
  /home/mtx26/netvue-phase12-179f7438/real-state/budget.json
```

Le résultat ne révèle ni adresses ni identifiants ; il ne suffit pas à lui
seul à valider l'identité et la cohérence complètes du budget. Toute
réconciliation reste une opération séparée.

L'image correspondant au code validé a été publiée et téléchargée. La
visibilité publique du seul package GHCR et le pull anonyme restent à
vérifier ; le dépôt source reste privé. L'image publique contient le code du
pont. Ne pas utiliser le tag ancien comme image à jour.

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
   l'entrée effectivement observée est refusée par le nouveau composant et
   n'est pas migrée automatiquement. La suppression et le réajout nécessitent
   une décision explicite ; le réajout demande immédiatement une session au
   pont et ne doit donc pas être effectué sans autorisation caméra bornée.
   Toute copie du composant, modification de HA et tout redémarrage attendent
   une autorisation explicite distincte.

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

La correction locale, les tests synthétiques et la publication GHCR sont
terminés ; le package est encore privé. La validation Swarm réelle, la
transition et la validation HA réelle ne sont pas effectuées. Les prochaines
étapes sont le passage du package en public avec vérification du pull anonyme,
et une lecture légitime des budgets réels. Après cet inventaire, présenter
le basculement précis et obtenir son autorisation avant toute intervention.

Le premier essai caméra 2 exigera une autorisation séparée indiquant le nombre
maximal de tentatives, la durée et les opérations permises : découverte,
authentification LAN, vidéo, RTSP, lecture HA, mesures et déconnexion. Les
limites de tentatives doivent être compatibles avec le mode de test choisi ;
ne pas démarrer une supervision continue sans borne pour cet essai.
