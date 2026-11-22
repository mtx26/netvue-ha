# Phase 65 - Centralisation des reglages HA

## Objectif

Centraliser les reglages permanents dans Home Assistant tout en conservant
les variables d'environnement comme valeurs par defaut de compatibilite.

## Decisions

- Home Assistant Store prive pour les reglages globaux geres.
- Bridge Go comme autorite runtime pour TURN et l'enregistrement.
- Options par camera pour les annotations live, replay et export.
- Le panneau permanent est retire de la carte; les boutons live/replay restent
  comme surcharges temporaires uniquement.

## Modifications

- Options et endpoint `/api/netvue_local/overlay/{entry_id}`.
- Endpoints Go `/api/v1/settings` et `/api/v1/webrtc/config`.
- Persistance runtime, validation et reapplication sans redemarrage.
- Jeton TURN conserve cote serveur, masque dans les interfaces et jamais
  renvoye dans les reponses publiques.
- Enregistrements proteges exclus du nettoyage temporaire.

## Tests

- Tests Go du gestionnaire et tests TURN/stockage executes localement.
- Syntaxe JavaScript verifiee.
- Tests HA ajoutes pour les preferences par camera, TURN, stockage et
  restauration.
- Execution complete HA non disponible dans le runtime Python actuel.

## Etat final

- Implante hors ligne.
- Validation production, TURN reel et camera reelle restantes.
