# Phase 56 — Médiathèque des enregistrements

## Objectif et décisions

Afficher REC sur la carte lorsque le pont signale un flux enregistré, masquer la
commande manuelle en mode continu et ouvrir les archives de la caméra dans le
navigateur multimédia Home Assistant. La lecture passe par une URL HA signée et
le proxy local existant, sans exposer MediaMTX.

## Modifications et tests

La source multimédia liste les caméras et leurs segments lisibles. Le pont
identifie chaque segment par caméra et heure de début, sans inclure sa durée
variable. Les commandes de la carte déclenchent une actualisation de l'état.
Leurs fonds sombres restent visibles sur une image claire. L'ancien lien direct,
non signé, est remplacé par le navigateur multimédia.

Les tests frontend, HA avec pont synthétique et Go ciblés, y compris le race
detector, valident le parcours et l'isolation des caméras. Un test WebRTC
préexistant de libération du quota échoue toujours séparément sous Windows.

## État et limites

Implémentation testée hors ligne ; aucune caméra ni installation réelle n'a été
contactée. Le décodage réel et le parcours visuel dans HA restent à valider.
Le proxy ne prend pas en charge les requêtes Range ; le voyant reflète l'état
rapporté par le pont, sans confirmer l'écriture effective des fichiers.
