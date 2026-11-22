# Phase 69 — Fiabilité des connexions et récupération vidéo

## Objectif

Établir ce que l’APK Netvue fait réellement lors des connexions instables,
comparer son comportement au pont Go/MediaMTX/WebRTC, corriger les défauts
confirmés et valider la reprise synthétique et sur l’appareil autorisé.

## Résultats APK vérifiés

APK Netvue 5.27.0 (versionCode 121868), SHA-256
`9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`,
hébergée sur le domaine officiel Netvue. La signature éditeur n’a pas été
vérifiée. L’analyse est statique et limitée par les 155 erreurs de la
décompilation antérieure ; aucune caméra ou session cloud n’a été contactée.
Les preuves détaillées et références de classes sont dans le
[complément APK phase 1](./phase1-report.md).

Constats de confiance forte dans les chemins examinés :

- L’application sépare CameraII, le LAN classique, le relais TCP et Agora.
  CameraII essaie LAN TCP, UDP punch puis relais ; les chemins classiques
  choisissent LAN, relais ou Agora selon capacités, préférences et erreurs.
  Il n’existe pas de bascule universelle LAN → cloud.
- Le LAN classique utilise TCP, avec un délai de connexion de 2 s et une
  détection d’inactivité de lecture à 30 s. Le code étudié n’établit pas de
  watchdog spécifique aux images figées.
- Le lecteur possède des files bornées (100 trames média, puis sélection
  d’image clé vidéo au-delà de 30 trames en attente). Ce sont des seuils de
  file, pas des durées réseau garanties.
- Des qualités AUTO/HD/BALANCE/SD existent, mais aucune adaptation
  démontrée à partir de mesures de perte ou de congestion n’a été trouvée.
  L’envoi périodique de keepalive observé concerne Agora ; il n’est pas établi
  pour le LAN classique.
- AWS IoT/MQTT apparaît dans la signalisation et la demande de tickets ; cela
  ne prouve pas que le média transite par MQTT. Les bibliothèques WebRTC,
  STUN/TURN et codecs présentes ne prouvent pas leur emploi pour chaque
  modèle Vigil.

Les choix de transport dépendent de la famille et du firmware. La caméra
Entrée n’a pas été utilisée pour déduire le comportement de l’application
Android.

## Architecture et changements

Le pont Go utilise le LAN Netvue, publie le média en RTSP/TCP vers MediaMTX,
puis Home Assistant le lit directement ou via sa carte WebRTC. MediaMTX
transporte et republie le flux local ; il ne bascule pas la connexion caméra
vers le cloud. TURN concerne le trajet navigateur–pont, pas le trajet
caméra–pont.

Le watchdog Go exige la première vidéo dans les 15 s et interrompt une
session après 30 s sans vidéo ; la supervision réessaie avec un backoff
plafonné à 30 s. Ce sont des politiques Go, pas des comportements attribués à
l’APK.

Corrections frontend réalisées :

- Une erreur d’envoi de candidat ICE n’est plus ignorée ; elle ferme la
  négociation et suit la classification existante des erreurs récupérables
  ou fatales.
- Le watchdog de la carte utilise le nombre d’images vidéo effectivement
  présentées (images totales moins images abandonnées) lorsque le navigateur
  expose ces compteurs. Ainsi, l’horloge de lecture audio seule ne masque plus
  un blocage vidéo. Si les compteurs ne sont pas disponibles, le suivi
  historique par `currentTime` reste utilisé.

Un test Go synthétique vérifie également qu’une trame vidéo fragmentée en
plusieurs écritures `net.Pipe`, espacées par un délai, est reconstituée et
publiée sans fausse interruption.

## Validation et mesures

- `node --test tests/frontend/*.test.mjs` : **92 réussis, 0 échec**.
  L’avertissement de test préexistant sur le chargement des préférences
  d’overlay apparaît, sans faire échouer la suite.
- `go test ./... -count=1`, `go test -race ./... -count=1` et `go vet ./...` :
  réussis.
- Tests ciblés Go : paquet fragmenté via `net.Pipe` avec 20 ms de délais
  cumulatifs, stall vidéo détecté en 100 ms dans le test, reconnexion simulée et
  republication en **1,72 s**. Les délais de ces tests sont synthétiques et
  réduits ; ils ne changent pas les seuils de production.
- Validation réelle, caméra Entrée : fermeture contrôlée de son unique socket
  TCP établi, puis reprise automatique en **4,41 s** ; compteur de reprises
  passé de 32 à 33, état `recovered` et horodatage vidéo avancé. Les trois
  sessions Netvue étaient de nouveau `connected/recovered` et les trois
  chemins MediaMTX prêts, avec deux pistes chacun. Aucun autre service n’a
  été redémarré et aucune donnée n’a été supprimée.

Une exécution intermédiaire a échoué au nettoyage `TempDir` de deux tests
manager sous Windows. Ces tests ont réussi seuls, puis la suite Go complète
et la suite avec `-race` ont réussi ; aucun fichier manager n’a été modifié.

## État final et limites

La reprise réseau du pont est confirmée une fois sur la caméra et le réseau
autorisés ; cela ne valide ni une panne Wi-Fi prolongée ni tous les modèles.
La perte ou la latence artificielle de paquets sur le réseau n’a pas été
injectée. Le test réel a interrompu le socket ciblé, pas l’extendeur ni le
routeur. Les comportements APK restent statiques et n’ont pas été observés
dans l’application.

Le déploiement du frontend n’a pas été effectué : les nouvelles ressources
doivent être livrées avec la version de chemin statique Home Assistant et
vérifiées dans un navigateur réel. Le pont MediaMTX partagé héberge trois
caméras ; aucun redémarrage global n’a été fait. Aucun benchmark CPU/mémoire
comparatif n’a été réalisé. Les changements ne modifient pas le chemin Go de
l’enregistrement ou de la détection IA ; ces fonctions n’ont pas fait l’objet
d’une nouvelle validation réelle.
