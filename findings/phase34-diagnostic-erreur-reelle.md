# Phase 34 — Diagnostic de l'erreur réelle

## Objectif et décisions

Retrouver l'origine du message historique sans contacter de caméra. L'ancien
gestionnaire regroupait `ErrProtocol` et `ErrMediaFrame` sous le même message ;
le superviseur arrêtait ces erreurs. Ce texte ne permet pas de déterminer
quelle validation a échoué. Les diagnostics assainis introduits au commit
`424324c` distinguent depuis les étapes et motifs, sans exposer de paquets.

Une ancienne version rejetait la confirmation LIVE `V=1`, observée sur la
caméra 2 ; le correctif `4447309` accepte les versions 1 et 2. La version ou
le digest effectivement déployé lors du journal est inconnu : cette hypothèse
ne constitue pas une cause confirmée de l'incident.

## Modifications et tests

La lecture synthétique de `C:null` produisait à tort un entier nul : découverte,
authentification et LIVE l'acceptaient comme un succès. Le parseur exige
maintenant un entier JSON effectif ; la découverte accepte toujours l'absence
du résultat, mais pas `null`. Les tests couvrent les trois étapes et vérifient
qu'une authentification invalide n'envoie pas de commande LIVE.

En publication RTSP, gortsplib renvoie le statut dans une erreur typée et
peut fournir une réponse nulle. Un 503 était donc classé fatal malgré la
politique de reprise. Le statut est maintenant extrait de l'erreur ; des
serveurs locaux synthétiques vérifient 503 et 403 à ANNOUNCE, SETUP et RECORD.
Ces deux défauts ne démontrent pas la cause du message historique.

## État final et limites

Les tests Go, le détecteur de courses, `go vet` et la compilation sont
exécutés hors ligne. Aucun essai sur caméra ni changement en production.
Le décodeur vidéo complet n'est pas exécuté en production : des NAL acceptés
ne garantissent pas qu'une image soit décodable. Les flux avec jeux de
paramètres multiples ou fragments interpaquets restent non vérifiés.

Pour identifier l'incident, il faut d'abord le digest de l'image exécutée au
moment du journal et, s'il existe déjà, son diagnostic assaini avec catégorie,
étape et motif. Une nouvelle session réelle ou une mise à jour de production
nécessite une autorisation distincte et des limites explicites.
