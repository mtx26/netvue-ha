# Netvue — Instructions permanentes

## Projet

Le projet développe une passerelle locale Netvue Vigil vers RTSP et Home Assistant.

La passerelle principale est en Go. Python est conservé pour l'intégration Home Assistant ; l'ancienne passerelle est isolée dans `archive/python-bridge/`.

Le but est d'obtenir un logiciel :

- Simple et lisible.
- Fiable et performant.
- Économe en ressources.
- Facile à configurer.
- Facile à installer avec Portainer.
- Compatible avec plusieurs caméras.
- Facile à maintenir et à faire évoluer.

## Principes

Privilégier la simplicité de l'architecture et du code.

Utiliser des bibliothèques fiables lorsqu'elles évitent des implémentations personnalisées.

Ne pas reproduire automatiquement les anciennes architectures, budgets, superviseurs et mécanismes expérimentaux.

Éviter :

- La duplication.
- Les abstractions inutiles.
- Les dépendances disproportionnées.
- Les états excessifs.
- Le code mort.
- Les commentaires inutiles.

Les anciennes compatibilités expérimentales ne sont pas obligatoires.

Ne pas modifier du code uniquement pour le rendre différent. Toute refactorisation doit apporter un bénéfice concret : simplicité, fiabilité, performance, lisibilité ou maintenabilité.

## Autonomie

L'agent peut, sans nouvelle confirmation :

- Lire et modifier le dépôt.
- Développer de nouvelles fonctionnalités.
- Créer ou supprimer des fichiers du projet.
- Installer des outils dans l'environnement de développement.
- Effectuer des recherches techniques.
- Exécuter des tests locaux et synthétiques.
- Créer des tests et des documentations.
- Utiliser des sous-agents.
- Corriger les problèmes identifiés.

Les demandes d'implémentation autorisent les modifications locales nécessaires.

Ne pas s'arrêter à un simple diagnostic lorsqu'une implémentation est demandée.

Vérifier les modifications Git existantes avant de travailler. Préserver les changements de l'utilisateur et éviter toute suppression accidentelle de travail non commité.

## Sécurité

Conserver des protections proportionnées aux risques réels.

Protéger :

- L'authentification des caméras.
- Les identifiants et secrets.
- Les connexions réseau.
- Les sessions concurrentes.
- Les ressources.
- Les processus.
- Les erreurs critiques.

Éviter les protections redondantes ou inutilement complexes.

Les anciens budgets persistants ne constituent pas une obligation pour la nouvelle architecture.

## Opérations réelles

Une autorisation explicite reste nécessaire pour :

- Contacter les caméras réelles.
- Accéder aux comptes Netvue.
- Modifier le firmware.
- Modifier ou redémarrer les services de production.
- Déployer ou remplacer une stack Portainer.
- Modifier Home Assistant installé.
- Supprimer ou remplacer des données réelles.

Les autorisations de tests réels doivent désigner les caméras concernées, les opérations permises et les limites des essais. Elles peuvent être accordées pour une tâche ou une phase entière.

Les anciennes données expérimentales doivent être préservées tant qu'aucun abandon autorisé n'a été effectué.

Ne jamais contourner les restrictions d'accès.

Ne pas publier de secrets.

Ne pas exposer les flux vidéo sur Internet sans autorisation.

## Travail

Pour les tâches importantes :

1. Comprendre le problème.
2. Identifier les besoins.
3. Choisir une solution adaptée.
4. Implémenter.
5. Tester.
6. Corriger.
7. Vérifier le résultat.

Lorsqu'une fonctionnalité concerne plusieurs composants, vérifier également leur intégration. Tester les erreurs, les cas limites et les comportements concurrents pertinents.

Utiliser des sous-agents lorsque cela améliore réellement le travail.

Poursuivre les étapes sûres et autorisées tant qu'elles permettent d'atteindre l'objectif.

Ne pas multiplier les demandes de confirmation pour les choix techniques ordinaires.

## Documentation

Maintenir un plan principal du projet.

Pour chaque phase importante, créer ou actualiser :

`findings/phaseXX-description.md`

Le document doit présenter :

- L'objectif.
- Les décisions.
- Les modifications.
- Les tests.
- L'état final.
- Les limites restantes.

Ne pas multiplier les rapports.

Privilégier une documentation concise, claire et utile. Éviter les répétitions entre fichiers.

## Communication

Répondre en français clair.

Distinguer les faits vérifiés des hypothèses importantes.

Ne jamais inventer de résultat.

Donner des bilans courts, précis et orientés résultat.

## Objectif fondamental

Concevoir et développer la solution la plus simple qui répond correctement aux besoins du projet.

**La qualité et la fiabilité sont prioritaires.**

## Netvue Protocol Reference

Before modifying camera discovery, authentication, LAN communication,
video reception, audio reception, talkback, RTP/RTSP publication,
WebRTC audio or camera session management:

1. Read `docs/netvue-protocol.md`.
2. Read the relevant sections of `docs/netvue-lan-client-spec.md`.
3. Check existing tests and previous findings.
4. Preserve verified protocol behavior.
5. Distinguish documented facts from implementation assumptions.
6. Update the documentation when new behavior is verified.

Do not reinvent or modify the Netvue protocol without examining
the existing reverse-engineering documentation.

Prefer simple, maintainable implementations supported by evidence.