# Phase 14 — refactoring et audit

## Verdict

**INCOMPLET.** La séparation H.264 et la simplification du publieur améliorent le projet, mais une refonte complète ne peut pas être déclarée achevée : des responsabilités restent mélangées, tous les tests HA ne sont pas exécutables dans l'environnement disponible et la compatibilité n'est pas entièrement démontrée.

## État initial et décisions

**PROUVÉ :** avant le refactoring, l'analyse Annex B, l'horloge PTS et la publication étaient dans `netvue_bridge/rtsp.py`. La validation des en-têtes était dupliquée entre datagrammes et flux TCP. Le diagnostic `check_rtsp.main` concentrait le lancement des processus, les sondes, les métriques et le nettoyage. Les budgets borné et glissant étaient déjà séparés mais leur verrou ne couvrait que leur propre répertoire.

**Priorités restantes :**

1. Préserver les budgets et réconcilier explicitement les états bloqués en phase 15 ; ne pas créer un autre répertoire pour les contourner.
2. Terminer l'environnement de test HA, puis vérifier les flux de configuration, les anciennes entrées, les entités, les traductions et le scénario isolé complet avec la version HA cible.
3. Simplifier progressivement `check_rtsp.main` (322 lignes) et `H264Publisher` (373 lignes), après tests de caractérisation des sorties, du nettoyage et des scénarios vidéo. Le publieur mélange encore reconfiguration, correction PTS, décodage, remux et métriques.
4. Documenter et tester les contrats partagés entre les deux superviseurs, sans changer formats, verrous, quotas ou CLI ; le budget glissant hérite du budget borné tout en ayant un format distinct.
5. Valider Docker et cgroups dans un environnement isolé adapté et préparer une validation réelle explicitement autorisée.

La duplication de découverte entre le pont et HA reste intentionnelle : l'intégration doit pouvoir être installée seule, et utilise une diffusion déclenchée par l'utilisateur plutôt que l'unicast du pont. La fusion imposerait une dépendance supplémentaire et pourrait changer le comportement.

## Modifications

- `netvue_bridge/h264.py` extrait la découpe Annex B, l'en-tête de slice et `TimestampClock` ; les anciens imports depuis `netvue_bridge.rtsp` sont conservés et vérifiés.
- `netvue_bridge/protocol.py` factorise la validation commune des en-têtes UDP et TCP ; un test caractérise les erreurs et leur ordre.
- `netvue_bridge/rtsp.py` découpe `_push_frame` entre mises à jour des paramètres, délai, démarrage et reconfiguration. Les traitements et erreurs ont été comparés à `HEAD` ; aucun changement intentionnel des PTS ni des limites.
- `tests/test_rtsp_bridge.py` allonge le scénario avec collisions pour permettre aux lecteurs de terminer ; il teste également la compatibilité des imports.
- `tests/test_continuous.py` et `tests/test_continuous_timestamp_integration.py` isolent les contrôles de signal et de vidéo du cgroup hôte en simulant les métriques. Les tests de vidéo utilisent des adresses loopback distinctes pour éviter les conflits après une exécution précédente. Les sorties de processus sont récupérées même en cas d'échec.
- `docs/architecture.md`, `docs/development.md` et les README donnent les responsabilités, les limites et les précautions. Les rapports historiques restent intacts.

## Validation hors ligne

**PROUVÉ :**

- Référence avant refactoring : Windows, 62 tests découverts, 31 ignorés et une erreur d'import HA ; WSL, 62 tests, 13 ignorés et une erreur d'import HA. Deux tests RTSP Windows avaient expiré ; un test SIGTERM WSL avait échoué puis réussi au rejeu.
- Dernière suite WSL avec PyAV et MediaMTX : 64 éléments, 63 réussis et une erreur d'import HA. Aucun test ignoré dans cette exécution ; l'élément en erreur est un module non importable, pas l'exécution des tests HA.
- Les 17 tests du superviseur continu et des collisions réussissent après isolation du cgroup. Les métriques cgroup y sont simulées : cela ne valide pas la lecture des vrais fichiers cgroup.
- Trois scénarios RTSP synthétiques supplémentaires réussissent ; les deux scénarios avec collisions publient chacun 80 images et FFprobe lit 40 paquets.
- Le scénario synthétique SPS/PPS, changement 64×64 → 128×64 et réinitialisation PTS réussit : 290 images publiées, 201 paquets FFprobe, 197 images décodées par FFmpeg, PTS croissants et aucune erreur signalée par les lecteurs.
- Syntaxe AST valide sur 32 fichiers Python ; `git diff --check` et sa variante indexée ne signalent aucune erreur d'espacement.

**NON VÉRIFIÉ :** les tests HA ne s'importent pas dans le venv WSL, et l'environnement Python portable possède une installation HA incomplète. Le moteur Docker local est indisponible ; tests HA et Docker isolés non exécutés pendant cet audit. La simulation longue conçue pour un conteneur avec montages `/ffmpeg` et cgroup n'a pas été rejouée. Ces limites ne prouvent pas une régression ni une réussite.

## Compatibilité et risques

**PROUVÉ par comparaison Git :** `stream.py`, `supervisor.py`, `continuous.py`, les configurations de déploiement, scripts et intégration HA n'ont pas changé par rapport à `HEAD`. Les budgets réels, services installés et caméras n'ont pas été touchés.

**DÉDUIT :** les CLI, formats de budget et configurations restent inchangés par les modifications examinées ; cela ne remplace pas un test de chaque combinaison. L'extraction H.264 conserve les objets accessibles par les anciens imports ; l'identité des symboles est testée.

**NON VÉRIFIÉ :** compatibilité HA effective après refactoring, Swarm et fonctionnement prolongé avec cgroup réel ; correction expérimentale des doublons PTS sur caméra ; comportement des lecteurs après toutes les configurations et anomalies. Les fragments et collisions ambiguës restent bloquants par conception.

## Phase 15

Examiner les budgets historiques et leur état sans modification, concevoir une réconciliation auditée séparée, puis solliciter une autorisation précise avant toute validation réelle bornée. Aucun déploiement ni reprise automatique n'est autorisé par ce rapport.

## Reprise de l'audit et du refactoring

**PROUVÉ :** l'audit ultérieur a confirmé les limites ci-dessus. Dans `check_rtsp.py`, la validation des sorties FFprobe/FFmpeg et la construction du résumé ont été extraites de `main` sans changer les seuils, messages ni forme de sortie. Des tests couvrent les valeurs limites, l'absence de keyframe ou d'images, le codec et la monotonie. `main` reste cependant volumineuse et mélange encore gestion de processus, synchronisation et présentation.

Dans `rtsp.py`, un échec de `output.close()` empêchait de libérer les tampons et références vidéo. Une clause `finally` les libère désormais tout en laissant remonter l'erreur initiale. Un test reproduit l'échec puis vérifie la libération. Les états, quotas, algorithmes PTS et commandes de production ne sont pas modifiés.

Le script `tests/timestamp_long_synthetic.py` accepte désormais les binaires FFmpeg/FFprobe portables et indique `null` si une métrique cgroup est absente. Ses chemins historiques restent les valeurs par défaut. Cela ne modifie pas les mesures ni les protections du superviseur réel.

**Résultats de cette reprise :**

- `unittest discover -s tests -p test_*.py -q` sous WSL avec PyAV, MediaMTX, FFmpeg et FFprobe : 68 éléments, 67 réussis et une erreur d'import du module HA, aucun test ignoré. Les deux tests d'intégration du superviseur simulent les métriques cgroup ; un test séparé vérifie la lecture sur des fichiers cgroup fictifs et le refus d'une limite infinie.
- Tests RTSP Windows : 24 éléments, dont 20 réussis et 4 ignorés sans dépendances multimédia ; 7 tests PTS supplémentaires réussis.
- Scénario RTSP synthétique de 30 secondes : 290 images publiées, 201 paquets FFprobe, 200 images décodées par FFmpeg ; un changement SPS, PPS et de résolution, ainsi qu'une réinitialisation PTS observés. PTS croissants et aucune erreur lecteur.
- Simulation accélérée : 20 000 images fictives publiées et décodées, 1 999,9 secondes de PTS simulés, quatre collisions corrigées, un changement SPS/PPS et de résolution et une réinitialisation PTS. Durée réelle 12,23 s ; RSS Python maximal 150 096 Kio. Métrique mémoire de crête cgroup indisponible ; le CPU cgroup partagé ne représente pas ce seul test.
- La tentative d'installation HA avec roues seulement échoue parce que `pyric` n'a pas de roue utilisable. Deux autres tentatives isolées sont arrêtées après 90 secondes ; aucune validation HA ne peut en être déduite. Docker local reste indisponible ; les scénarios Docker et HA isolés n'ont pas été rejoués.
- Aucun appareil réel, budget réel ni service installé n'a été touché.

**DÉDUIT :** l'architecture est plus lisible, mais la compatibilité complète exige encore les tests HA et Docker. Les optimisations supplémentaires du publieur et des superviseurs doivent être précédées de tests de caractérisation et préserver leurs contrats sensibles.

**Verdict : INCOMPLET, qualité estimée 6,5/10.** Points forts : séparation H.264, limites et fermetures explicites, nombreux tests synthétiques et compatibilité des imports vérifiée. Points faibles : classe de publication et diagnostic encore volumineux, duplication volontaire de découverte à maintenir, couverture HA et environnement Docker indisponibles, cgroup réel et caméra non vérifiés.

## Suite de la phase 14 — audit final

Cette section actualise les résultats et le verdict précédents, sans effacer leur historique.

### Modifications supplémentaires

**PROUVÉ :** `h264.py` isole maintenant la vérification préliminaire des frontières H.264 et le calcul borné d'interpolation ; `rtsp.py` conserve la machine d'état, le parseur PyAV et la publication. Les valeurs, messages d'erreur, contrôles et symboles précédemment importables restent conservés. Deux tests directs couvrent le retour modulo 32 bits et les cas insuffisants ou dangereux ; les scénarios complets existants couvrent la collaboration avec le publieur.

Dans `check_rtsp.py`, l'analyse CLI, la construction des commandes des lecteurs et la vérification des résultats image/HA sont séparées de la boucle de supervision. Des tests couvrent les arguments, les options de lecteurs, les dimensions, l'annonce HA, les erreurs et les seuils. `main` passe de 322 lignes dans l'audit initial à 201 : elle coordonne encore les processus et leur nettoyage. `H264Publisher` reste une classe d'environ 360 lignes : les états du décodeur, de reconfiguration et de publication restent couplés ; c'est une limite de maintenabilité, même si les étapes sont maintenant nommées.

`docs/architecture.md` précise les contrats respectifs des budgets v2 et v3, leurs réservations, quotas, états, verrous, échecs et limites de coordination. Les implémentations de découverte du pont et de HA ne sont pas fusionnées : l'une est unicast et l'autre est une diffusion utilisateur, et HA est distribué séparément.

### Tests et compatibilité

**PROUVÉ :**

- Les arguments `argparse.add_argument` de `client`, `stream`, `check_rtsp`, `supervisor` et `continuous` ont été comparés par AST au commit initial `e4b4bc5` : identiques. Les fichiers de déploiement, l'intégration HA et les deux superviseurs ne diffèrent pas de ce commit. Les symboles H.264 historiques importés de `rtsp` font l'objet d'un test d'identité ; cela ne prouve pas tous les usages externes possibles.
- Sur Debian, 73 tests du pont réussissent dans l'image existante `netvue-bridge:phase12b-179f7438` avec **sources actuelles montées en lecture seule** ; 13 tests HA réussissent dans l'image existante HA 2026.7.1. Les conteneurs sont temporaires, réseau `none`, sans port publié, sans budgets réels ni configuration HA réelle. Leurs limites étaient respectivement 512 Mio/1 CPU et 512 Mio/0,5 CPU. Les commandes utilisaient `python -B -W error::ResourceWarning -m unittest` pour le pont et `python -B -m unittest discover -s /workspace/tests -p test_home_assistant_integration.py -v` pour HA. Aucune image du pont n'a été reconstruite pour cette phase.
- Le test HA complet isolé réussit : création et options via HA, entités, disponibilité RTSP, aperçu JPEG Stream, arrêt du flux et déchargement. Le journal de HA fictif comporte une erreur Zeroconf/analytics liée à l'absence d'interface multicast et un message de fin de flux après l'arrêt volontaire ; ces événements ne démontrent pas le comportement de l'instance installée.
- La simulation RTSP Docker avec SPS/PPS, résolution 64×64 → 128×64 et PTS réinitialisé réussit : 290 images publiées, 201 paquets FFprobe et 193 images décodées par FFmpeg ; horodatages croissants.
- La simulation accélérée Docker publie et décode 20 000 images fictives ; 1 999,9 secondes de PTS simulés, quatre collisions corrigées et une réinitialisation. Crête cgroup : 139 063 296 octets pour le conteneur de test, limité à 512 Mio. Ce n'est pas une preuve de fonctionnement réel de 33 minutes.
- La lecture des métriques cgroup réelles est vérifiée dans un autre conteneur temporaire limité à 256 Mio. Les tests de collisions du superviseur utilisent cependant des métriques simulées ; le fonctionnement prolongé du superviseur avec cgroup réel n'est pas prouvé par ces tests.
- `docker stack config` valide le rendu fusionné des deux fichiers Swarm avec des valeurs fictives, sans déployer. Syntaxe des 32 fichiers Python valide ; `git diff --check` et sa variante indexée passent.

Reproduction : préparer une copie de travail de ces sources et des outils portables sous un répertoire temporaire privé, monter uniquement cette copie et les binaires FFmpeg/MediaMTX en lecture seule dans les images citées. Utiliser `--network none`, aucun `-p`, une racine en lecture seule et un `/tmp` temporaire. Les tests du pont reçoivent `PYTHONPATH=/workspace:/workspace/tests`, `NETVUE_MEDIAMTX=/mediamtx` et `NETVUE_FFPROBE=/ffmpeg/ffprobe`. Ils créent des budgets et adresses **fictifs** et consomment CPU, mémoire et espace temporaire. Ne jamais monter les budgets, la configuration HA ou les services de production.

**NON VÉRIFIÉ :** exécution réelle d'une stack Swarm issue de ce code, image nouvellement construite, interface HA installée, stabilité continue et correction PTS sur caméra réelle. Les tests isolés utilisent des images existantes et ne remplacent pas ces validations. Aucune caméra, budget réel ni service installé n'a été contacté ou modifié.

**Verdict : INCOMPLET, qualité estimée 7,5/10.** Les tests hors ligne et la séparation se sont améliorés et les contrats sensibles sont documentés. Le publieur et l'orchestration du diagnostic restent volumineux ; des usages externes et le déploiement réel ne sont pas entièrement caractérisés. Ne pas traiter ce score comme une certification de production.
