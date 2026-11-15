# Observations connues avant l'analyse

## Faits issus des tests réalisés par le propriétaire

- Deux caméras annoncées comme **Netvue Vigil Cam** ; références matérielles précises et versions des firmwares non encore fournies.
- Aucune carte microSD installée à ce jour.
- Toutes deux accessibles sur le LAN de l'utilisateur lors du premier scan.
- Scan TCP complet des ports 1–65535 : seul **5555/tcp** a été observé comme ouvert sur chacune.
- Les ports 23/tcp (Telnet) et 554/tcp (RTSP standard) ont été observés fermés.
- `nmap -sV --version-light` a échoué à identifier le protocole du port 5555 (`freeciv?` n'est qu'une supposition de nom de service).
- Lors d'un contrôle ultérieur, l'une des caméras ne répondait temporairement plus. Ne pas déduire une panne du seul résultat Nmap.
- Le serveur Home Assistant est un Debian exécutant Docker ; son accès réseau ne donne pas automatiquement accès au trafic unicast entre l'application mobile et les caméras.

## Hypothèses à vérifier

- Certaines Netvue Vigil de première génération sont documentées avec un SoC **Goke GK7101**, sans preuve que ces deux exemplaires utilisent cette révision : [comparatif fabricant](https://www.netvue.com/blogs/netvue-blog/compare-netvue-cameras).
- [CamStream GK7101](https://github.com/Nicooow/netvue-camstream-GK7101) dépend du tampon d'enregistrement de l'appareil sur SD et d'un accès Linux root. Il n'est pas directement utilisable dans l'état actuel.
- [Netvue-Hacks](https://github.com/WelterRocks/Netvue-Hacks) nécessite aussi un accès préalable à Linux ; aucune prise en charge directe de 5555 n'est démontrée.
- [Netvue](https://github.com/netvue) publie des dépôts logiciels et une [page de versions Android](https://resource.netvue.com/android/netvue/release.html). Ce ne sont pas des preuves de l'existence d'une API vidéo publique.

## Questions ouvertes

1. Quelle version Android officielle prend encore en charge cette génération de Vigil ?
2. Quels modules natifs sont chargés pendant le live view ?
3. Le transport vidéo est-il direct, relayé par le cloud, ou les deux ?
4. Le service TCP 5555 appartient-il au protocole vidéo, au contrôle, ou à un autre mécanisme ?
5. Une intégration conforme et autorisée peut-elle éviter l'accès root et la carte SD ?
