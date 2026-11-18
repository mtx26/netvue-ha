# Phase 15A — simplification finalisée hors ligne

## Résultat

**Code hors ligne : phase 15A finalisée.** Le pont n'a plus qu'un superviseur
(`netvue_bridge.continuous`) et un format persistant v4 (`netvue_bridge.budget`).
Le mode `run` est continu ; `test` exige une durée et un nombre maximal
d'essais et passe par le même budget, verrou et watchdog.

Le format v4 retire les quotas permanents et conserve un backoff progressif
persistant (5, 15, 60, 300, 900 s, jitter optionnel), réinitialisé après
publication stable. Les erreurs vidéo/réseau restent réessayables ;
authentification refusée, erreurs critiques, état invalide, reboot ou
interruption ambiguë restent bloquants.

La migration v2/v3 est une commande explicite, jamais automatique. Elle écrit,
synchronise et relit un audit avant le remplacement atomique de l'état. Un
budget v2 est migré bloqué faute d'historique temporel. Un v3 `ready` ne garde
son état et son backoff que si son boot et son horloge concordent ; les états
bloqués, en cours ou ambigus restent bloqués. Aucun budget réel n'a été lu,
migré ou modifié.

## Code retiré et consolidation

- Supprimé `netvue_bridge/supervisor.py` : ancien superviseur, budget v2,
  backoff dupliqué et commande de réconciliation vidéo.
- Supprimé `tests/test_supervisor.py` et ses assertions propres au budget v2.
- Supprimé `deployment/stack-continuous.yaml` : la stack de production continue
  est maintenant unique. La stack synthétique de tests est conservée.
- Remplacé `RollingBudget` v3 dans `continuous.py` et le `Budget` v2 par une
  classe d'état commune dans `budget.py`.
- Supprimé les quotas 3/24 h et 8/7 jours, le seuil de pause après échecs et
  la réconciliation manuelle qui pouvait débloquer un budget.
- Unifié la stack autour de `continuous.py`; actualisé les guides architecture,
  développement et déploiement.

`git diff HEAD` totalise **1 086 lignes ajoutées et 1 280 supprimées
(net −194)**. Ce total comprend aussi des changements locaux qui existaient
avant cette intervention ; il n'est donc pas un décompte isolé des seules
modifications de phase 15A. Les suppressions ci-dessus sont celles de cette
simplification.

## Vérifications

Sous Linux/WSL, sans caméra :

- Superviseur, état, migration, verrou, limites mémoire et watchdog :
  **16/16**.
- Pont/protocole : **8/8** ; récupération PTS : **10/10**.
- RTSP/H.264, MediaMTX, FFmpeg et FFprobe synthétiques : **29/29** ; suite
  répétée trois fois de plus séquentiellement, **3/3 réussies**.
- Reconnexion et collisions d'horodatages avec caméra fictive :
  **2/2**.
- Simulation longue : **20 000 images**, 1 999,9 s de PTS, 20 000 images
  décodées, quatre corrections de collisions, changement de résolution et
  aucune erreur de décodage.
- `git diff --check HEAD` : aucun problème d'espacement.

Une exécution parallèle de deux suites RTSP a échoué au démarrage de
MediaMTX : elles se disputaient le port fixe 8554. Les suites séquentielles
ont ensuite passé cinq exécutions séquentielles ; la documentation demande leur
exécution séquentielle. L'ancien incident de PTS non monotones rapporté
auparavant n'a pas été reproduit ; son origine propre ne peut pas être
confirmée par ces essais.

Les tests Home Assistant n'ont pas pu être exécutés : l'environnement HA
embarqué est incomplet (`homeassistant.util` manquant) et le moteur Docker
local n'est pas accessible. Les composants HA n'ont pas été modifiés.

## Limites de validation

**Déploiement réel : non effectué**, conformément aux limites de la demande.
Aucune caméra, stack, service ni donnée d'état réelle n'a été touché. Les
permissions Swarm, la migration d'un budget réel, le fonctionnement prolongé,
la coordination entre répertoires/hôtes et l'aperçu Home Assistant nécessitent
des validations distinctes et autorisées.
