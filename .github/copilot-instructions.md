# Instructions permanentes — Netvue Bridge

## Projet

Ce dépôt développe une passerelle locale **Netvue Vigil LAN → H.264/RTSP → Home Assistant**.
L'utilisateur possède deux caméras Netvue Vigil et un serveur Debian avec Docker,
Portainer et Home Assistant. Le projet a dépassé l'analyse statique de l'APK.

Les travaux déjà démontrés comprennent la découverte LAN, une authentification
locale autorisée sur la caméra 2, la réception H.264 et une publication RTSP
lisible. Cela **ne prouve pas** la stabilité 24 h/24, la compatibilité de la
caméra 1, ni la disponibilité permanente du flux.

Objectifs : stabiliser le pont, obtenir une intégration Home Assistant simple,
préparer une stack Portainer facile à installer et, plus tard, permettre
l'enregistrement et la consultation d'historique.

## Priorités de développement

1. Livrer le comportement demandé avec un code lisible, simple et testable.
2. **Remplacer puis retirer** les anciennes implémentations devenues inutiles :
   ne pas accumuler plusieurs superviseurs, budgets, wrappers ou variantes
   pour une même responsabilité sans nécessité démontrée.
3. Préférer les fonctions et mécanismes déjà présents aux nouvelles couches
   d'abstraction. Éviter la duplication, les états excessifs et le code mort.
4. Modifier directement le code dans le périmètre autorisé. Ne pas s'arrêter
   au diagnostic ou au plan si la demande porte sur une implémentation.
5. Conserver les fonctionnalités existantes lors du refactoring et caractériser
   les comportements sensibles par des tests avant de les remplacer.
6. Mesurer la simplification avec des éléments concrets (responsabilités,
   fichiers obsolètes retirés, duplications éliminées), **pas seulement** avec
   le nombre de lignes. Ne jamais supprimer une protection utile pour atteindre
   artificiellement un objectif de réduction.

## Méthode

- Lire uniquement les modules, tests et rapports pertinents ; ne pas refaire
  l'analyse statique déjà terminée.
- Définir les critères de réussite de la tâche, implémenter, tester, corriger
  puis enlever le code remplacé. Continuer tant que des étapes sûres et
  autorisées permettent d'atteindre ces critères.
- Exécuter les tests hors ligne adaptés et signaler honnêtement les échecs
  intermittents ; ne pas transformer des réussites au rejeu en preuve
  d'absence de défaut.
- Séparer **code terminé et testé hors ligne** de **déploiement et validation
  réelle encore à effectuer**. Ne pas qualifier toute la refonte d'inachevée
  seulement parce qu'un test caméra ou une mise en production reste à autoriser.
- En cas de vrai blocage, préciser le fait bloquant et ce qui a été terminé.
  Ne jamais déclarer réussie une vérification non exécutée.
- Pour une tâche complexe, utiliser des sous-agents si disponibles : analyses
  indépendantes en parallèle, modifications par zones de fichiers distinctes,
  intégration et validation sous la responsabilité de l'agent principal.

## Sécurité et autorisations

**Autorisés sans nouvelle confirmation pour les tâches de développement :**
lecture et modification des fichiers du dépôt, suppression de code obsolète
après vérification, tests purement locaux ou synthétiques, dépendances
installées uniquement dans le projet, diagnostics passifs et lecture des états
Docker/SSH accessibles avec les droits déjà configurés.

**Autorisation explicite distincte nécessaire avant :**

- Tout contact actif avec les caméras réelles (préciser caméra, opérations,
  nombre maximal de tentatives et durée).
- Accès aux comptes Netvue ou utilisation de leurs identifiants.
- Modification du firmware ou réinitialisation d'une caméra.
- Modification ou redémarrage de Home Assistant, Docker/Swarm, des services,
  du réseau ou du serveur de production.
- Migration appliquée aux états/budgets persistants réels ou autre opération
  qui supprime, écrase ou rend difficilement récupérables des données.

Ne pas contourner un budget/état bloqué en changeant de chemin ou en créant
un répertoire neuf. Préparer et tester une migration/réconciliation sûre hors
ligne avant toute application réelle. Ne pas exposer le flux sur Internet ;
privilégier RTSP sur loopback et des accès locaux contrôlés. Conserver les
limitations raisonnables de ressources, les verrous de session, les délais de
reconnexion et le traitement explicite des erreurs critiques. Pas de brute
force, d'exploitation de vulnérabilité ou de contournement des permissions.

Ne jamais publier de secrets, tokens, cookies, MAC complètes, identifiants
matériels ou captures brutes sensibles. Expurger les journaux avant partage.
Les ports 5555 des caméras ne prouvent **pas** à eux seuls un protocole donné ;
le chipset de ces exemplaires n'est pas confirmé.

## Code, déploiement et documentation

- Respecter les contrats publics utiles : CLI, flux RTSP, entités Home
  Assistant et configuration déployée. Une compatibilité interne obsolète peut
  être retirée après mise à jour des appelants et des tests.
- La passerelle et MediaMTX sont des services distincts de Home Assistant ;
  préparer une stack Portainer reproductible, sans modifier les autres stacks.
- Utiliser les sources officielles pour les faits externes, sans transformer
  chaque tâche de code en recherche générale.
- Mettre à jour les documents actifs (`README.md`, `docs/`, guide de
  déploiement) plutôt que créer systématiquement un nouveau rapport `findings/`.
  Préserver les preuves historiques utiles et les sauvegardes utilisateur.
- À la fin, donner un bilan **court et concret** : comportement livré, ancien
  code supprimé ou fusionné, tests exécutés, limites non vérifiées et seule
  prochaine action réellement nécessaire.

Écrire en français clair. Pour les conclusions techniques importantes,
distinguer **observé**, **déduit** et **non vérifié** ; citer les chemins du
code source quand cela éclaire la décision.
