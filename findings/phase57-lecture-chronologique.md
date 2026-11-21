# Phase 57 — Lecture chronologique

## Objectif et décisions

Consulter les enregistrements dans la carte sans sélectionner de fichier :
frise, date et heure, retours rapides et retour au direct. Les coupures sont
signalées ; le segment en cours est consultable dès qu'une partie est lisible.
La frise affiche au maximum 24 heures ; une autre date déplace la fenêtre sans
modifier la conservation.
Une recherche manuelle dans le lecteur annule la reprise automatique en
attente afin qu'une réponse tardive ne replace pas le curseur.
Dans l'historique, le voyant REC est masqué ; la barre sur la vidéo offre le
retour au direct clairement identifié, les sauts de quinze secondes et les
autres actions directement accessibles.
La frise affiche les périodes enregistrées en vert, les coupures en gris et
l'heure du curseur, avec des graduations.

## Modifications

MediaMTX ne fournit ni plage HTTP ni taille pour son export. Le pont prépare
un MP4 temporaire sur le volume partagé, puis sert les plages avec taille et
ETag. Une durée figée maintient l'instantané cohérent lorsque le segment
continue à grandir. FFmpeg produit une miniature à l'instant demandé. Les
URLs de lecture et de miniature sont signées par Home Assistant. La règle CSS
qui rendait visible le bouton masqué en mode continu est corrigée.
Lors d'un retour rapide au direct alors que quatre lecteurs sont présents,
Home Assistant attend la fermeture d'une session déjà en cours de libération
avant de refuser une nouvelle offre.

## Tests et état

Tests frontend et proxy HA isolé ; test synthétique avec MediaMTX et FFmpeg,
incluant un fichier ouvert en cours de croissance, les plages, une miniature et
le décodage MP4. L'affichage et la recherche dans une vidéo synthétique ont
été vérifiés dans un navigateur local.
La recherche dans la barre du lecteur a aussi été vérifiée sur un MP4
fragmenté exporté par MediaMTX, avec plages HTTP locales.
Un test frontend vérifie l'annulation d'une reprise programmée ou déjà en cours
lors d'une recherche manuelle.
Les sauts entre segments et le déplacement de la frise ont été vérifiés dans
un navigateur local avec une vidéo synthétique et des plages HTTP.
Les commandes directes ont été vérifiées par les tests frontend ; leur
disposition et le sélecteur de date ont été contrôlés dans un navigateur local
avec des données simulées, notamment sur une carte de 260 pixels.
Les plages vertes, leur découpage dans la fenêtre et l'heure du curseur sont
testés hors ligne ; leur affichage et la navigation au clavier ont été vérifiés
dans un navigateur avec des données simulées.

## Limites

L'avance dans la barre du lecteur reste signalée défaillante sur l'installation
réelle ; sa cause n'est pas encore identifiée. Deux préparations
simultanées au maximum, limitées chacune à 1 Gio et deux minutes ; chaque
requête de plage régénère l'export, ce qui peut augmenter la latence et la
charge disque. La miniature peut manquer si les images demandées ne sont pas
encore décodables. Après un arrêt brutal, les fichiers temporaires résiduels
sont soumis à la rétention habituelle.
