# Protocole LAN Netvue : faits et limites

Synthèse des preuves du code Android analysé et des tests
autorisés antérieurs. Ce n'est ni une spécification fabricant ni une garantie
de compatibilité. Le code actif est `internal/netvue/`; les analyses
historiques locales peuvent rester dans `findings/` (ignoré par Git).

## Confirmé par les preuves disponibles

- Le code Android analysé contient une découverte UDP broadcast ; une
  requête unicast et sa réponse ont aussi été testées.
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
  de version 1. Le pont accepte les confirmations versions 1 et 2, avec
  résultat explicite égal à zéro.

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
  l'adresse ciblée ; borner longueurs et délais.
- Exiger un résultat explicite à chaque étape login/sélection LIVE ; rejeter
  explicitement les paquets incomplets ou inattendus.
- Ne pas journaliser clé locale, identifiant, token, média ou trame brute.
- Le login TCP observé envoie la clé locale sans chiffrement applicatif
  apparent ; l'implémentation Go conserve ce comportement nécessaire à
  l'interopérabilité. La clé reste en mémoire et hors journaux, mais un
  observateur du LAN peut la voir. Sa réutilisabilité n'est pas établie.
  N'utiliser que sur un LAN de confiance ; ce transport ne protège pas le
  secret en transit.
- Les tests Go utilisent des réponses fabriquées ou un pair local.
