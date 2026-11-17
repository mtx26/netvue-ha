# Plan Netvue Bridge Go

## Objectif et exigences

Recevoir un flux Netvue autorisé sur le LAN et le publier en H.264/RTSP pour
Home Assistant. Go est la passerelle ; Python assure l'intégration HA.

- Ne contacter que les appareils configurés et les endpoints annoncés par la
  réponse de découverte validée.
- Borner les ressources, délais et buffers. Fermer les sessions et signaler
  les erreurs critiques sans exposer de secrets ni de captures.
- Le transport Netvue observé n'est pas chiffré. Utiliser un LAN de confiance ;
  ne pas exposer l'API ou RTSP sur Internet.
- Tester hors ligne avec des appareils et des médias fictifs.

## Périmètre

| Étape | Fonctionnalités et critères |
|---|---|
| MVP | Découverte unicast, authentification explicite, LIVE, H.264 et publication RTSP/TCP. `test` lance une session bornée ; `run` supervise une caméra. Tests synthétiques des erreurs, délais et fermetures, puis décodage indépendant. |
| V1 | Gestion dynamique multi-caméra, sessions et reconnexions indépendantes, API locale, entités HA et stack Portainer. Tests d'isolation, de reprise et de chaîne complète hors ligne. La compatibilité réelle et la stabilité prolongée restent à valider séparément. |
| Évolutions | Enregistrement, historique, rétention, stockage, mouvement et statistiques ; pas d'implémentation anticipée. |

## Architecture et choix vidéo

```text
Home Assistant -> API locale Go -> sessions Netvue indépendantes
                                         |
                              H.264/RTP -> MediaMTX -> Home Assistant
```

HA conserve les entrées de configuration ; Go maintient seulement l'état
runtime des caméras demandées. HA réajoute une entrée manquante
après redémarrage du pont mais ne relance pas implicitement une session
existante arrêtée ou en échec. La saisie de l'IPv4 est manuelle ; la
découverte de candidats par diffusion LAN n'est pas implémentée.

| Technologie | Décision |
|---|---|
| [gortsplib](https://github.com/bluenviron/gortsplib) | Publication RTSP/RTP en Go ; ne traite pas le protocole Netvue ni le décodage. |
| [mediacommon](https://github.com/bluenviron/mediacommon) | Analyse Annex B et utilitaires H.264 ; ne remplace pas un décodeur. |
| [MediaMTX](https://github.com/bluenviron/mediamtx) | Serveur RTSP partagé, distinct du pont. |
| [FFmpeg](https://ffmpeg.org/) | Décodage indépendant dans les tests, pas de processus vidéo obligatoire en production. |

Les versions Go sont épinglées dans `go.mod`. L'API et RTSP écoutent sur
loopback sans authentification distincte ; tout processus qui partage cet
espace réseau peut les joindre. Les autres contrats et limites sont décrits
dans [architecture.md](./architecture.md) et [bridge.md](./bridge.md).

## Avancement

- **Implémenté et testé hors ligne :** protocole, gestionnaire multi-caméra,
  supervision, publication RTSP, diagnostics et reprise ciblée dans HA.
  Les tests synthétiques couvrent notamment les paramètres H.264, timestamps,
  interruptions, changements de résolution et isolation des sessions.
- **Chaîne synthétique vérifiée :** HA réel, gestionnaire Go, caméra fictive,
  MediaMTX et JPEG HA ; voir [development.md](./development.md).
- **Tests et documentation simplifiés :** scénarios Go conservés ; tests Go,
  détecteur de courses, `go vet` et intégrations MediaMTX/FFmpeg réussis.
- **Installation préparée :** stack Portainer avec deux variables obligatoires ;
  intégration HACS configurée pour le dépôt. Le dépôt GitHub doit être public
  pour HACS, et le package GHCR pour un téléchargement sans authentification.
  GitHub Actions publie aussi l'image et un artefact HA après les tests sur
  `main`. Le workflow du commit `d40cd66` a réussi ; cela ne prouve pas une
  installation ou un accès au registre sur le serveur.
- **Non vérifié :** compatibilité de la caméra 1, autonomie sans cloud,
  B-frames, fragments entre paquets, stabilité prolongée et charge réelle.

## Blocages et prochaines étapes

Un ancien état `blocked/BLOCKED` a été rapporté. Son état actuel et son lien
avec la caméra 2 restent inconnus. Go ne le consulte pas et `serve` peut
réessayer sans limite globale de durée ou de tentatives. Une entrée HA
existante peut relancer une caméra dès que le pont répond.

Avant tout essai réel : identifier l'état canonique et tous les producteurs,
préparer et tester une réconciliation préservant l'historique, puis valider
un dispositif borné. Les anciens
services et les ports occupés doivent être pris en compte sans lancer de
seconde stack concurrente. Le [guide Portainer](../deployment/go-portainer.md)
détaille les préconditions.

Les résultats historiques et les décisions de phase sont conservés dans
`findings/` lorsqu'ils sont disponibles localement. Les validations de la
présente phase sont consignées dans son rapport.
