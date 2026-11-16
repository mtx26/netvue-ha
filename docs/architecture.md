# Architecture Netvue Local

## Périmètre

Le projet comprend la passerelle Python, son API de contrôle loopback, l'intégration Home Assistant et MediaMTX. L'intégration demande au gestionnaire du pont de démarrer ou d'arrêter les sessions ; le pont reste seul responsable des budgets et de l'authentification LAN Netvue.

```text
Home Assistant -- découverte UDP utilisateur --> caméra
       -- HTTP loopback 127.0.0.1:8765 --> gestionnaire --> superviseur v4
                                                         --> authentification LAN --> caméra
Home Assistant <-- RTSP 127.0.0.1:8554 <-- MediaMTX <-- H.264/RTSP/TCP
```

Le transport Netvue local n'est pas chiffré. RTSP est limité à la boucle locale dans la configuration fournie, mais reste accessible aux autres processus partageant cet espace réseau. Aucun accès Internet ni enregistrement ne sont nécessaires.

## Passerelle

| Module | Responsabilité |
|---|---|
| [manager.py](../netvue_bridge/manager.py) | API HTTP loopback, validation des demandes, allocation des budgets par caméra et cycle de vie des sessions. |
| [protocol.py](../netvue_bridge/protocol.py) | Trames, JSON, validation des en-têtes et lectures bornées. |
| [discovery.py](../netvue_bridge/discovery.py) | Découverte UDP unicast, validation de l'émetteur et extraction des paramètres de connexion. |
| [connection.py](../netvue_bridge/connection.py), [authentication.py](../netvue_bridge/authentication.py) | Socket TCP et authentification LAN ; la clé ne figure pas dans la représentation de l'objet découvert. |
| [video.py](../netvue_bridge/video.py) | Analyse du flux LIVE et décodage borné en mémoire. |
| [h264.py](../netvue_bridge/h264.py) | Découpage Annex B, en-tête de slice, contrôle des frontières, interpolation bornée et horloge PTS. Les anciens symboles restent importables depuis `netvue_bridge.rtsp`. |
| [rtsp.py](../netvue_bridge/rtsp.py) | Validation de l'URL de publication, état H.264, décodeur PyAV et remux RTSP. |
| [stream.py](../netvue_bridge/stream.py) | Travailleur interne de découverte, connexion, authentification, LIVE et fermeture de la publication ; pas de point d'entrée de session autonome. |
| [budget.py](../netvue_bridge/budget.py) | État persistant v4 unique, verrou, migration auditée et backoff. |
| [continuous.py](../netvue_bridge/continuous.py) | Supervision unique, travailleur vidéo, veille et mode de test borné. |
| [check_rtsp.py](../netvue_bridge/check_rtsp.py) | Diagnostic borné avec MediaMTX et lecteurs externes ; le flux caméra passe par le mode test du superviseur et son budget v4. |

Les dépendances internes vont de l'orchestration vers le protocole et la vidéo ; aucun cycle d'import relatif n'a été détecté par l'analyse statique des modules Python. Le module de protocole ne dépend ni de Home Assistant ni de MediaMTX. Les deux implémentations de découverte sont volontairement distinctes : l'intégration HA doit être installable sans le paquet de la passerelle, et sa découverte est une diffusion utilisateur plutôt que l'unicast du pont.

## Cycle d'une session

Le superviseur détient le verrou du budget, vérifie son état, puis réserve une tentative durablement **avant** la découverte. La passerelle émet une requête UDP, ouvre une connexion TCP, envoie une authentification puis une seule demande LIVE. La réception classe les commandes et paquets média ; le publieur attend SPS, PPS et IDR, vérifie le décodage puis remuxe les images en RTSP/TCP. La socket et le publieur sont fermés en fin de session ou sur erreur.

`H264Publisher` conserve l'état du décodeur, les tampons et la publication ; les calculs de frontières de trames et d'interpolation PTS sont isolés dans `h264.py`. Il conserve les limites de mémoire, le rejet des images B, l'attente de l'IDR lors d'un changement de paramètres et les règles de réinitialisation PTS. La correction expérimentale des horodatages identiques ne résout que certains cas contrôlés ; les cas ambigus échouent explicitement. Ses hypothèses et limites sont détaillées dans [phase12b-timestamp-recovery.md](../findings/phase12b-timestamp-recovery.md). Aucun test synthétique ne constitue une validation sur la caméra.

Le verrou coordonne les processus partageant le même répertoire ; au démarrage, le superviseur vérifie aussi dans `/proc` qu'aucun travailleur connu ne tourne déjà pour la même caméra. Après un redémarrage, un `in_progress` n'est repris que si cette absence est vérifiée et que l'horloge est cohérente ; la reprise le transforme en nouvel essai réessayable avec backoff. Un processus actif, un état corrompu, une horloge ambiguë ou `blocked` arrête le démarrage sans modifier l'état bloqué. Ne jamais contourner un état avec une copie ou un nouveau répertoire. Le budget v4 n'impose aucun quota journalier ou hebdomadaire ; seul le backoff progressif limite les erreurs transitoires.

## Contrat du superviseur unique

`netvue_bridge.continuous` fournit `init`, `run`, `test`, `migrate` et une
commande interne `_session`. `run` est l'exploitation continue ; `test`
exige simultanément `--max-duration` et `--max-attempts`. Les deux modes
utilisent le même état v4, la même réservation et le même verrou. Le mode
borné n'efface ni ne remplace un état opérationnel.

Le format v4 retient le total, l'étape, le compteur d'échecs et le délai
requis, sans historique de quotas permanents. Les erreurs réseau et vidéo
réessayables suivent 5, 15, 60, 300 puis 900 s avec jitter optionnel ; une
session vidéo stable réinitialise le streak. Le refus d'authentification,
une erreur critique ou un résultat ambigu arrêtent le traitement. Les états
v2/v3 ne sont migrés que par `migrate`, avec audit écrit avant remplacement ;
un v2 devient bloqué par prudence et un état v3 ambigu reste bloqué. Cette
commande n'a pas été exécutée sur les données réelles.

Chaque exécution vérifie le RTSP loopback avant réservation. Le travailleur
signale étapes et progrès vidéo ; watchdog et seuil mémoire arrêtent le
processus ou laissent l'état à examiner. La reprise après arrêt récupère un
`in_progress` seulement après contrôle des processus et de l'horloge ; elle
respecte le backoff. Le verrou et le contrôle de processus ne garantissent pas
l'exclusivité entre hôtes ou espaces PID différents. Le mode `test` est celui
destiné aux essais synthétiques et ne constitue pas une autorisation de
contacter une caméra.

## Home Assistant et déploiement

[config_flow.py](../custom_components/netvue_local/config_flow.py) propose une découverte UDP déclenchée par l'utilisateur ou un ajout manuel. La sélection démarre le gestionnaire via [bridge_api.py](../custom_components/netvue_local/bridge_api.py) ; une URL et un nom de flux déterministes sont associés à chaque IPv4. Le gestionnaire démarre un superviseur et un budget distincts par caméra, sans plafond logiciel fixe ; la capacité pratique dépend des ressources allouées au service. L'API est sans authentification mais écoute uniquement sur 127.0.0.1:8765 ; elle est accessible aux processus partageant l'espace réseau de l'hôte. [coordinator.py](../custom_components/netvue_local/coordinator.py) sonde périodiquement le RTSP ; [camera.py](../custom_components/netvue_local/camera.py) et [binary_sensor.py](../custom_components/netvue_local/binary_sensor.py) exposent l'entité caméra et l'état de disponibilité. Les anciennes URL non standard restent lues sans être reconfigurées par le gestionnaire.

[stack.yaml](../deployment/stack.yaml) épingle les deux services au nœud Home Assistant, utilise le réseau host, ne publie aucun port et conserve les budgets v4 dans le volume d'état. MediaMTX reste lié à `127.0.0.1:8554` ; l'API du pont écoute sur `127.0.0.1:8765`. Lire le guide [Home Assistant](../custom_components/netvue_local/README.md) et [deployment/README.md](../deployment/README.md) avant toute opération autorisée.
