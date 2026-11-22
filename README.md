# YDZ OS — Home Pi Lab

**YDZ OS** est le système maison de mon **Home Pi Lab** : un petit labo d'auto-hébergement construit autour de
Raspberry Pi 5 (le premier s'appelle **Pi One**), avec un tableau de bord web, un écran HDMI de contrôle et une
gestion des apps (Docker). Tout est en français et pensé pour une maison, pas pour un datacenter.

Ce dépôt publie les **paquets de mise à jour** de YDZ OS (dans le dossier [`paquets/`](paquets/))
et leur [journal des changements](CHANGELOG.md).

## Ce que fait YDZ OS

- **Tableau de bord** web : état du Pi et de la tour, températures, ventilation, stockage, terminal, apps.
- **Écran du Pi** (HDMI) : PC allumé ou éteint, météo, alertes plein écran, état des services, pages qui tournent, horloge de nuit calée sur le coucher et le lever du soleil, consommation électrique et coût.
- **Réseau** : appareils de la maison (renommables), alerte nouvel appareil, statistiques Pi-hole, test de débit nocturne, qualité du Wi-Fi.
- **Fiabilité** : historique des paquets avec annulation, sauvegarde vers le PC, notifications Discord et ntfy, joueurs Minecraft.
- **Apps et accès** : adresses HTTPS via Tailscale (jamais sur Internet), mise à jour des apps en un clic, historique 7 jours, journal d'activité.
- **Réglages généraux** : nom du labo et des Pi, Wi-Fi (avec retour automatique en cas d'échec), Bluetooth, souris, écran, heure, son.
- **Mises à jour automatiques** : **à partir de la 1.0.15, les mises à jour arrivent automatiquement** depuis ce dépôt.

## Installer ou mettre à jour

1. Télécharge `ydz-1.0.X.zip` (et son fichier `.sha256`) depuis le dossier [`paquets/`](paquets/).
2. Vérifie l'empreinte : `sha256sum -c ydz-1.0.X.zip.sha256`.
3. Ouvre YDZ, puis **Réglages → Mises à jour** et installe le `.zip` (chaque installation garde une sauvegarde : « Annuler » reste possible).

**Ordre d'installation** : les versions s'installent **dans l'ordre**, de 1.0.9 jusqu'à 1.0.15 (1.0.9, 1.0.10, 1.0.11,
1.0.12, 1.0.13, 1.0.14, 1.0.15). Chaque paquet applique ses propres réglages au moment de l'installation, donc on ne saute
pas d'étape.

**La 1.0.15 est la dernière à installer à la main.** La 1.0.14 avait introduit les mises à jour automatiques, mais elles
cherchaient dans les *Releases* de GitHub (non utilisées ici) ; c'est la **1.0.15** qui les fait lire ce dépôt (`paquets/index.json`).
Une fois en 1.0.15, YDZ cherche lui-même les nouvelles versions toutes les 6 heures, les télécharge, vérifie leur empreinte
SHA-256 et te prévient : tu les installes d'un clic dans Réglages → Mises à jour (ou la nuit à 04:00 si tu actives l'option).
Le dépôt manuel d'un `.zip` reste possible en secours.

## Sécurité

> **N'expose jamais les ports 80 (YDZ) et 7681 (terminal) à Internet** : pas de redirection de port sur la box, pas de `tailscale funnel`.
> Pour joindre YDZ de l'extérieur, utilise Tailscale (adresses HTTPS visibles uniquement par tes appareils).

Les paquets sont vérifiés par empreinte SHA-256 avant installation, et aucun mot de passe ni code PIN n'est copié dans les journaux.

## Contenu des paquets

Chaque `ydz-1.0.X.zip` contient `YDZ-PAQUET.json` (nom, version, description), les dossiers `lib/` et `ui/`, et éventuellement
`bin/`, `kiosk/` et un script `apres.sh` lancé après l'installation. Les zips, leurs empreintes `.sha256` et les notes
(`paquets/notes/`) sont versionnés dans le dossier `paquets/`, et listés dans `paquets/index.json`.
