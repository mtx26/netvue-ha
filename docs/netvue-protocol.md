# Protocole LAN Netvue : faits et limites

Synthèse des preuves du code Android analysé et des tests
autorisés antérieurs. Ce n'est ni une spécification fabricant ni une garantie
de compatibilité. Le code actif est `internal/netvue/`; les analyses
historiques locales peuvent rester dans `findings/` (ignoré par Git).

## Confirmé par les preuves disponibles

- Le code Android analysé contient une découverte UDP broadcast ; une
  requête unicast et sa réponse ont aussi été testées.
- La recherche utilisateur Go envoie une requête V2 UDP/36868 à l'adresse
  broadcast du sous-réseau de l'interface configurée, puis collecte pendant
  deux secondes les réponses compatibles avec la session Go. L'adresse
  annoncée doit correspondre à l'émetteur ; les autres paquets sont ignorés.
  Une recherche réelle autorisée a trouvé la caméra 2 depuis le serveur.
  Ce filtrage n'établit pas à lui seul la compatibilité vidéo du modèle.
- Les réponses de découverte sont encadrées et contiennent des unités JSON.
  Une réponse observée sur la caméra 2 annonçait son adresse source, un
  endpoint TCP et des informations locales d'authentification.
- Le cadrage historique comporte un en-tête binaire fixe de 16 octets, une
  longueur de corps bornée, un indicateur et un type de paquet. Les commandes
  sont des unités JSON ; les médias utilisent un type distinct.
- L'application possède plusieurs chemins de transport/authentification :
  ne pas confondre la branche LAN classique et CameraII.
- Lors d'essais antérieurs explicitement autorisés sur la caméra 2, le login
  et la demande LIVE ont réussi ; du H.264 a été reçu et une publication
  RTSP/TCP décodée.
- La caméra 2 a répondu à une demande LIVE version 2 avec une confirmation
  `V=1` et un résultat explicite égal à zéro. Le code accepte `V=1` et `V=2`,
  mais seule la réponse `V=1` est documentée comme observée sur une caméra.
  Le code Android analysé utilise lui aussi `V` pour choisir un décodeur
  d’ACK version 1 ou 2 sur le chemin LAN classique. Il mappe une version absente
  ou inconnue vers un paquet non reconnu. Conserver les deux valeurs admises
  et rejeter les versions absentes ou inconnues ; les tests hors ligne couvrent
  ces cas. Cette observation APK ne décrit pas la branche CameraII.

## Probable ou limité

- Les unités média ont été interprétées comme H.264 Annex B avec un timestamp
  32 bits en millisecondes. C'est cohérent avec les observations et les tests,
  sans être une spécification universelle.
- Réponses et chemin effectif peuvent dépendre du modèle, firmware, capacités,
  endpoint dynamique et choix de service de l'application.
- Le port 5555 a été annoncé et utilisé lors d'essais historiques, mais n'est
  pas un port universel.

## Non vérifié

Le rôle de TCP/5555 pour tout modèle, sans déduire le protocole du port seul ;
l'authentification, le fonctionnement
sans cloud et le chemin réellement choisi pour tous les appareils ; protocole
et codec caméra 1, audio, fragments génériques, B-frames et stabilité 24/7.

## Garde-fous et sécurité

- Accepter la découverte seulement si source et endpoint correspondent à
  l'adresse ciblée (ou à l'émetteur sur le sous-réseau pour une recherche
  broadcast) ; borner longueurs, nombre de réponses et délais.
- Exiger un résultat entier explicite à chaque étape login/sélection LIVE ;
  rejeter `null` ainsi que les paquets incomplets ou inattendus. En découverte,
  le résultat reste facultatif, mais doit être un entier nul s'il est présent.
- Ne pas journaliser clé locale, identifiant, token, média ou trame brute.
- Le login TCP observé envoie la clé locale sans chiffrement applicatif
  apparent ; l'implémentation Go conserve ce comportement nécessaire à
  l'interopérabilité. La clé reste en mémoire et hors journaux, mais un
  observateur du LAN peut la voir. Sa réutilisabilité n'est pas établie.
  N'utiliser que sur un LAN de confiance ; ce transport ne protège pas le
  secret en transit.
- Les tests Go utilisent des réponses fabriquées ou un pair local.
