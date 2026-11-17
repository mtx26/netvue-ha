# Observations historiques

Faits rapportés par le propriétaire ; ils ne décrivent pas nécessairement
l'état actuel des appareils ou du réseau.

## Appareils et réseau

- Deux caméras annoncées comme Netvue Vigil ; références et firmwares exacts
  inconnus. Aucune carte microSD installée.
- Toutes deux étaient accessibles sur le LAN lors du premier scan. Un scan
  TCP complet (1–65535) n'a observé que `5555/tcp` ouvert ; `23/tcp` et le
  RTSP standard `554/tcp` étaient fermés.
- `nmap -sV --version-light` n'a identifié aucun protocole sur 5555 ;
  `freeciv?` n'était qu'une supposition du nom de service. Une caméra ne
  répondait temporairement plus lors d'un contrôle ultérieur : cela ne prouve
  pas une panne.
- HA tourne sous Debian/Docker. Son accès réseau ne donne pas
  automatiquement accès au trafic unicast mobile-caméra.

## Hypothèses et pistes non démontrées

- Certaines Vigil de première génération seraient basées sur Goke GK7101,
  sans preuve pour ces appareils : [comparatif Netvue](https://www.netvue.com/blogs/netvue-blog/compare-netvue-cameras).
- [CamStream GK7101](https://github.com/Nicooow/netvue-camstream-GK7101)
  dépend d'un tampon d'enregistrement sur SD et d'un accès root Linux ;
  indisponible dans la situation décrite.
- [Netvue-Hacks](https://github.com/WelterRocks/Netvue-Hacks) nécessite aussi
  un accès Linux préalable ; aucune prise en charge directe de 5555 démontrée.
- Les dépôts [Netvue](https://github.com/netvue) et leur
  [page de versions Android](https://resource.netvue.com/android/netvue/release.html)
  ne prouvent pas l'existence d'une API vidéo publique.

## Questions ouvertes

Compatibilité Android pour cette génération ; modules natifs du live view ;
transport direct, cloud ou mixte ; rôle de TCP/5555 ; voie autorisée sans
root ni carte SD. Les faits de protocole et leurs limites sont dans
[netvue-protocol.md](./netvue-protocol.md).
