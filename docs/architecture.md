# Architecture Netvue Local

## Architecture actuellement active — passerelle Go

Depuis la phase 26, une seule instance de `netvue-go` gère toutes les caméras.
Son gestionnaire en mémoire crée une session `RunContinuous` indépendante par
adresse ; il n'y a pas de nombre maximal codé en dur ni de second superviseur.
MediaMTX est partagé. Les chemins RTSP sont déterministes, et chaque opération
sur une caméra reste isolée des autres sessions.

L'API `net/http` du pont écoute sur `127.0.0.1:8765`. Elle permet à
Home Assistant d'ajouter, consulter, arrêter, redémarrer explicitement et
supprimer une caméra. La publication utilise MediaMTX sur
`127.0.0.1:8554`. L'API n'ajoute pas d'authentification distincte et ne doit
pas être exposée hors de l'hôte.

Home Assistant conserve ses config entries et sert de source persistante ; le
registre Go est volatile et est réconcilié après redémarrage du pont.
L'intégration Python ne contient plus le protocole Netvue : elle gère les
config entries, les entités, l'état RTSP et le contrat HTTP local. L'adresse
IPv4 est saisie manuellement ; seule la session Go découvre la caméra.
Le déchargement HA supprime la session Go et conserve l'entrée si la
suppression échoue.

Voir le [plan Go](./go-plan.md), le [guide Portainer Go](../deployment/go-portainer.md)
et le [rapport phase 26](../findings/phase26-multicamera-go.md).

## Architecture Python historique

Les sections suivantes décrivent l'ancienne passerelle Python et son
superviseur à budgets persistants. Elles sont conservées comme référence ; ces
mécanismes ne sont pas utilisés par la stack Go de phase 26.

### Périmètre

L'ancien projet Python comprend sa passerelle, son API de contrôle loopback,
l'intégration Home Assistant et MediaMTX.

```text
Home Assistant -- HTTP loopback 127.0.0.1:8765 --> gestionnaire --> superviseur v4
                                                         --> authentification LAN --> caméra
Home Assistant <-- RTSP 127.0.0.1:8554 <-- MediaMTX <-- H.264/RTSP/TCP
```

Le transport Netvue local n'est pas chiffré. RTSP est limité à la boucle locale dans la configuration fournie, mais reste accessible aux autres processus partageant cet espace réseau. Aucun accès Internet ni enregistrement ne sont nécessaires.

### Passerelle Python

| Module | Responsabilité |
|---|---|
| [manager.py](../netvue_bridge/manager.py) | API HTTP loopback, validation des demandes, allocation des budgets par caméra et cycle de vie des sessions. |
| [protocol.py](../netvue_bridge/protocol.py) | Trames, JSON, validation des en-têtes et lectures bornées. |
| [discovery.py](../netvue_bridge/discovery.py) | Découverte UDP unicast, validation de l'émetteur et extraction des paramètres de connexion. |
| [connection.py](../netvue_bridge/connection.py), [authentication.py](../netvue_bridge/authentication.py) | Socket TCP et authentification LAN ; la clé ne figure pas dans la représentation de l'objet découvert. |
| [h264.py](../netvue_bridge/h264.py) | Découpage Annex B, en-tête de slice, contrôle des frontières, interpolation bornée et horloge PTS. |
| [rtsp.py](../netvue_bridge/rtsp.py) | Validation de l'URL de publication, état H.264, décodeur PyAV et remux RTSP. |
| [stream.py](../netvue_bridge/stream.py) | Exécute une tentative de découverte, connexion, authentification et réception LIVE, puis ferme la publication ; ne gère pas les reconnexions. |
| [budget.py](../netvue_bridge/budget.py) | État persistant v4 unique, verrou et backoff ; toute autre version est refusée sans modification. |
| [continuous.py](../netvue_bridge/continuous.py) | Supervision unique, travailleur vidéo, veille et mode de test borné. |
| [check_rtsp.py](../netvue_bridge/check_rtsp.py) | Diagnostic borné avec MediaMTX et lecteurs externes ; le flux caméra passe par le mode test du superviseur et son budget v4. |

Les dépendances internes vont de l'orchestration vers le protocole et la vidéo ; aucun cycle d'import relatif n'a été détecté par l'analyse statique des modules Python. Le module de protocole ne dépend ni de Home Assistant ni de MediaMTX. L'intégration HA ne met plus en œuvre la découverte UDP ; celle de la passerelle Python historique reste inchangée.

### Cycle historique d'une session

Le superviseur détient le verrou du budget, vérifie son état, puis réserve une tentative durablement **avant** la découverte. `stream.run_session` exécute une tentative : la passerelle émet une requête UDP, ouvre une connexion TCP, envoie une authentification puis une seule demande LIVE. La réception classe les commandes et paquets média ; le publieur attend SPS, PPS et IDR, vérifie le décodage puis remuxe les images en RTSP/TCP. La socket et le publieur sont fermés en fin de session ou sur erreur. Le worker retourne l'étape réessayable ou un résultat bloquant ; seul `continuous.py` décide de la reconnexion et applique le backoff v4.

`H264Publisher` conserve l'état du décodeur, les tampons et la publication ; les calculs de frontières de trames et d'interpolation PTS sont isolés dans `h264.py`. Il conserve les limites de mémoire, le rejet des images B, l'attente de l'IDR lors d'un changement de paramètres et les règles de réinitialisation PTS. La correction expérimentale des horodatages identiques ne résout que certains cas contrôlés ; les cas ambigus échouent explicitement. Ses hypothèses et limites sont détaillées dans [phase12b-timestamp-recovery.md](../findings/phase12b-timestamp-recovery.md). Aucun test synthétique ne constitue une validation sur la caméra.

Le verrou coordonne les processus partageant le même répertoire ; au démarrage, le superviseur vérifie aussi dans `/proc` qu'aucun travailleur connu ne tourne déjà pour la même caméra. Après un redémarrage, un `in_progress` n'est repris que si cette absence est vérifiée et que l'horloge est cohérente ; la reprise le transforme en nouvel essai réessayable avec backoff. Un processus actif, un état corrompu, une horloge ambiguë ou `blocked` arrête le démarrage sans modifier l'état bloqué. Ne jamais contourner un état avec une copie ou un nouveau répertoire. Le budget v4 n'impose aucun quota journalier ou hebdomadaire ; seul le backoff progressif limite les erreurs transitoires.

### Contrat de l'ancien superviseur

`netvue_bridge.continuous` fournit `init`, `run`, `test` et une commande
interne `_session`. `run` est l'exploitation continue ; `test`
exige simultanément `--max-duration` et `--max-attempts`. Les deux modes
utilisent le même état v4, la même réservation et le même verrou. Le mode
borné n'efface ni ne remplace un état opérationnel.

Le format v4 retient le total, l'étape, le compteur d'échecs et le délai
requis, sans historique de quotas permanents. Les erreurs réseau et vidéo
réessayables suivent 5, 15, 60, 300 puis 900 s avec jitter optionnel ; une
session vidéo stable réinitialise le streak. Le refus d'authentification,
une erreur critique ou un résultat ambigu arrêtent le traitement. Les états
autres que v4 sont refusés explicitement et laissés inchangés. Aucun chemin
de migration ou de réinitialisation automatique n'est fourni.

Chaque exécution vérifie le RTSP loopback avant réservation. Le travailleur
signale étapes et progrès vidéo ; watchdog et seuil mémoire arrêtent le
processus ou laissent l'état à examiner. La reprise après arrêt récupère un
`in_progress` seulement après contrôle des processus et de l'horloge ; elle
respecte le backoff. Le verrou et le contrôle de processus ne garantissent pas
l'exclusivité entre hôtes ou espaces PID différents. Le mode `test` est celui
destiné aux essais synthétiques et ne constitue pas une autorisation de
contacter une caméra.

### Intégration Home Assistant et déploiement

L'intégration Python active, [netvue_local](../custom_components/netvue_local/README.md),
ajoute les caméras par adresse IPv4 manuelle et communique avec le gestionnaire
Go par HTTP loopback. `bridge_api.py` réconcilie les entrées absentes et
supprime la session au déchargement ; le coordinator sonde le RTSP et les
plateformes exposent la caméra et sa disponibilité. Le protocole Netvue n'est
pas implémenté dans cette intégration.

[deployment/stack.yaml](../deployment/stack.yaml) reste l'ancienne stack Python
et utilise encore ses budgets v4. Pour la passerelle actuelle, utiliser
[go-stack.yaml](../deployment/go-stack.yaml) avec le [guide Go Portainer](../deployment/go-portainer.md) ;
ces deux stacks ne doivent pas être démarrées simultanément sur le même hôte.
