# Phase 32 — Confirmation LIVE de la caméra 2

## Objectif et décision

Corriger l'arrêt de la session sur une erreur de protocole. La caméra 2
répond à une demande LIVE version 2 avec une confirmation version 1. Accepter
les versions 1 et 2, sans accepter une version inconnue ni un résultat absent
ou non nul.

## Modifications et tests

La réception LIVE accepte la version observée et le gestionnaire signale
distinctement les versions LIVE non prises en charge. Les tests synthétiques
couvrent les confirmations versions 1 et 2, l'absence de version, une version
inconnue, ainsi que l'absence ou l'échec du résultat.

## Réévaluation hors ligne

La réponse réelle documentée est `V=1` après une demande `V=2`. Aucune preuve
locale ne démontre qu'une réponse `V=2` serait invalide ni ne spécifie le rôle
exact de `V` ; supprimer le contrôle accepterait aussi une version absente.
L'allowlist `1, 2` est donc conservée, de même que les contrôles d'action et de
résultat explicite nul. Cette réévaluation et les tests n'ont contacté aucune
caméra et n'ont modifié aucun service de production.

Avec l'autorisation du propriétaire, deux essais de diagnostic bornés ont
identifié l'erreur puis la version reçue. Un troisième essai de 15 secondes,
avec un binaire corrigé temporaire, s'est terminé sans erreur : le pont a
publié vers MediaMTX et FFmpeg dans Home Assistant a retourné le code 0.
La sortie ne permet pas de confirmer le nombre d'images décodées.

## État final et limites

La correction est locale ; la stack en production n'a pas été modifiée.
La session HA existante reste en échec tant qu'une image corrigée n'est pas
installée et que la session n'est pas relancée. La stabilité prolongée et
l'affichage dans HA restent à vérifier.
