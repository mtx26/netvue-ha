# Phase 22 — refactoring global et lisibilité

## OBJECTIF

Examiner les modules actifs et simplifier les responsabilités dupliquées
sans altérer les garanties de la supervision, du protocole Netvue, de la
vidéo H.264/RTSP et de Home Assistant, ni contacter de caméra.

## ÉTAT INITIAL

La reprise de cette phase a examiné les 21 modules Python d'exécution de
`netvue_bridge/` et `custom_components/netvue_local/`, ainsi que
`scripts/apk_inventory.py`, les tests pertinents et la configuration de
déploiement. L'état initial contenait déjà des modifications non commitées
dans `.gitignore`, `budget.py`, `continuous.py` et ce rapport.
D'autres changements de déploiement et de test sont apparus en parallèle ;
ils n'appartiennent pas à cette reprise et ont été conservés.

- L'architecture est déjà organisée par responsabilités : `stream.py`
  réalise une tentative, `continuous.py` supervise, `budget.py` conserve
  l'état v4, `h264.py` gère les paquets et horloges, et `rtsp.py` publie.
- Le code de supervision et `H264Publisher` sont les zones les plus denses.
  Leur complexité correspond aux cycles de vie de processus, à la reprise
  persistante, au changement de paramètres H.264 et aux timestamps ; la
  découper davantage aurait dispersé ces règles.
- Avant cette reprise, trois listes de résultats réessayables étaient déjà
  regroupées dans le superviseur ; les listes du budget avaient aussi été
  regroupées. Les deux modules conservaient toutefois chacun une liste
  identique. Les transitions de reprise et de reconnexion répétaient le même
  calcul de backoff et la même mise à jour d'état.
- Aucune fonction morte ou ancienne interface publique à supprimer n'a été
  confirmée. L'action `_run` est appelée par le mode de test borné ; les
  découvertes HA et pont restent distinctes car elles ont des dépendances et
  des comportements différents.

## MODIFICATIONS

- Les regroupements préexistants des étapes et erreurs ont été conservés.
  `netvue_bridge/budget.py` fournit désormais l'unique liste des résultats
  réessayables utilisée également par `continuous.py`.
- Les deux chemins de transition vers `RECONNECT_WAIT` utilisent le même
  calcul de streak et de backoff dans `Budget._set_retry_wait`, sans changer
  leurs contrôles préalables ni l'écriture durable.
- Des tests caractérisent les étapes de reprise, les issues du worker et du
  superviseur, les résultats inconnus et le plafond du streak.
- Aucun module, fonction ou appelant n'a été supprimé. Aucun format d'état,
  contrat CLI, protocole, dépendance ou configuration de déploiement n'a
  changé.
- Les protections réseau, d'authentification, de verrouillage, de ressources,
  de délais et d'arrêt ont été conservées : aucune suppression n'était
  justifiée par cette refactorisation.

## FONCTIONNEMENT

La lecture v4, `Budget.finish_retryable()` et les trois contrôles des
résultats dans `continuous.py` utilisent `RETRYABLE_STAGES`. Après validation,
`Budget.recover_after_restart()` et `Budget.finish_retryable()` appliquent
`_set_retry_wait` : incrément borné, délai et état d'attente. La reprise
continue à choisir l'erreur selon l'étape interrompue ; les cas bloqués et
ambigus restent distincts et ne sont pas réessayés.

### Comparaison avant / après

| Avant | Après |
|---|---|
| Une liste de résultats dans le budget et une autre dans le superviseur. | Un seul contrat de résultats réessayables pour les deux. |
| Deux calculs de transition vers le backoff dans le budget. | Une transition commune, avec les validations et écritures propres à chaque appelant. |
| Reprise et issues principales couvertes, mais pas toutes les combinaisons d'étape et de redémarrage. | Les quatre étapes interrompues sont vérifiées avec et sans redémarrage, ainsi que les résultats inconnus. |

Par rapport à l'état initial non commité, une comparaison ligne à ligne
donne, hors ce rapport : **15 lignes ajoutées et 15 supprimées** dans les
deux modules de production, et **123 ajoutées et une supprimée** dans les
tests de supervision. Les changements initiaux déjà présents ne sont pas
comptés dans ces chiffres. Aucun module n'a été supprimé.

## PROTECTIONS RÉÉVALUÉES

- **Conservées :** RTSP et API de contrôle sur loopback ; validation des
  adresses ; une session par caméra ; verrou et état v4 durable ; échec fermé
  sur état ambigu ou authentification refusée ; limites du worker, délais,
  watchdog, fermeture des processus ; bornes vidéo et attente SPS/PPS/IDR.
- **Simplifiées :** les définitions répétées des résultats et les deux
  transitions identiques vers le backoff.
- **Supprimées :** aucune. Les protections observées couvrent des risques
  concrets et les retirer n'aurait pas réduit la complexité utile.

## TESTS

Dans WSL avec Python 3.14.4 et les environnements existants :

- Référence : suite complète `unittest discover -s tests -p 'test_*.py'`,
  **94 tests exécutés, dont 6 ignorés**. Après refactoring : **100 tests,
  dont 6 ignorés**.
- Avec PyAV 16.1 et MediaMTX/FFprobe locaux : **4 tests synthétiques réussis**
  couvrant les deux configurations RTSP, les collisions de timestamps et
  une reconnexion ; le test synthétique de rejet des B-frames a aussi réussi.
- Supervision et gestionnaire avec PyAV 16.1 : **36 tests réussis**.
- L'environnement HA émet un avertissement non bloquant sur `libturbojpeg`.
  Sous Windows, la suite complète échoue à l'import de `fcntl` et de Home
  Assistant ; elle n'est pas représentative du déploiement Linux.
- `git diff --check` : réussi. Les tests conditionnels non sélectionnés,
  notamment le diagnostic borné dépendant des cgroups, restent non vérifiés.
- Aucun déploiement, service de production, budget réel ni caméra n'a été
  touché ; la stabilité réelle et la compatibilité caméra 1 restent à valider.

## RELECTURE FINALE

Quatre sous-agents d'exploration ont examiné indépendamment l'architecture et
le LAN, la supervision, la vidéo et Home Assistant ; un agent de développement
a modifié la supervision et un autre a étudié le diagnostic RTSP, sans
modification retenue. Un agent distinct de relecture du diff n'a relevé aucun
défaut significatif ; ses tests sous Windows étaient ignorés, les tests Linux
ci-dessus ont été exécutés séparément.

La séparation des découvertes HA et pont, les validations aux frontières et
les protections vidéo ont été conservées. Le diagnostic RTSP partiel tolère
des données que le lecteur strict rejette : une factorisation ajouterait des
branches. Dans le manager, la première lecture du budget extrait la cible et
la seconde, sous verrou, vérifie l'état complet ; les fusionner exige de
modifier l'interface et de caractériser notamment les remplacements
concurrents et les erreurs de verrou. Ces pistes ne sont pas prouvées
équivalentes et ne sont donc pas appliquées.

## ÉTAT FINAL

La transition de backoff et le contrat des résultats réessayables sont
unifiés et les validations hors ligne décrites ci-dessus réussissent. Le gain
reste ciblé, non une réduction profonde de l'ensemble du code ; aucune
suppression plus large n'a été justifiée par l'analyse. La prochaine étape
est une validation réelle explicitement autorisée avant tout déploiement.
