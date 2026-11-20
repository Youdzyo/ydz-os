# Changelog

Toutes les évolutions notables de YDZ OS (Home Pi Lab) sont notées ici, de la plus récente à la plus ancienne.
Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Les paquets (`ydz-1.0.X.zip`) sont dans les
[Releases](https://github.com/Youdzyo/ydz-os/releases).

## [1.0.14] — 2026-11-20 — Mises à jour automatiques

**Mises à jour automatiques** : à partir de cette version, les nouvelles versions de YDZ OS arrivent toutes seules depuis ce dépôt.

> Installer après la 1.0.13. **À installer à la main une dernière fois** (Réglages → Mises à jour, ou dépôt du `.zip`) : ensuite, les versions suivantes arrivent toutes seules.

### Ajouté
- **Vérification automatique** des nouvelles versions sur GitHub (dépôt public `Youdzyo/ydz-os`) toutes les 6 heures, ou à la demande avec **« Rechercher des mises à jour »**. Une seule requête à l'API publique, sans compte (l'ETag évite de tout retélécharger ; limite de débit de GitHub respectée).
- **Téléchargement automatique et vérifié** : empreinte SHA-256 comparée au fichier `.sha256`, paquet relu, version du paquet = étiquette de la release, HTTPS uniquement depuis les sites GitHub (chaque redirection est contrôlée).
- **Notification** « YDZ OS 1.0.X est prête à installer » avec le début des nouveautés (interface, journal, téléphone).
- **Réglages → Mises à jour** : les versions en attente avec leurs notes ; **« Installer »** les installe toutes, dans l'ordre, chacune avec sa sauvegarde, avec l'avancement en direct (« Installation 2/3 : 1.0.15… ») et arrêt au premier problème.
- Option **« Installer automatiquement la nuit »** (à 04:00, **coupée par défaut**).
- L'écran HDMI signale discrètement une version prête.
- **Horloge de nuit avec les secondes.**
- Réglages → Général : bouton **« Tester la mise en veille (10 s) »**.
- Nouveau module `majydz.py` et script `bin/ydz-maj-chaine` (la chaîne d'installation tourne dans une unité systemd détachée qui survit aux redémarrages de YDZ). Routes : `/api/maj`, `/api/maj/verifier`, `/api/maj/appliquer`.

### Modifié
- En mode **« Écran éteint »**, le Pi coupe vraiment la sortie HDMI (plus de signal : le moniteur se met en veille).
- Le **dépôt manuel d'un `.zip`** reste possible, en secours (« Installer un fichier de mise à jour (secours) »).

### Sécurité
- Rien n'est installé sans empreinte SHA-256 valide ni paquet relu ; aucun téléchargement hors des sites GitHub ; l'installation automatique de nuit est désactivée par défaut.
- Chaque version garde sa sauvegarde : « Annuler ce paquet » marche une version à la fois.

### À faire après l'installation
- **Installer cette version à la main une fois** ; les suivantes arriveront toutes seules.
- `apres.sh` crée `/var/lib/ydz/maj` (droits 700), installe `wlr-randr` / `wlopm` si besoin et teste l'accès à GitHub (jamais bloquant).
- Facultatif : activer « Installer automatiquement la nuit » dans Réglages → Mises à jour.

### Limites connues
- Sans accès à GitHub, YDZ réessaie toutes les 6 heures sans bloquer le reste.
- Limite de GitHub : 60 requêtes par heure et par adresse IP ; si elle est atteinte, YDZ attend l'heure indiquée.
- Une chaîne d'installation coupée par un redémarrage du Pi est signalée et reprend à la prochaine installation.

## [1.0.13] — 2026-11-20 — Réglages généraux

**Réglages généraux** : la section « Général » regroupe tout ce qui concerne le Pi lui-même.

> Installer après la 1.0.12. Attention : cette version **renomme le Pi** (voir ci-dessous).

### Ajouté
- **Réglages → Général** (remplace « Écran ») avec : Nom, **Wi-Fi**, **Bluetooth**, **Souris**, **Écran**, **Heure**, **Son**.
- **Wi-Fi sûr** : réseaux à portée et enregistrés ; le changement de réseau est une transaction. Si la passerelle ou le DNS ne répondent pas dans les 60 s, **le Pi revient tout seul à l'ancien réseau** (et efface le nouveau profil s'il vient d'être créé). Le mot de passe n'apparaît jamais dans une commande ni dans un journal.
- **Bluetooth** : marche/arrêt, recherche, appairage (code à confirmer dans l'interface), connexion, oubli ; manettes, claviers, casques, enceintes.
- **Fuseau horaire** et **Son** (PipeWire ou ALSA selon ce qui est disponible).
- **Noms du labo** : l'ensemble s'appelle **« Home Pi Lab »**, ce Pi devient **« Pi One »**, puis « Pi Two »… YDZ OS reste le nom du logiciel.
- **Animations entre les pages** de l'écran HDMI.
- **Nuit calée sur le soleil** : l'écran peut s'éteindre au coucher et se rallumer au lever (calcul local, décalages réglables, 60 min après le coucher par défaut).
- Nouvelles routes : `/api/general/*`, `/api/wifi/*`, `/api/bluetooth/*`.

### Modifié
- **Renommage réseau : `forge` devient `pi-one`**. Nouvelle adresse : **http://pi-one.local/**. L'adresse IP ne change pas ; sous Tailscale le nom devient `pi-one`. Le renommage se fait une seule fois, via le script `bin/ydz-nom-hote`, et l'historique est conservé. Les rôles internes (forge, vault, pulse) ne changent pas.

### À faire après l'installation
- Mettre à jour tes favoris : `http://forge.local/` → `http://pi-one.local/`.
- Si le renommage échoue, le Pi garde le nom `forge` ; recommence dans Réglages → Général → Nom.
- `apres.sh` installe BlueZ s'il manque (jamais bloquant), passe le fuseau à Europe/Paris si le Pi était en UTC et recharge l'écran.

### Limites connues
- Pas de puce Bluetooth (ou BlueZ absent) : la section Bluetooth reste indisponible.
- Sur Pi OS Lite, le son ne passe en général que par les sorties HDMI (sans réglage de volume matériel).
- Les anciennes adresses `forge.local` ne répondent plus après le renommage.

## [1.0.12] — 2026-11-20 — Apps et accès

**Apps et accès** : des adresses HTTPS pour tes appareils, des apps à jour en un clic, de l'historique et un journal.

> Installer après la 1.0.11.

### Ajouté
- **Adresses HTTPS via Tailscale serve** (`https://<nom>.tailXXXX.ts.net/` pour YDZ, et un port par app comme Home Assistant), visibles **uniquement par tes appareils Tailscale**. Jamais `tailscale funnel`, rien n'est publié sur Internet. Activation dans Réglages → Accès à distance. Un petit relais local (127.0.0.1) dessert les apps installées sur un autre Pi.
- **Mise à jour des apps en 1 clic** : YDZ compare l'empreinte de l'image Docker locale à celle du registre (sans rien télécharger), vérifie chaque matin vers 05:00 et à la demande, et prévient une seule fois par nouvelle version.
- **Historique 7 jours** avec graphiques (CPU, température, RAM, disque, réseau, ventilateur, consommation, signal Wi-Fi), une moyenne toutes les 5 minutes. Écriture sur la carte SD limitée à une fois toutes les 30 minutes.
- **Journal d'activité** : qui a fait quoi, quand, d'où (fichier `/var/log/ydz/activite.jsonl`). Le contenu des requêtes n'est jamais recopié (ni codes PIN, ni mots de passe, ni webhooks).
- **Sauvegarde « dès que le PC s'allume »** (mode par défaut) : partie quand le PC est vu en ligne 2 minutes de suite et qu'aucune sauvegarde n'a réussi depuis 20 h. Plus d'alerte quand le PC est simplement éteint : seulement en cas d'échec ou après 3 jours sans sauvegarde. Le mode « à heure fixe » reste possible.
- **Écran HDMI** : curseur de souris masqué (réactivable dans Réglages → Écran).
- Nouvelles routes : `/api/acces`, `/api/activite`, `/api/historique`, `/api/apps/maj/…`.

### Modifié
- Mises à jour des apps, sauvegarde, notifications et pages Réseau/Apps/Réglages adaptées (nouvelle vue Historique).

### Corrigé
- La **bulle « Google Traduction »** ne s'affiche plus sur l'écran du Pi (traduction coupée dans le profil du navigateur).

### Sécurité
- Une requête relayée par Tailscale arrive au démon comme venant de 127.0.0.1, exactement comme l'écran de la tour. YDZ la **reconnaît désormais comme relayée** (en-têtes `Tailscale-*`, `X-Forwarded-*`, nom d'hôte `*.ts.net`) et ne la prend **jamais pour l'écran local** : elle doit s'authentifier comme tout visiteur distant.
- Le journal d'activité est lisible par root seulement (il contient des adresses IP) ; la route de lecture exige le niveau « action ».

### À faire après l'installation
- Si tu veux les adresses HTTPS : dans la console Tailscale (onglet DNS), activer **MagicDNS** et **HTTPS Certificates**, puis Réglages → Accès à distance. Sans Tailscale, elles restent indisponibles (le reste fonctionne).
- `apres.sh` règle les droits du journal, rend le script du kiosque exécutable et relance l'écran (nécessaire pour masquer le curseur).

### Limites connues
- L'historique est gardé en mémoire et écrit toutes les 30 minutes : un arrêt brutal peut perdre jusqu'à 30 minutes de courbes.
- La détection de mise à jour d'app compare les empreintes de l'étiquette suivie (par exemple `latest`) : une image figée sur un numéro de version précis ne signale rien.

## [1.0.11] — 2026-11-20 — Fiabilité

**Fiabilité** : pouvoir revenir en arrière, sauvegarder, être prévenu.

> Installer après la 1.0.10.

### Ajouté
- **Historique des paquets et annulation** : Réglages → Mises à jour liste les paquets installés et permet d'annuler le dernier (chaque installation garde une copie des fichiers remplacés avec un manifeste).
- **Sauvegarde automatique vers le PC** (partage Windows SMB `YDZ-Sauvegardes`, via `smbclient`) : la configuration du Pi (`/etc/ydz`, `/var/lib/ydz`) est emballée en `.tar.gz`, envoyée au PC, et seules les N plus récentes sont gardées. Le script **« Préparer le PC »** crée côté Windows un compte local dédié qui n'a accès qu'à ce partage.
- **Notifications sur le téléphone** : **ntfy** et **Discord** (webhook), avec cases à cocher par type d'alerte et limite de débit (même alerte au plus une fois par heure, 20 par heure en tout).
- **Joueurs Minecraft** : joueurs connectés, noms, version et message d'accueil du serveur Minecraft de l'app, sur la tuile Apps.
- Nouvelles routes : `/api/maj/annuler`, `/api/maj/sauvegardes`, `/api/sauvegarde`, `/api/sauvegarde/lancer`, `/api/sauvegarde/preparer-pc`, `/api/notifications`, `/api/notifications/test`, `/api/minecraft`.

### À faire après l'installation
- Pour la sauvegarde : lancer « Préparer le PC » dans Réglages (sur le PC Windows), puis renseigner le PC. Sans `smbclient` ou sans PC prêt, Réglages l'indique et le reste de YDZ fonctionne.
- Pour les notifications : coller l'URL du webhook Discord et/ou le sujet ntfy dans Réglages, puis « Envoyer un test ».
- `apres.sh` installe `smbclient` et crée `/var/lib/ydz/tmp` (droits 700).

### Limites connues
- La sauvegarde ne s'envoie que si le PC est allumé (essais toutes les heures jusqu'à 09:00, puis une alerte).
- Les données des apps ne sont pas sauvegardées par défaut (seulement la configuration de YDZ).
- Les sauvegardes de paquets d'avant la 1.0.11 n'ont pas de manifeste : le nom du paquet est retrouvé dans `paquets.log`.

## [1.0.10] — 2026-11-20 — Réseau

**Réseau** : tout ce qui se passe sur le réseau de la maison, d'un coup d'œil.

> Installer après la 1.0.9.

### Ajouté
- **Liste des appareils** du réseau, que tu peux **renommer** (« iPhone de Justin », « Télé salon »…).
- **Alerte nouvel appareil** : une notification quand un appareil inconnu rejoint le réseau.
- **Statistiques Pi-hole** : nombre de pubs et de requêtes bloquées (si l'app Pi-hole est installée).
- **Test de débit** chaque nuit à 03:30 (points de mesure publics de Cloudflare), ou à la demande depuis la page Réseau.
- **Qualité du Wi-Fi du Pi** (signal en dBm, qualité en %, débit).
- **3e page de l'écran HDMI : « réseau »**.
- Nouvelles routes : `/api/appareils`, `/api/reseau`, `/api/reseau/debit`.

### Modifié
- Nouveau module `reseau.py` ; les modules `voisins`, `hub`, `sysapi` et l'interface (accueil, réseau) évoluent en conséquence.

### À faire après l'installation
- Rien : `apres.sh` installe `iw` (lecture du Wi-Fi) s'il manque et recharge l'écran.
- Facultatif : `avahi-resolve` donne un nom aux appareils que ta box ne nomme pas.

### Limites connues
- Sans `iw`, le signal Wi-Fi est lu dans `/proc/net/wireless` (moins de détails).
- Le test de débit consomme un peu de bande passante (quelques dizaines de Mo) une fois par nuit.

## [1.0.9] — 2026-11-20 — Écran du Pi

**Écran du Pi** : l'écran HDMI branché sur le Pi devient un vrai tableau de bord de la maison.

> Installer après la 1.0.8 (première version publiée ici, aucune version antérieure requise sur GitHub).

### Ajouté
- La **version de YDZ OS** s'affiche sur l'écran du Pi.
- **Veille ou horloge de nuit** : la nuit, l'écran HDMI s'éteint (mise en veille avec `wlopm`, ou coupure de la sortie avec `wlr-randr`) ou affiche une grande horloge. Horaires et mode réglables dans Réglages (par défaut 00:00 → 07:00).
- **Tuile « PC »** : le PC de la tour est affiché allumé ou éteint.
- **Météo de Toulouse** (Open-Meteo, sans compte ni clé).
- **Alerte plein écran** : une alerte importante prend tout l'écran du Pi.
- **État des services** (tour, apps) visible d'un coup d'œil.
- **Pages qui tournent** : l'écran alterne entre la page « tour » et la page « maison ».
- **Consommation électrique** du Pi mesurée par le PMIC de la carte, avec le coût estimé.

### À faire après l'installation
- Rien : `apres.sh` installe `wlopm` si besoin, passe le Pi à l'heure de Paris s'il était resté en UTC (la nuit et la météo suivent l'heure locale) et recharge l'écran.

### Limites connues
- Sans `wlopm` (paquet indisponible), la sortie HDMI est coupée avec `wlr-randr`: certains écrans mettent plus de temps à se réveiller.
- La météo est celle de Toulouse (lieu non modifiable à ce stade).

