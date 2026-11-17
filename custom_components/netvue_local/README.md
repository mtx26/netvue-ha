# Netvue Local pour Home Assistant

Cette intégration ajoute une caméra Netvue et demande au service Netvue Bridge
de démarrer son flux. Elle ne communique pas elle-même avec la caméra pour
l'authentification LAN ; cette tâche reste au pont.

## Prérequis

- Netvue Bridge et MediaMTX déployés sur le même nœud que Home Assistant.
- Home Assistant utilise le réseau host afin que `127.0.0.1` désigne l'hôte.
- MediaMTX écoute sur `127.0.0.1:8554` et le gestionnaire du pont sur
  `127.0.0.1:8765`. Aucun de ces ports ne doit être publié sur une interface
  externe.
- Le réseau autorisé de `NETVUE_SOURCE` couvre les caméras à ajouter.

Voir le [guide Portainer](../../deployment/README.md) pour la stack. Le code
de cette intégration est le dossier `custom_components/netvue_local`.
Copiez-le sous `<config Home Assistant>/custom_components/netvue_local/`,
puis redémarrez Home Assistant.

## Ajouter une caméra

1. Dans **Paramètres → Appareils et services → Ajouter une intégration**,
   choisissez **Netvue Local**.
2. Saisissez l'adresse IPv4 privée et un nom affiché. L'intégration génère le nom de flux sans demander
   d'URL, de port ni d'identifiants RTSP, puis demande au pont de lancer la
   session.

L'adresse est saisie manuellement. Home Assistant ne met pas en œuvre le
protocole Netvue : seule la passerelle Go effectue la découverte LAN, le login
et la réception vidéo.

Pour `192.168.10.2`, le nom de flux est `netvue_192_168_10_2` et l'URL est
`rtsp://127.0.0.1:8554/netvue_192_168_10_2`. Le nom dérivé de l'adresse est stable et distinct pour chaque caméra. Une
instance du pont Go conserve une session et un superviseur indépendant par
entrée HA ; la liste du pont est en mémoire et HA la restaure au redémarrage.
Il n'y a pas de nombre maximal codé en dur. La capacité pratique dépend des
ressources disponibles et des caméras et MediaMTX.

L'entité caméra lit le flux via Home Assistant Stream. Un capteur binaire
indique si l'annonce SDP RTSP/H.264 est disponible. L'indisponibilité peut
signifier que le pont démarre, que MediaMTX est en panne ou que la session
caméra échoue ; elle ne permet pas à elle seule d'en déterminer la cause.

## Modifier ou supprimer

Le nom affiché se modifie depuis l'appareil ou l'entité dans Home Assistant.
L'option de l'intégration permet de changer l'adresse IPv4 ; le flux précédent
est arrêté puis le pont démarre celui de la nouvelle adresse. Supprimer
l'intégration demande au pont d'arrêter et de supprimer sa session.

Les entrées doivent utiliser le nom et l'URL générés par l'intégration. Une
ancienne entrée sans nom géré ou avec une URL personnalisée est refusée ; elle
n'est ni convertie ni réécrite automatiquement.

## Dépannage et limites

- **Gestionnaire indisponible** : vérifiez que le service `netvue-bridge` est
  `1/1`, sur le même hôte/réseau que Home Assistant, et écoute sur
  `127.0.0.1:8765`. L'entrée est réessayée par Home Assistant.
- **Flux indisponible** : vérifiez MediaMTX sur `127.0.0.1:8554`, l'état de
  l'entrée dans le gestionnaire et les journaux du pont.
- L'API du pont n'a pas d'authentification distincte et n'écoute que sur
  loopback. Les processus partageant le réseau hôte peuvent l'appeler ; ne
  publiez pas son port et gardez les services locaux dignes de confiance.
- Si la suppression de la session échoue pendant le déchargement, l'intégration
  conserve l'entrée chargée et signale l'erreur au lieu de laisser une session
  orpheline sans configuration HA.
- La compatibilité des autres modèles Netvue et la disponibilité prolongée
  doivent être validées séparément. Les tests synthétiques n'utilisent aucune
  caméra réelle.
