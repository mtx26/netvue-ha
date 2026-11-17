---
name: netvue-video
description: Utiliser pour concevoir, développer, tester ou diagnostiquer la réception H.264, les horodatages, la publication RTP/RTSP et la compatibilité MediaMTX ou Home Assistant du projet Netvue.
---

# Netvue Video

## Objectif

Obtenir une chaîne vidéo H.264 vers RTSP fiable avec un minimum de code personnalisé.

## Technologies

Étudier en priorité les solutions existantes :

- gortsplib.
- mediacommon.
- MediaMTX.
- FFmpeg lorsque nécessaire.

Privilégier les bibliothèques qui prennent déjà en charge les fonctionnalités standards.

Ne pas développer un serveur RTSP personnalisé sans nécessité démontrée.

## Traitement H.264

Vérifier notamment :

- Les unités NAL.
- Les SPS/PPS.
- Les images IDR.
- Les frontières des images.
- Les horodatages.
- La monotonie temporelle.
- Les changements de résolution.
- Les interruptions.
- Les paquets incomplets.

Ne pas supposer que le flux Netvue respecte parfaitement les comportements H.264 attendus.

## Publication

Comparer les méthodes de publication disponibles.

Évaluer la possibilité de publier directement avec gortsplib.

Ne conserver FFmpeg ou un traitement personnalisé que si les besoins et les tests le justifient.

## Tests

Utiliser des données synthétiques.

Vérifier :

1. Démarrage du flux.
2. Publication RTSP.
3. Décodage H.264.
4. Images-clés.
5. Changements SPS/PPS.
6. Changements de résolution.
7. Horodatages.
8. Déconnexions.
9. Reconnexions.
10. Libération des ressources.

Vérifier la lecture avec des outils indépendants lorsque cela apporte une preuve utile.

## Sécurité

Limiter les ressources.

Fermer correctement les processus et connexions.

Ne pas exposer les flux sur Internet sans autorisation.

Ne pas contacter les caméras pendant les tests hors ligne.

## Principe

Réutiliser les traitements vidéo éprouvés.

Le code personnalisé doit se limiter autant que possible aux particularités réelles de Netvue.