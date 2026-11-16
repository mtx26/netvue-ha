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
2. Choisissez **Rechercher des caméras** ou **Ajouter manuellement**.
3. La découverte demande une interface réseau puis envoie une seule diffusion
   UDP sur son sous-réseau privé. Elle affiche les adresses ayant répondu ;
   elle ne garantit pas que le modèle soit compatible. Sinon, saisissez son
   adresse IPv4 manuellement.
4. Donnez un nom affiché. L'intégration génère le nom de flux sans demander
   d'URL, de port ni d'identifiants RTSP, puis demande au pont de lancer la
   session.

Pour `192.168.10.2`, le nom de flux est `netvue_192_168_10_2` et l'URL est
`rtsp://127.0.0.1:8554/netvue_192_168_10_2`. Le nom dérivé de l'adresse est
stable et distinct pour chaque caméra. Le pont crée une session, un
superviseur et un budget v4 persistant par appareil ; il n'impose pas de
nombre maximal de caméras. La capacité pratique dépend des ressources CPU/RAM
allouées au service et de la capacité des caméras et de MediaMTX.

L'entité caméra lit le flux via Home Assistant Stream. Un capteur binaire
indique si l'annonce SDP RTSP/H.264 est disponible. L'indisponibilité peut
signifier que le pont démarre, que MediaMTX est en panne ou que la session
caméra échoue ; elle ne permet pas à elle seule d'en déterminer la cause.

## Modifier ou supprimer

Le nom affiché se modifie depuis l'appareil ou l'entité dans Home Assistant.
L'option de l'intégration permet de changer l'adresse IPv4 ; le flux précédent
est arrêté puis le pont démarre celui de la nouvelle adresse. Supprimer
l'intégration arrête sa session et conserve son budget sur le volume d'état.

Les anciennes entrées compatibles avec le flux loopback continuent à être
gérées par le pont. Une ancienne URL personnalisée (autre port ou chemin
imbriqué) est préservée et lue comme auparavant ; elle n'est pas réaffectée
automatiquement au gestionnaire.

## Dépannage et limites

- **Gestionnaire indisponible** : vérifiez que le service `bridge` est `1/1`,
  sur le même hôte/réseau que Home Assistant, et écoute sur
  `127.0.0.1:8765`. L'entrée est réessayée par Home Assistant.
- **Flux indisponible** : vérifiez MediaMTX sur `127.0.0.1:8554` et les
  journaux du pont. Ne supprimez pas un budget `blocked`, corrompu ou ambigu ;
  appliquez uniquement la migration explicite documentée.
- **Découverte vide** : la diffusion UDP est déclenchée par l'utilisateur,
  limitée à un sous-réseau privé de `/23` à `/30` ; utilisez l'ajout manuel.
- L'API du pont n'a pas d'authentification distincte et n'écoute que sur
  loopback. Les processus partageant le réseau hôte peuvent l'appeler ; ne
  publiez pas son port et gardez les services locaux dignes de confiance.
- La compatibilité des autres modèles Netvue et la disponibilité prolongée
  doivent être validées séparément. Les tests synthétiques n'utilisent aucune
  caméra réelle.
