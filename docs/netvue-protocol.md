# Protocole LAN Netvue — faits utilisables

Ce document est une synthèse des preuves déjà présentes dans le dépôt et du
code historique. Il ne constitue pas une spécification fabricant ni une
garantie de compatibilité entre modèles. Voir l'analyse [LAN](../findings/lan-protocol-analysis.md),
la [faisabilité du login](../findings/lan-login-feasibility.md) et les modules
Python conservés sous `netvue_bridge/`.

## Confirmé

- Le code Android analysé contient une découverte LAN par UDP broadcast ; le
  pont Python a aussi été testé avec une requête UDP unicast et une réponse
  décodée.
- Les réponses de découverte sont des paquets encadrés contenant des unités
  JSON. Une réponse observée sur la caméra 2 annonçait son adresse source, un
  endpoint TCP et des informations locales d'authentification.
- Le cadrage historique utilise un en-tête binaire fixe de 16 octets, une
  longueur de corps bornée, un indicateur et un type de paquet. Les commandes
  utilisent des unités JSON ; les données média sont transportées dans un
  type de paquet distinct.
- L'application possède plusieurs chemins de transport et d'authentification.
  La branche LAN classique et la branche CameraII ne doivent pas être
  confondues.
- Lors d'essais antérieurs explicitement autorisés sur la caméra 2, le login
  et une demande LIVE ont reçu un résultat positif ; un flux H.264 a été
  reçu et une publication RTSP/TCP a été décodée.

## Probable / limité à certains cas

- Le code historique interprète les unités média comme H.264 Annex B et un
  horodatage 32 bits en millisecondes. Cette interprétation est cohérente avec
  les observations et les tests existants, mais n'est pas une spécification
  universelle du fabricant.
- Les réponses et le chemin effectif peuvent dépendre du modèle, firmware,
  capacités annoncées, endpoint dynamique et choix de service de l'application.
- Le port 5555 a été annoncé lors d'une réponse observée et utilisé dans les
  essais historiques ; il n'est pas traité comme port universel dans le
  nouveau code.

## Non vérifié

- Le lien du port TCP 5555 avec le média pour tout modèle ; aucune déduction
  de protocole ne doit venir du numéro de port seul.
- L'authentification, la disponibilité cloud-indépendante et le chemin choisi
  en fonctionnement pour tous les appareils du propriétaire.
- Le protocole exact et le codec de la caméra 1, l'audio, les fragments
  génériques, les images B et la stabilité 24/7.
- L'équivalence du nouveau traitement Go avec le décodeur historique.

## Garde-fous d'implémentation

- N'accepter une découverte que si son adresse source et l'endpoint annoncé
  correspondent à l'adresse explicitement ciblée ; borner les longueurs et
  délais.
- Exiger un code de résultat explicite à chaque étape d'authentification et de
  sélection LIVE. Un paquet incomplet ou inattendu échoue explicitement.
- Ne pas journaliser de clé locale, identifiant, token, charge utile média ou
  trame brute.
- **Limite de sécurité observée :** le code LAN historique envoie la clé locale
  dans le login TCP sans chiffrement applicatif apparent. La nouvelle
  implémentation conserve ce comportement nécessaire à l'interopérabilité ;
  la clé reste en mémoire et hors journaux, mais un observateur du LAN peut
  voir la requête. La réutilisabilité de la clé capturée n'est pas établie.
  N'utiliser que sur un réseau de confiance et ne pas présenter cette voie
  comme une authentification protégeant le secret en transit.
- Ne jamais contacter une caméra pendant les tests du protocole Go. Les tests
  utilisent uniquement des réponses fabriquées ou un pair de boucle locale.
- La capture ou l'essai sur appareil nécessite une autorisation distincte et
  bornée.
