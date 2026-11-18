# Phase 35 — Découverte LAN depuis Home Assistant

## Objectif et décisions

Permettre une recherche explicite et une sélection dans HA, sans supprimer la
saisie manuelle. Le pont envoie une requête Netvue V2 au broadcast du
sous-réseau de son adresse source ; il conserve les réponses compatibles avec
sa découverte unicast, sans exposer la clé ni démarrer de session.

## Modifications

L'API locale propose `POST /api/v1/discovery`. HA présente la liste des
adresses, refuse les doublons et permet de réessayer ou de saisir une adresse
en cas d'absence de réponse ou d'erreur. Les recherches concurrentes sont
refusées et leur interruption suit l'arrêt du gestionnaire.

## Validation et limites

- Tests Go complets, détecteur de courses, `go vet` et compilation réussis.
- Tests UDP synthétiques sur loopback : requête, filtrage, dédoublonnage,
  absence de réponse et annulation. Tests HTTP : résultats, erreurs,
  concurrence et arrêt.
- 29 tests HA réussis avec HA 2026.11.2 ; client API, sélection, doublons,
  erreurs et saisie manuelle sont testés sans caméra réelle.
- Essai HA synthétique avec faux gestionnaire : découverte et création de
  l'entrée réussies ; la validation ultérieure de l'aperçu JPEG échoue dans
  l'environnement local, malgré un décodage FFmpeg indépendant réussi.
- Essai réel autorisé : une seule diffusion UDP depuis le serveur Debian
  vers le sous-réseau `192.168.128.0/23` ; l'API du pont temporaire a trouvé
  la caméra `192.168.129.2` et aucun autre appareil compatible. Aucun login,
  flux ou changement de service de production ; le pont temporaire a été
  arrêté et supprimé.
- La sélection depuis une installation HA réelle et la compatibilité des
  autres modèles restent à valider. Le PC Wi-Fi est sur un autre sous-réseau ;
  l'essai a utilisé l'interface Ethernet du serveur.
