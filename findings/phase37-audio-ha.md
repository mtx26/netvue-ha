# Phase 37 — Audio et améliorations Home Assistant

## Objectif

Analyser statiquement le chemin audio de l'application Netvue, ajouter la
réception audio et la publication RTSP A/V lorsque les formats sont identifiés,
préparer le talk sans supposer de protocole et améliorer les diagnostics HA.

## Décisions et modifications

- L'analyse APK est statique et limitée au code Android disponible. Les types
  de trames, la sélection audio et la direction de réception sont documentés
  dans [la spécification LAN](../docs/netvue-protocol.md). Aucun trafic caméra
  n'a été capturé pendant cette phase. Le SHA-256 de l'APK vérifié dans
  l'inventaire existant est `9907dbd62af295dd3bb2b17209edb1f2f87172b749079c27b1d9902ba44ef2be`.
- Go demande la réception audio uniquement quand la découverte annonce cette
  capacité. Une sélection A/V rejetée est retentée en vidéo seule. AAC/ADTS et
  PCM 16 bits sont convertis en RTP. Une arrivée tardive de piste réarme le
  délai d'attente de l'image-clé; les anomalies d'horloge vidéo sont reflétées
  dans la base audio. Un codec ou des paramètres audio non pris en charge
  désactivent l'audio avec un avertissement sans interrompre la vidéo.
- La publication RTSP ajoute la piste audio au SDP. Le test d'intégration
  synthétique et le test de boucle LIVE couvrent l'arrivée tardive de l'audio
  et le redémarrage de publication requis pour ajouter sa piste.
- Le talk n'est pas implémenté : l'identification statique d'une commande
  directionnelle ne suffit pas à prouver le format d'émission ni le
  comportement des caméras.
- HA expose une entité de problème « Caméra indisponible », un diagnostic
  traduit, et des détails techniques comme attribut; README et tests ont été
  adaptés.
- Le test A/V MediaMTX/FFmpeg a été ajouté au sélecteur CI existant. Aucun
  service ou déploiement de production n'a été modifié.

## État avant le diagnostic approfondi

| Domaine | État |
|---|---|
| Analyse APK | Terminé statiquement; non confirmé sur une caméra |
| Réception micro Go | AAC/ADTS et PCM 16 bits implémentés; deux essais réels ont rencontré des paramètres audio hors plage; audio non confirmé |
| RTSP A/V | Testé avec MediaMTX et FFmpeg sur flux synthétiques |
| Synchronisation | Vérifiée sur timestamps synthétiques, pas sur appareil réel |
| Audio bidirectionnel | Étudié partiellement; émission non implémentée |
| Alertes et diagnostics HA | Implémentés; tests HA/Portainer passés dans un conteneur isolé sans réseau |
| Multi-caméra et régressions Go | Tests synthétiques multi-caméra, suite Go et détecteur race réussis; aucun essai réel multi-caméra |

## Tests

- `go test ./...` — réussi, avec MediaMTX et FFmpeg locaux configurés.
- `go test -race ./...` — réussi, avec le test d'intégration A/V activé.
- `go vet ./...` — réussi.
- `python -B -m unittest discover -s tests -p 'test_*.py' -v` — 40 tests
  passés dans un conteneur HA isolé (`--network none`), monté en lecture seule
  sur le serveur; les fichiers temporaires ont été supprimés.

## Vérification réelle isolée

Deux sessions bornées ont été réalisées vers un MediaMTX temporaire lié à
`127.0.0.1`. La première (35 s maximum) s'est terminée sur des paramètres de
canal audio hors plage (`audio channel parameters out of range`) avant toute
publication RTSP. Après l'ajout du repli vidéo seule, la seconde (40 s maximum)
a terminé avec succès : le diagnostic a désactivé l'audio et l'API MediaMTX a
annoncé une piste `H264` prête. Aucun paramètre audio exact n'a été journalisé
ni conservé.

FFprobe et FFmpeg ont été lancés après la fin de cette seconde session; MediaMTX
a alors répondu `404`, donc le décodage indépendant n'a pas été vérifié. Aucun
flux audio réel n'a été publié et aucun média n'a été enregistré.

L'API de production est restée en état `supervising`; le socket TCP existant est
resté établi durant les essais, avec au plus une connexion de test additionnelle.
Après fermeture du test, seul le socket de production restait. Aucun impact de
production n'a été observé. Les conteneurs, tunnels et fichiers temporaires ont
été supprimés.

## Limites avant le diagnostic approfondi

La compatibilité audio de la caméra reste non validée : les deux réponses LIVE
ont annoncé des paramètres hors plage et l'audio a été désactivé. Le repli a
permis une publication H.264 réelle, mais son décodage n'a pas été confirmé par
un lecteur indépendant. Les codecs, les timestamps A/V réels, le multi-caméra
audio et le protocole complet de talk restent à valider. Tout nouvel essai réel
nécessitera une autorisation distincte. La CI distante n'a pas été déclenchée.

## Diagnostic approfondi et correction

- L'APK 5.27.0 lit les trois champs audio LIVE dans
  `NvCameraChannelParamAudio.readFromJSON` comme `samplerate`, `samplesize`
  et `channels`. `NVCameraPlayerImpl.initAudioHandler` utilise la fréquence
  et le nombre de canaux mais pas `samplesize`. Son unité exacte en réponse
  LIVE reste inconnue : le traiter comme une profondeur en bits était injustifié.
  Le lecteur standard configure séparément PCM 16 bits ; le choix AAC ou PCM
  vient de l'en-tête média. La branche CameraII et son identifiant de codec
  sont distincts et ne prouvent pas le codec du LIVE classique.
- La caméra 2 a annoncé `[8000, 1, 1]`. Le parseur rejetait le deuxième champ
  parce qu'il exigeait 8 à 32 bits ; le pont ne demandait alors pas la direction
  audio. La première trame observée après activation était non-PCM et
  commençait par un en-tête ADTS. Le format AAC est vérifié indépendamment
  dans l'ADTS, et sa fréquence et ses canaux restent confrontés à la réponse.
- Le parseur conserve `samplesize` comme métadonnée opaque et vérifie que les
  trois valeurs sont des entiers JSON, sans accepter `null` ni affecter de
  signification à ce champ. Le PCM du lecteur standard est 16 bits ; son ordre
  d'octets reste une hypothèse non confirmée pour cette caméra. Des tests
  couvrent la réponse observée, AAC 8 kHz, PCM et une valeur JSON `null`.
- Le talk reste non implémenté : les chemins de capture, d'encodage AAC et la
  direction `TWOWAY` existent dans l'APK, mais leur interopérabilité avec
  cette caméra n'est pas établie.

## Validation réelle approfondie

Six connexions supplémentaires, chacune limitée à 50 s au plus, ont été
effectuées vers la seule caméra autorisée, avec publication RTSP sur loopback
par un MediaMTX temporaire. Aucun média n'a été enregistré. FFprobe et FFmpeg
ont été lancés pendant les sessions :

- FFprobe a identifié H.264 et AAC-LC mono 8 kHz. Après correction, FFmpeg a
  décodé simultanément 10 s de média dans la version finale : 84 images et
  156 Kio audio PCM en sortie `null`, sans erreur de décodage dans cet essai.
  Le dernier MediaMTX n'exposait que RTSP/TCP sur `127.0.0.1`.
- Sur une autre fenêtre d'environ 8 s, FFprobe a relevé 67 paquets vidéo et
  63 audio ; l'analyse des PTS, limitée à la seconde par la locale du serveur,
  n'a pas détecté de régression à cette précision. Elle ne mesure ni le décalage
  perceptuel A/V ni sa dérive prolongée.
- Un essai précédent a été interrompu après 2,93 s par une fin de flux ;
  d'autres ont signalé un DTS vidéo non monotone côté sortie
  `null`. Le diagnostic ultérieur a observé un changement de paramètres vidéo
  et le redémarrage RTSP prévu ; le lien exact avec chaque avertissement n'est
  pas établi. Le changement de résolution et la continuité lecteur restent à
  étudier.
- La socket de production a changé entre les deux premiers essais. Les journaux
  montrent une interruption vidéo et une tentative de découverte à ce moment ;
  une influence des essais est possible mais non démontrée. La production a
  ensuite repris et la même socket est restée établie pendant les essais
  suivants. Aucun service de production n'a été modifié.
- Attention : malgré l'adresse RTSP loopback, la configuration par défaut du
  MediaMTX temporaire a aussi ouvert des écouteurs UDP RTP/RTCP et MoQ sur
  toutes les interfaces. Leur accessibilité depuis Internet n'a pas été
  vérifiée. Le conteneur et ces écouteurs temporaires ont été arrêtés ;
  les futurs essais doivent désactiver explicitement les services et transports
  inutilisés ou les lier à loopback.

## Vérifications locales

La suite Go, `go test -race ./...`, `go vet ./...` et `go build ./...` ont
réussi ; les tests MediaMTX/FFmpeg étaient activés. Un premier passage race
concurrent à une autre suite a échoué sur un lecteur synthétique arrivé trop
tard ; il a réussi isolément puis dans la suite complète relancée. Les tests
ciblés, dont le rejet de `null`, ont aussi réussi sous race après la dernière
modification. Les 40 tests HA/Portainer ont réussi hors réseau et le rendu
Swarm a réussi sans déploiement. Le scénario CI HA → Go → caméra synthétique
→ MediaMTX → JPEG a réussi après adaptation de la recherche d'entités aux
nouveaux capteurs HA ; le scénario HA synthétique séparé a aussi réussi sur un
réseau Docker interne.

La synchronisation perceptuelle, l'endurance, le PCM réel, PCMU et le talk
restent non validés. La CI distante n'a pas été déclenchée.
