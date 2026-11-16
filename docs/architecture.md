# Architecture Netvue Local

## Périmètre

Le projet comprend deux composants indépendants : la passerelle Python et l'intégration Home Assistant. MediaMTX est un service distinct. L'intégration ne démarre pas la passerelle, ne gère pas ses budgets et ne configure pas MediaMTX.

```text
Caméra -- UDP découverte --> discovery
       -- TCP authentification/LIVE --> stream --> H.264 / PTS --> RTSP/TCP
                                                                  |
Home Assistant <-- lecture RTSP et sonde SDP <-- MediaMTX <-------+
```

Le transport Netvue local n'est pas chiffré. RTSP est limité à la boucle locale dans la configuration fournie, mais reste accessible aux autres processus partageant cet espace réseau. Aucun accès Internet ni enregistrement ne sont nécessaires.

## Passerelle

| Module | Responsabilité |
|---|---|
| [protocol.py](../netvue_bridge/protocol.py) | Trames, JSON, validation des en-têtes et lectures bornées. |
| [discovery.py](../netvue_bridge/discovery.py) | Découverte UDP unicast, validation de l'émetteur et extraction des paramètres de connexion. |
| [connection.py](../netvue_bridge/connection.py), [authentication.py](../netvue_bridge/authentication.py) | Socket TCP et authentification LAN ; la clé ne figure pas dans la représentation de l'objet découvert. |
| [video.py](../netvue_bridge/video.py), [client.py](../netvue_bridge/client.py) | Diagnostic historique LIVE et décodage borné en mémoire. |
| [h264.py](../netvue_bridge/h264.py) | Découpage Annex B, en-tête de slice, contrôle des frontières, interpolation bornée et horloge PTS. Les anciens symboles restent importables depuis `netvue_bridge.rtsp`. |
| [rtsp.py](../netvue_bridge/rtsp.py) | Validation de l'URL de publication, état H.264, décodeur PyAV et remux RTSP. |
| [stream.py](../netvue_bridge/stream.py) | Orchestration découverte, connexion, authentification, LIVE et fermeture de la publication. |
| [supervisor.py](../netvue_bridge/supervisor.py) | Budget persistant borné, réservation et watchdog de durée. |
| [continuous.py](../netvue_bridge/continuous.py) | Budget glissant, supervision du processus enfant, veille vidéo et contrôle des ressources. |
| [check_rtsp.py](../netvue_bridge/check_rtsp.py) | Diagnostic borné avec MediaMTX et lecteurs externes ; l'analyse CLI, les commandes de lecture et la validation des résultats sont séparées de l'orchestration. Ce n'est pas le superviseur de production. |

Les dépendances internes vont de l'orchestration vers le protocole et la vidéo ; aucun cycle d'import relatif n'a été détecté par l'analyse statique des modules Python. Le module de protocole ne dépend ni de Home Assistant ni de MediaMTX. Les deux implémentations de découverte sont volontairement distinctes : l'intégration HA doit être installable sans le paquet de la passerelle, et sa découverte est une diffusion utilisateur plutôt que l'unicast du pont.

## Cycle d'une session

Le superviseur détient le verrou du budget, vérifie son état, puis réserve une tentative durablement **avant** la découverte. La passerelle émet une requête UDP, ouvre une connexion TCP, envoie une authentification puis une seule demande LIVE. La réception classe les commandes et paquets média ; le publieur attend SPS, PPS et IDR, vérifie le décodage puis remuxe les images en RTSP/TCP. La socket et le publieur sont fermés en fin de session ou sur erreur.

`H264Publisher` conserve l'état du décodeur, les tampons et la publication ; les calculs de frontières de trames et d'interpolation PTS sont isolés dans `h264.py`. Il conserve les limites de mémoire, le rejet des images B, l'attente de l'IDR lors d'un changement de paramètres et les règles de réinitialisation PTS. La correction expérimentale des horodatages identiques ne résout que certains cas contrôlés ; les cas ambigus échouent explicitement. Ses hypothèses et limites sont détaillées dans [phase12b-timestamp-recovery.md](../findings/phase12b-timestamp-recovery.md). Aucun test synthétique ne constitue une validation sur la caméra.

Le verrou ne coordonne que les processus partageant le même répertoire. Ne pas contourner un état `blocked` ou `in_progress` avec une copie ou un nouveau répertoire. Les quotas, formats persistants et procédures de reprise restent inchangés ; leur réconciliation relève de la phase 15.

## Contrats des superviseurs

| | Superviseur borné | Superviseur continu |
|---|---|---|
| Entrée | `netvue_bridge.supervisor` : `init`, `run`, `_worker` interne | `netvue_bridge.continuous` : `init`, `run`, `_session` interne |
| État | `Budget`, format version 2, plafond initial explicite | `RollingBudget`, format version 3, fenêtres glissantes : 3 tentatives par 24 h et 8 par 7 jours |
| Tentative | Verrou exclusif et réservation persistante avant découverte ; une tentative réseau par appel de `run_sessions` | Même principe, avec étapes et échanges d'événements entre superviseur et processus de session |
| Échec | Reprise limitée aux erreurs classées réessayables, après délai progressif ; authentification refusée ou erreur protocole bloquante | Erreur réessayable suivie d'un délai progressif avec jitter ; échec non réessayable bloque, résultat ambigu laisse la réservation active |
| Arrêt | `run` exige `--max-duration` et lance un watchdog ; une interruption peut laisser `in_progress` | Arrêt SIGTERM et watchdog surveillent le processus enfant ; une interruption ambiguë laisse `in_progress` |

Les deux budgets valident leur format, leur source et leur cible, utilisent les mêmes primitives de verrouillage et d'écriture atomique, mais **ne sont pas interchangeables**. `RollingBudget` réutilise ces primitives de `Budget` et redéfinit la validation et la politique ; il ne consulte pas un autre répertoire. Chaque superviseur vérifie d'abord la disponibilité du port RTSP loopback, puis réserve avant d'appeler la session. Cette vérification locale ne valide pas le contenu du flux. Une protection globale entre répertoires n'est pas implémentée.

Les événements internes du continu portent une étape, des métriques vidéo ou un résultat classé. Leur perte, un arrêt imprévu ou des métriques cgroup invalides peuvent empêcher la reprise et imposent une analyse manuelle ; ils ne constituent pas une autorisation de réinitialiser l'état. Les CLI internes ne sont pas une voie de contournement des budgets.

## Home Assistant et déploiement

[config_flow.py](../custom_components/netvue_local/config_flow.py) propose une découverte UDP déclenchée par l'utilisateur ou une saisie manuelle. Il génère une URL loopback depuis le nom technique, affiche cette URL et demande confirmation avant la sonde RTSP. Les anciennes URL enregistrées restent utilisées. [coordinator.py](../custom_components/netvue_local/coordinator.py) sonde périodiquement l'annonce SDP ; [camera.py](../custom_components/netvue_local/camera.py) et [binary_sensor.py](../custom_components/netvue_local/binary_sensor.py) exposent le flux et sa disponibilité. Une annonce SDP ne prouve ni le décodage ni l'identité de la caméra.

[stack.yaml](../deployment/stack.yaml) utilise le réseau host externe, une image préconstruite et un budget persistant ; [stack-continuous.yaml](../deployment/stack-continuous.yaml) sélectionne le superviseur continu. Les fichiers de déploiement ne sont pas exécutés automatiquement par ce dépôt. Lire [deployment/README.md](../deployment/README.md) avant toute opération autorisée.
