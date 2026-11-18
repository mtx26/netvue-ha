## Roadmap du projet

Le fichier `docs/roadmap.md` constitue la feuille de route principale du projet.

Tous les agents doivent :

- Consulter la roadmap avant de commencer une tâche importante.
- Tenir compte des fonctionnalités existantes, prévues et de leurs dépendances.
- Mettre à jour la roadmap lorsque leur travail modifie réellement l'avancement du projet.
- Ajouter les nouveaux besoins ou problèmes importants identifiés.
- Distinguer les fonctionnalités implémentées, testées hors ligne et validées en conditions réelles.
- Ne jamais déclarer une fonctionnalité terminée sans preuve.
- Éviter les doublons et les informations obsolètes.
- Conserver une roadmap concise et facile à comprendre.

La roadmap doit refléter l'état réel du projet, sans devenir un journal détaillé des modifications.

## Documentation technique de référence

Le projet possède une documentation détaillée du protocole Netvue.

Fichiers de référence :

- `docs/netvue-protocol.md` : protocole réseau, commandes,
  paquets et formats audio/vidéo.
- `docs/netvue-lan-client-spec.md` : spécification complète
  permettant de reconstruire un client LAN Netvue.
- `docs/roadmap.md` : fonctionnalités, priorités et avancement.

Avant toute modification concernant :

- La découverte des caméras.
- L'authentification.
- Les connexions TCP/UDP.
- Le protocole Netvue.
- La réception audio ou vidéo.
- Le talkback.
- La synchronisation.
- Les sessions et reconnexions.

L'agent doit consulter les documents techniques pertinents.

Il doit distinguer :

1. Les comportements confirmés par l'analyse APK.
2. Les comportements confirmés sur une caméra réelle.
3. Les choix spécifiques à l'implémentation Go.
4. Les hypothèses encore non vérifiées.

Ne jamais remplacer une information vérifiée par une supposition.

Si une nouvelle découverte remet en question la documentation,
vérifier les preuves puis mettre à jour les documents concernés.

Ne pas dupliquer inutilement les informations.