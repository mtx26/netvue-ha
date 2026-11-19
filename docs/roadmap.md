# Netvue Bridge â€” Roadmap

## Objectif

DÃ©velopper une passerelle locale Netvue vers RTSP et Home Assistant,
fiable, performante, modulaire et simple Ã  installer.

## Ã‰tats

- âœ… TerminÃ© et validÃ©
- ðŸ§ª ImplÃ©mentÃ©, validation rÃ©elle nÃ©cessaire
- ðŸ”„ En dÃ©veloppement
- â¬œ Ã€ faire
- ðŸ’¡ Ã€ Ã©tudier

## 1. FiabilitÃ© et intÃ©gration

| Ã‰tat | FonctionnalitÃ© | Objectif |
|---|---|---|
| ðŸ§ª | Protocole Netvue | Connexion et vidÃ©o fiables ; [guide de reconstruction LAN](../findings/phase44-specification-reconstruction-lan.md) |
| ðŸ§ª | Supervision | Reconnexion et fonctionnement continu |
| ðŸ§ª | Multi-camÃ©ra | Sessions indÃ©pendantes |
| ðŸ§ª | Home Assistant | IntÃ©gration complÃ¨te |
| ðŸ§ª | DÃ©couverte LAN | DÃ©couverte UDP rÃ©elle confirmÃ©e en [phase 35](../findings/phase35-decouverte-lan-ha.md) ; crÃ©ation depuis HA et autres modÃ¨les Ã  valider |
| âœ… | CI multi-composants | Go/race/vet, tests HA, MediaMTX/FFmpeg, E2E HA â†” Go â†” MediaMTX et rendu Swarm rÃ©ussis dans le [run CI](https://github.com/mtx26/netvue-ha/actions/runs/44139631957) ; la publication A/V synthÃ©tique est dÃ©sormais incluse |
| 🧪 | Refonte tests et CI (phase 47) | `ci.yml` (jobs parallèles, aucun SKIP toléré), simulateur Netvue commun Go, E2E Go, harnais HA réel, fuzzing étendu ; validé hors ligne (Go/HA locaux), **premier run GitHub à confirmer** ([rapport](../findings/phase47-refonte-tests-ci.md)) |
| âœ… | Validation rÃ©elle courte (phase 36) | 30 s de H.264 dÃ©codÃ©es depuis la session active de la camÃ©ra ; le digest dÃ©ployÃ© n'est pas reliÃ© au commit local ([rapport](../findings/phase36-validation-reelle.md)) |
| âœ… | Endurance synthÃ©tique | Multi-camÃ©ra, camÃ©ra dÃ©faillante, churn, annulation et libÃ©ration de goroutines testÃ©s dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) |
| 🧪 | CI d'endurance | `endurance.yml` : race répété, fuzz long, endurance multi-caméra, manuel ou nocturne ; premier run GitHub à confirmer |
| ðŸ§ª | StabilitÃ© rÃ©elle 24 h/24 | Protocole prÃ©parÃ© dans la [phase 35](../findings/phase35-fiabilite-prolongee-et-performances.md) ; aucune validation rÃ©elle effectuÃ©e |
| ðŸ§ª | Performances de rÃ©fÃ©rence | Benchmarks et profils CPU/heap du gestionnaire ; aucune optimisation justifiÃ©e |

## 2. Audio

| Ã‰tat | FonctionnalitÃ© | Objectif |
|---|---|---|
| ðŸ§ª | Analyse APK audio | Analyse statique des trames et directions ; rÃ©ception AAC confirmÃ©e sur la camÃ©ra 2, unitÃ© de `samplesize` inconnue |
| ðŸ§ª | RÃ©ception audio | AAC/ADTS 8 kHz mono dÃ©codÃ© sur la camÃ©ra 2 ; PCM 16 bits non validÃ© sur camÃ©ra |
| ðŸ§ª | RTSP audio/vidÃ©o | AAC-LC mono 8 kHz capturÃ© sur la camÃ©ra ; pendant le talk, la publication a redÃ©marrÃ© au changement de format audio et a attendu un IDR aprÃ¨s changement H.264. ContinuitÃ© A/V et avertissements DTS restent Ã  confirmer ([phase 45](../findings/phase45-validation-reelle-talkback.md), [phase 39](../findings/phase39-talkback.md)) |
| ðŸ§ª | Audio bidirectionnel Go | Envoi Go direct d'une sinusoÃ¯de AAC-LC/ADTS 700 Hz, 0,5 s, -33,5 dBFS RMS ; pic spectral de retour Ã  -53,7 dBFS, contre -62,3/-64,4 dBFS avant/aprÃ¨s. Signal compatible avec un retour acoustique, mais haut-parleur et son audible non confirmÃ©s ([phase 45](../findings/phase45-validation-reelle-talkback.md)) |
| ðŸ§ª | Microphone Home Assistant | Provider WebRTC opt-in et transport synthÃ©tique validÃ©s ; microphone navigateur/tÃ©lÃ©phoneâ†’camÃ©ra non testÃ© en rÃ©el. ContrÃ´le expÃ©rimental dÃ©sactivÃ© par dÃ©faut ; `two_way_audio` et bouton restent masquÃ©s par dÃ©faut ([phase 43](../findings/phase43-microphone-ha.md), [phase 45](../findings/phase45-validation-reelle-talkback.md)) |

## 3. Enregistrement

| Ã‰tat | FonctionnalitÃ© | Objectif |
|---|---|---|
| â¬œ | Mesures de capacitÃ© | DÃ©bit, stockage et ressources |
| â¬œ | Enregistrement continu | Enregistrer les vidÃ©os |
| â¬œ | RÃ©tention | GÃ©rer automatiquement l'espace disque |
| â¬œ | Historique | Consulter les enregistrements |
| â¬œ | Exportation | TÃ©lÃ©charger des extraits |
| â¬œ | Captures | Enregistrer des images |

## 4. DÃ©tection et statistiques

| Ã‰tat | FonctionnalitÃ© | Objectif |
|---|---|---|
| ðŸ’¡ | DÃ©tection de mouvement | Comparer Netvue, Go et Frigate |
| â¬œ | Zones de dÃ©tection | Configurer les zones surveillÃ©es |
| â¬œ | Notifications HA | Signaler les Ã©vÃ©nements |
| â¬œ | Statistiques | Suivre Ã©vÃ©nements et disponibilitÃ© |
| ðŸ’¡ | DÃ©tection d'objets | Personnes, animaux et vÃ©hicules |

## 5. ExpÃ©rience utilisateur

| Ã‰tat | FonctionnalitÃ© | Objectif |
|---|---|---|
| ðŸ§ª | WebRTC | Provider HA opt-in, RTSP/TCP H.264/AAC, ICE UDP limitÃ© Ã  l'IPv4 source et conversion audio intÃ©grÃ©s ; scÃ©narios mÃ©dia synthÃ©tiques validÃ©s, usage rÃ©el dans le navigateur et accÃ¨s rÃ©seau externe Ã  confirmer |
| ðŸ§ª | Miniature camÃ©ra HA | Image complÃ¨te proportionnelle dans le badge 80 Ã— 80 ; proxy et formats synthÃ©tiques testÃ©s, affichage rÃ©el Ã  confirmer ([phase 46](../findings/phase46-miniature-ha.md)) |
| â¬œ | Configuration HA | Simplifier les rÃ©glages |
| ðŸ§ª | Alertes de panne | EntitÃ© Â« CamÃ©ra indisponible Â» et diagnostics actionnables ajoutÃ©s ; tests HA/Portainer passÃ©s en conteneur isolÃ©, Ã©tat rÃ©el HA non vÃ©rifiÃ© |
| â¬œ | Dashboard | Centraliser les informations |
| ðŸ§ª | Installation Portainer | Guide et stack disponibles ; dÃ©ploiement rÃ©el non validÃ© |

## RÃ¨gles de suivi

- Mettre Ã  jour les Ã©tats aprÃ¨s chaque dÃ©veloppement.
- Distinguer implÃ©mentation et validation rÃ©elle.
- Ajouter une fonctionnalitÃ© lorsqu'un nouveau besoin apparaÃ®t.
- Ã‰viter les fonctionnalitÃ©s redondantes.
- Conserver les dÃ©cisions techniques dÃ©taillÃ©es dans les documents adaptÃ©s.
- Utiliser cette roadmap comme rÃ©fÃ©rence pour les prochaines phases.
