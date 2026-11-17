---
name: netvue-protocol
description: Utiliser lors de l'analyse, de l'implémentation ou des tests du protocole LAN propriétaire Netvue Vigil, notamment découverte, authentification, sessions et réception vidéo.
---

# Netvue Protocol

## Objectif

Réutiliser les connaissances vérifiées sur le protocole LAN Netvue.

Ne pas reproduire automatiquement l'architecture de l'ancienne passerelle Python.

## Sources

Examiner les rapports existants dans findings/ et le code historique de netvue_bridge/.

Utiliser les résultats des tests et les captures autorisées disponibles.

Privilégier les faits observés.

## Points à documenter

- Découverte LAN.
- Connexion.
- Authentification.
- Structure des paquets.
- Encodage des messages.
- Commandes LIVE.
- Réception H.264.
- Gestion des erreurs.
- Fermeture des sessions.

## Règles

Ne pas supposer qu'un port réseau correspond à un protocole particulier sans preuve.

Ne pas supposer que le matériel de toutes les caméras est identique.

Ne pas inventer les structures de paquets ou les constantes.

Ne pas utiliser de secrets provenant de tiers.

Ne pas effectuer de connexion caméra sans autorisation.

## Implémentation

Privilégier les fonctions standard Go pour les opérations réseau et binaires.

Séparer les particularités Netvue des mécanismes génériques.

Éviter les validations dupliquées.

Conserver des tests pour les messages et comportements sensibles.

## Documentation

Maintenir une spécification technique vérifiée du protocole dans :

docs/netvue-protocol.md

Créer ce document à partir des preuves disponibles s'il n'existe pas.

Distinguer :

- Confirmé.
- Probable.
- Non vérifié.

Ne pas dupliquer inutilement la spécification complète dans ce skill.