# Instructions permanentes — Netvue Bridge

## Projet

Le projet consiste à développer une passerelle locale permettant de récupérer les flux vidéo des caméras Netvue Vigil et de les utiliser dans Home Assistant.

L'objectif est de créer une solution :

- Simple à comprendre et à maintenir.
- Fiable et performante.
- Économe en ressources.
- Facile à installer avec Portainer.
- Compatible avec plusieurs caméras.
- Sécurisée sans complexité excessive.

La nouvelle passerelle sera développée principalement en **Go**.

Home Assistant et MediaMTX pourront utiliser leurs technologies habituelles.

L'ancien projet Python constitue une référence technique, mais **son architecture ne doit pas être reproduite automatiquement**.

Le projet n'étant pas encore officiellement publié, aucune compatibilité avec les anciennes versions expérimentales n'est exigée, sauf demande explicite.

## Priorité actuelle : conception

Avant de développer la nouvelle passerelle, réaliser un audit de conception.

Examiner :

1. Les fonctionnalités réellement nécessaires.
2. Les technologies et bibliothèques disponibles.
3. Les méthodes les plus simples pour récupérer et transmettre la vidéo.
4. Les différentes architectures possibles.
5. Les protections de sécurité justifiées.
6. Les possibilités de supprimer des composants ou mécanismes.
7. Les méthodes de déploiement et de configuration.
8. Les besoins réels de Home Assistant.

Comparer les solutions lorsque plusieurs approches sont possibles.

Ne pas supposer que les choix de l'ancienne version restent les meilleurs.

**Privilégier la meilleure conception globale plutôt que la réutilisation du code existant.**

L'audit de conception précède l'implémentation. Une fois une conception validée, le développement peut avancer de manière autonome dans le périmètre autorisé.

## Principes de développement

- Privilégier un code simple, lisible et direct.
- Éviter les abstractions et dépendances inutiles.
- Éviter les duplications et les responsabilités mélangées.
- Utiliser des bibliothèques éprouvées lorsqu'elles simplifient réellement le projet.
- Ne pas réimplémenter des fonctionnalités déjà correctement prises en charge par les outils existants.
- Ajouter uniquement les fonctionnalités nécessaires.
- Ajouter des commentaires lorsqu'ils expliquent un comportement non évident.
- Favoriser la fiabilité et la facilité de maintenance.
- Évaluer les performances lorsque cela est pertinent.

Le nombre de lignes n'est pas un objectif en soi.

**La simplicité doit être évaluée sur l'ensemble du fonctionnement, pas uniquement sur les fichiers individuels.**

Lorsqu'une demande porte sur une implémentation, effectuer les modifications et les tests nécessaires, sans s'arrêter à un simple plan.

## Autonomie

L'agent est autorisé à :

- Examiner et modifier le code du projet.
- Supprimer les anciennes implémentations obsolètes après vérification.
- Créer une nouvelle architecture dans le dépôt.
- Exécuter des tests locaux et synthétiques.
- Installer les outils et dépendances dans le projet.
- Consulter les documentations officielles.
- Effectuer des diagnostics passifs sur les services accessibles.
- Utiliser des sous-agents pour les tâches complexes.

L'agent peut choisir les solutions techniques lorsqu'aucun choix précis n'a été imposé par l'utilisateur.

Ne pas demander de confirmation pour chaque modification locale ordinaire.

Préserver les modifications préexistantes de l'utilisateur.

Ne pas effectuer de commit, push ou publication sans demande explicite.

## Sécurité

Adopter une sécurité proportionnée aux risques réels.

Conserver notamment :

- L'authentification nécessaire auprès des caméras.
- La protection des identifiants et secrets.
- Des connexions réseau limitées aux besoins.
- La prévention des sessions concurrentes dangereuses.
- Des délais d'attente raisonnables.
- Des reconnexions contrôlées.
- Des limites adaptées de mémoire et de ressources.
- La gestion correcte des interruptions et erreurs critiques.

Ne pas ajouter des protections uniquement par précaution théorique.

Une protection doit avoir une utilité identifiable.

Les mécanismes redondants, disproportionnés ou inutiles peuvent être supprimés après analyse.

Les anciens systèmes de budgets, migrations, blocages permanents et superviseurs ne constituent pas des exigences pour la nouvelle architecture.

Ne pas les réintroduire automatiquement.

## Autorisations nécessaires

Une autorisation explicite reste nécessaire pour :

- Contacter activement les caméras réelles.
- Accéder aux comptes Netvue.
- Modifier le firmware ou réinitialiser un appareil.
- Modifier ou redémarrer des services de production.
- Modifier le réseau ou la configuration du serveur.
- Supprimer ou remplacer des données persistantes réelles.

Les tests caméra doivent respecter les limites de durée et de tentatives autorisées.

Les anciennes données expérimentales peuvent être archivées puis retirées d'une installation abandonnée sur décision du propriétaire.

Ne jamais supprimer silencieusement les états d'une installation active.

Ne pas exposer les flux vidéo sur Internet sans autorisation.

Ne jamais divulguer de secrets ou de données sensibles.

## Tests

Choisir les tests adaptés aux risques des modifications.

Privilégier :

- Les tests unitaires.
- Les caméras simulées.
- Les données vidéo synthétiques.
- Les tests d'intégration isolés.
- Les vérifications de performance utiles.

Distinguer les résultats vérifiés des hypothèses.

Ne jamais inventer une réussite.

Une fonctionnalité terminée et testée hors ligne peut être déclarée terminée même si son déploiement réel reste à effectuer.

Ne pas bloquer les travaux indépendants à cause d'une validation réelle encore non autorisée.

## Documentation

Pour chaque phase importante, créer ou actualiser un document :

`findings/phaseXX-description.md`

Le document présente brièvement :

1. L'objectif.
2. Les choix techniques.
3. Les modifications effectuées.
4. Les tests et leurs résultats.
5. L'état final et la prochaine étape.

Un seul document principal par phase.

Ne pas multiplier les rapports intermédiaires.

Les documents doivent être concis et correspondre au fonctionnement réel.

Mettre également à jour les guides actifs lorsque nécessaire.

## Communication

Répondre en français clair.

Être direct et orienté résultat.

Expliquer les décisions techniques importantes, leurs avantages et leurs limites.

Distinguer les faits vérifiés des hypothèses lorsque cela influence une décision.

Ne pas répéter systématiquement toutes les précautions historiques du projet.

Ne pas recommencer inutilement les analyses déjà validées, sauf lorsqu'elles sont nécessaires à une nouvelle décision.

## Principe fondamental

**Concevoir la solution la plus simple qui répond correctement aux besoins réels.**

Ne pas reproduire la complexité du projet précédent.

Ne pas conserver une technologie, une architecture ou une protection simplement parce qu'elle était présente auparavant.

Prendre les décisions à partir des besoins, des preuves disponibles et des tests.

La qualité, la simplicité et la fiabilité du résultat sont prioritaires.