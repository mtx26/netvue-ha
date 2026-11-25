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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. When the answer is under a Markdown heading, `graphify section "<heading>"` prints that section's text, so the .md need not be opened. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- A committed graphify-out/graph.json is rewritten whole on every build, so a large diff to it in a pull request is expected. Do not flag it as churn or suggest skipping the graph rebuild or its hooks.

# graphify
- **graphify** (`.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.
