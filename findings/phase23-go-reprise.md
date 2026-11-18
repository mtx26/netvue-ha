# Phase 23 — cahier des charges et reprise Go

## OBJECTIF

Séparer une nouvelle passerelle Go du pont Python historique, établir le
cahier des charges MVP/V1/futur, comparer les options vidéo et démarrer une
implémentation testable sans appareil réel.

## MODIFICATIONS

- Ajout de `docs/go-plan.md` : exigences, critères de sortie, architecture
  cible, avancement et décisions ouvertes.
- Ajout de `docs/netvue-protocol.md` : synthèse séparant faits confirmés,
  extrapolations limitées et inconnues, sans exposer de clé ou capture.
- Création de `netvue-go/` avec module Go distinct et versions épinglées de
  gortsplib et mediacommon.
- Ajout du cadrage Netvue borné, de la découverte UDP unicast, du login à
  résultat explicite, de l'arrêt avant LIVE en cas de refus et d'une demande
  LIVE unique.
- Ajout de l'analyse Annex B/SPS, des contrôles de taille et résolution, d'une
  horloge monotone limitée et d'un publieur direct gortsplib RTSP/TCP. Un
  changement SPS/PPS ferme l'ancienne publication et attend une IDR pour en
  ouvrir une nouvelle ; les compteurs distinguent la génération RTSP courante
  du total de session.
- Le listener RTSP loopback est vérifié avant toute découverte caméra afin
  d'éviter une tentative LAN si le destinataire local est indisponible. Les
  erreurs au bord d'une échéance de contexte sont normalisées en erreur de
  contexte plutôt qu'en simple timeout réseau.
- La durée unique commence avant le préflight et est propagée à la découverte,
  l'authentification, LIVE et RTSP. La distinction entre fin de durée configurée
  et arrêt du contexte appelant évite les succès trompeurs.
- Une échéance de démarrage absolue de cinq secondes borne aussi les requêtes
  RTSP ANNOUNCE/SETUP/RECORD ; commandes et trames non vidéo ne la repoussent
  pas. L'annulation ferme la socket RTSP avant d'attendre l'arrêt du client,
  et le callback reste armé pendant la fermeture.
- Les dimensions SPS codées et affichées sont calculées en `uint64` avec
  vérification du recadrage. Le préflight choisit IPv4/IPv6 selon l'adresse
  réellement analysée.
- La limite d'une unité H.264 est dérivée de la taille maximale d'un corps
  Netvue, et la taille de l'en-tête média est définie une seule fois.
- Ajout d'une CLI `session` à durée obligatoire, bornée à 24 heures et
  interrompable, ainsi que d'un test d'intégration optionnel utilisant
  MediaMTX/FFmpeg locaux sur loopback.
- Relecture Netvue Reviewer : défauts de fin de session et d'annulation
  corrigés en plusieurs passes. Les échéances startup/RTSP, les erreurs
  concurrentes avec l'annulation et les dimensions SPS ont des tests ciblés.
  La relecture finale n'a trouvé aucun problème important restant ; une
  fermeture bloquée après échec n'a pas été reproduite, mais les callbacks
  restent armés et la socket est interrompue pendant le nettoyage.
- Le risque de clé transportée en clair est documenté. Aucun mécanisme
  cryptographique n'a été inventé sans preuve de compatibilité caméra.
- Le choix vidéo initial est la publication RTSP directe avec gortsplib et les
  utilitaires H.264 mediacommon ; FFmpeg sert à la validation indépendante et
  MediaMTX reste le serveur séparé. Les versions/API officielles ont été
  consultées avant de retenir gortsplib v5.7.0 et mediacommon v2.10.0.
- Le code Python et son intégration HA ne sont pas modifiés.

## FONCTIONNEMENT

`protocol.go`, `discovery.go` et `authentication.go` encadrent les paquets,
valident la source et l'endpoint annoncés et conservent la clé en mémoire.
`session.go` n'envoie LIVE qu'après un résultat d'authentification explicite
réussi et demande un flux une seule fois. `video.go` attend SPS/PPS et IDR,
applique une limite de résolution et convertit l'horloge en RTP 90 kHz. La
publication RTSP est forcée sur TCP ; une génération qui ne republie pas après
reconfiguration ne peut pas être déclarée réussie. FFmpeg ne fait pas partie
du chemin de production.

## TESTS

- `go test -count=1 ./...` : réussi ; comprend tests synthétiques du cadrage,
  endpoint, authentification, annulation, erreurs média et fin de publication.
- `go test -race -count=1 ./...` : réussi.
- `go vet ./...` : réussi.
- Tests ciblés de délais, annulations, erreurs pendant `Push` et démarrage RTSP
  répétés cinq fois : réussis.
- `TestPublisherStartsRecordingWithGortsplibServer` : réussi ; la séquence
  locale complète ANNOUNCE/SETUP/RECORD aboutit avec le serveur Go.
- Relecture Netvue Reviewer finale : aucun problème important résiduel signalé.
- Une première exécution de `go test` a révélé une course entre le timeout
  réseau et l'échéance du contexte d'authentification ; la conversion explicite
  de cette échéance a été ajoutée, puis tests, détecteur de courses et vet ont
  tous réussi.
- Test loopback `TestMediaMTXPublicationDecodesWithFFmpeg` : réussi avec les
  binaires locaux MediaMTX 1.21.4 et FFmpeg n8.1.3 ; dix images synthétiques
  décodées par FFmpeg à partir d'une publication H.264 RTSP/TCP.
- Le test MediaMTX/FFmpeg n'a pas été rejoué pendant la dernière vérification :
  les chemins des binaires ne sont pas configurés dans cet environnement.
- La publication MediaMTX/FFmpeg avait été vérifiée avant la dernière
  refactorisation du démarrage gortsplib ; le serveur RTSP Go local valide le
  démarrage courant, mais le décodage indépendant n'a pas été rejoué.
- Test loopback de session : le faux appareil annonce un port dynamique,
  reçoit un login fictif refusé et confirme qu'aucune demande LIVE n'est
  envoyée.
- Aucune caméra, compte, budget réel, service de production ni registre externe
  n'a été contacté.

## ÉTAT FINAL

Le MVP local possède une session bornée, une publication RTSP/TCP et une
validation synthétique indépendante ; le chemin de production reste non
validé sur caméra. Le transport Netvue observé ne chiffre pas la clé de login
et doit rester sur un LAN de confiance. La correction historique des PTS
dupliqués n'est pas encore portée ; ce cas connu échoue explicitement. Les
B-frames et flux fragmentés ne sont pas pris en charge/validés.

Prochaine étape : terminer la revue ciblée puis caractériser les
reconfigurations SPS/PPS et les reconnexions RTSP, avant de concevoir le
superviseur unique et le contrôle multi-caméra. Aucun contact caméra ou
déploiement n'est inclus.
