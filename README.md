# ticketapp-nextwave

Billetterie en ligne de **NextWave** — événement actuel : **XMAS with Q-Dot**, 25 & 26 déc. 2026, J5 Lounge (Ilara, Ogun State)
(Bénin 🇧🇯 / Nigeria 🇳🇬) : vente de tickets en XOF ou en NGN, ticket avec QR code signé et numéro (affiché, téléchargé
et envoyé par e-mail), **1 ticket = 1 entrée par jour de l'événement**, impression des tickets en lot, application de scan
installable (PWA) et panneau d'administration complet. Le site reprend le style de l'affiche (rouge, flocons, or ;
polices Anton et Montserrat, auto-hébergées).

- **Backend** : Python 3.12, Django 5.2, PostgreSQL (SQLite en développement)
- **Admin** : django-unfold
- **Paiements** : FedaPay et FeexPay (Bénin, XOF) · Paystack et Flutterwave (Nigeria, NGN)
- **Tâches** : Celery + Redis (exécution directe si Redis est absent)
- **Tickets** : QR `qrcode`, PNG `Pillow`, PDF `WeasyPrint`
- **Langues** : anglais par défaut, français (`locale/fr`)

## Fonctionnalités

| Page | Rôle |
|---|---|
| `/` | Page unique : l'affiche (titre, prix, dates, compte à rebours, affiche officielle), 3 fiches (lieu, dates, billetterie), « Choisis ton pass » + note bracelet RFID, « Du clic à l'entrée », line-up (têtes d'affiche + liste par catégorie) et vidéos YouTube, infos pratiques (numéros de réservation, plan, points de vente par ville), FAQ |
| `/checkout/<type>/` | Achat sur une seule carte : devise (seulement celles où le ticket est vendu) → nombre de tickets → coordonnées → paiement |
| `/order/<ref>/` | Succès (téléchargement automatique), attente (vérification toutes les 3 s) ou échec |
| `/find-ticket/` | Renvoi des tickets par e-mail (limité à 5 essais/heure) |
| `/buy/` | Lien court vers la section tickets (cible du QR code d'affiche) |
| `/scan/` | Scanner de l'entrée, installable comme application (Android : bouton « Installer » ; iPhone : Safari → Partager → Sur l'écran d'accueil). Connexion avec **identifiant + code d'accès** donnés par l'admin (6 scanners actifs au maximum), ou compte staff |
| `/nwp-admin/` | Administration (URL modifiable avec `ADMIN_URL`) |

Sécurité : le ticket n'est émis qu'après vérification **côté serveur** du paiement (webhook signé et/ou API de vérification,
montant et devise contrôlés), un webhook reçu deux fois n'émet pas deux fois les tickets, et le scan est atomique
(`select_for_update`) : un ticket scanné en même temps sur deux téléphones n'est accepté qu'une fois.
Les clés des passerelles sont chiffrées en base (Fernet) et ne sont jamais réaffichées.

## Structure

```text
config/      réglages (base/dev/prod), urls, celery, formats
core/        réglages du site, événement, diaporama, FAQ, points de vente, accueil, tableau de bord admin
lineup/      catégories d'artistes, artistes
tickets/     types de tickets, commandes, tickets, QR, rendu PNG/PDF, e-mails, tâches
payments/    passerelles + providers/ (fedapay, feexpay, paystack, flutterwave), webhooks
scanner/     scan d'entrée, API, journal des scans, PWA
templates/   pages, e-mails, ticket PDF, scanner, admin
static/      css, js, img, fonts
locale/      traduction française
fixtures/    données de démonstration
deploy/      Nginx, systemd, entrypoint Docker
```

## Installation locale

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # puis ajustez si besoin (SQLite et e-mails en console par défaut)
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo      # contenu de l'affiche (XMAS with Q-Dot, artistes, tickets, FAQ, villes) + affiche officielle
python manage.py runserver
```

- Site : http://localhost:8000 · Admin : http://localhost:8000/nwp-admin/ · Scanner : http://localhost:8000/scan/
- Compte scanner de test : `python manage.py seed_demo --scanner-email scan@exemple.com --scanner-password '...'`,
  ou dans l'admin : créez un utilisateur et ajoutez-le au groupe **Scanner**.
- WeasyPrint a besoin de Pango (`sudo apt install libpango-1.0-0 libpangoft2-1.0-0`). Sinon le PDF est produit par Pillow.
- Sans Redis, les tâches (génération des tickets, e-mails) s'exécutent directement. Avec Redis :
  `REDIS_URL=redis://localhost:6379/0` puis `celery -A config worker -l info` et `celery -A config beat -l info`.

Tests : `python manage.py test` (le test de double scan simultané ne tourne que sur PostgreSQL).

## Variables d'environnement

Tout est documenté dans [`.env.example`](.env.example). En production, sont **obligatoires** :

| Variable | Rôle |
|---|---|
| `SECRET_KEY` | Clé Django (40 caractères minimum) |
| `FIELD_ENCRYPTION_KEY` | Clé Fernet qui chiffre les clés des passerelles en base |
| `QR_SECRET_KEY` | Clé HMAC des QR codes — la changer invalide tous les tickets émis |
| `SITE_URL` | URL publique (e-mails, retours de paiement, webhooks, QR d'affiche) |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Domaines autorisés |
| `DATABASE_URL`, `REDIS_URL` | PostgreSQL et Redis |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | SMTP pour l'envoi des tickets |

## Configurer les passerelles de paiement

Les clés se saisissent **uniquement dans l'admin** : *Passerelles de paiement* → choisir la passerelle →
mode Sandbox/Live → clés → cocher *Actif* → bouton **Tester la connexion**. La page affiche aussi les URL à déclarer.
Seules les passerelles actives avec leurs clés apparaissent au paiement.

| Passerelle | Pays | Où trouver les clés | À déclarer chez le fournisseur |
|---|---|---|---|
| **FedaPay** | Bénin (XOF) | Tableau de bord FedaPay → *Paramètres → API* : clé secrète `sk_sandbox_…`/`sk_live_…` (champ *clé secrète*). *Webhooks* → créer un endpoint → secret `wh_…` (champ *clé supplémentaire*) | Webhook : `https://VOTRE-DOMAINE/payments/fedapay/webhook/` |
| **FeexPay** | Bénin (XOF) | Tableau de bord FeexPay → *Développeurs* : jeton API (champ *clé secrète*) et ID de boutique (champ *clé supplémentaire*) | Webhook : URL complète affichée dans l'admin (contient une clé `?k=…`) |
| **Paystack** | Nigeria (NGN) | *Settings → API Keys & Webhooks* : `sk_test_…`/`sk_live_…` (champ *clé secrète*, elle signe aussi les webhooks) | Webhook URL : `https://VOTRE-DOMAINE/payments/paystack/webhook/` |
| **Flutterwave** | Nigeria (NGN) | *Settings → API Keys* : `FLWSECK_TEST-…`/`FLWSECK-…` (champ *clé secrète*). *Settings → Webhooks* : définissez un *Secret hash* (champ *clé supplémentaire*) | Webhook URL : `https://VOTRE-DOMAINE/payments/flutterwave/webhook/` |

Le retour navigateur après paiement (`/payments/<passerelle>/callback/`) est configuré automatiquement à chaque transaction.

**Tester en sandbox** : mettez la passerelle en mode *Sandbox* avec les clés de test, achetez un ticket avec les
numéros/cartes de test du fournisseur, puis vérifiez dans l'admin : *Commandes* (statut *Payée*), *Tickets*, et
*Journal des passerelles* (requêtes et webhooks, sans données sensibles). En local, les webhooks ne peuvent pas
atteindre votre machine : la page d'attente interroge alors directement la passerelle (toutes les ~8 s).

> FeexPay : sa documentation n'est lisible qu'en ligne. L'intégration suit les points d'API de ses SDK officiels
> (`FEEXPAY_API_BASE` modifiable). Validez-la en sandbox avec le support FeexPay avant la mise en production.

## Déploiement avec Docker

```bash
cp .env.example .env    # renseignez les variables de production (voir plus haut)
# Sur un serveur partagé : WEB_PORT=8460, MEDIA_DIR=/chemin/data/media, PRIVATE_MEDIA_DIR=/chemin/data/private_media
docker compose -p nextwave up -d --build      # web, db, redis, celery, celery-beat
docker compose -p nextwave exec web python manage.py createsuperuser
docker compose -p nextwave exec web python manage.py seed_demo     # facultatif
```

Le conteneur `web` applique les migrations au démarrage et écoute sur `127.0.0.1:${WEB_PORT}`.
Nginx (sur l'hôte) termine le HTTPS et transmet au conteneur : voir [`deploy/nginx.conf`](deploy/nginx.conf)
ou l'exemple réel [`deploy/nginx.ticket.j5group.bar.conf`](deploy/nginx.ticket.j5group.bar.conf)
(domaine principal + redirections). Certificat : `certbot certonly --webroot -w /var/www/certbot -d votre-domaine`.

Mise à jour : copiez le code (sans écraser `.env` ni le dossier des médias), puis `docker compose -p nextwave up -d --build`.

## Déploiement sans Docker (Gunicorn + Nginx)

```bash
sudo apt install python3.12-venv postgresql redis-server nginx libpango-1.0-0 libpangoft2-1.0-0 fonts-liberation gettext
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
export DJANGO_SETTINGS_MODULE=config.settings.prod   # + variables de .env
.venv/bin/python manage.py migrate && .venv/bin/python manage.py collectstatic --noinput && .venv/bin/python manage.py compilemessages
```

Services systemd fournis dans `deploy/` : `nextwave-gunicorn.service`, `nextwave-celery.service`,
`nextwave-celery-beat.service` (libère les réservations de 15 min expirées). Sans Celery beat, planifiez
`python manage.py release_expired_orders` chaque minute (cron).

## Administration — repères

- **Réglages du site** : couleurs, contacts, ventes ouvertes/fermées, photo obligatoire, frais, textes légaux,
  et **QR code de la page d'achat** (PNG/SVG pour les affiches).
- **Événement / Diaporama / Artistes / Types de tickets / Points de vente / FAQ** : tout le contenu EN/FR.
- **Événement** : *nombre de jours* (2 = valable le 25 et le 26), ligne « présenté par », numéros de réservation,
  note sous les tickets (ex. bracelet RFID), vidéos YouTube (liste de liens, onglet de l'événement ou menu *Vidéos YouTube*).
  Une soirée qui dépasse minuit compte pour le jour où elle a commencé (jusqu'à 6 h).
- **Design des tickets** (par type) : importez **un exemplaire de votre design** (ex. 2400 × 900 px, proportions conservées) ;
  le système ajoute sur chaque ticket le QR code, le code et le **numéro** (N° 00042, qui se suit dans l'événement),
  et le titulaire pour les tickets en ligne (position et taille réglables, aperçu en direct). Sans visuel, un billet
  au style de l'affiche est dessiné (cases JOUR 1 / JOUR 2 sur le talon). Le PDF est au format billet.
- **Impression en lot** : type de ticket + quantité (1 à 1 000) + format **A4 avec 4 ou 5 tickets par page** (ou taille
  ticket, 1 par page) → le système crée les tickets (QR, code, numéros qui se suivent, valables chaque jour) puis le PDF
  avec traits de coupe. Ils comptent dans le stock, sans recette en ligne ni e-mail. Changer le format et enregistrer
  régénère le PDF. Dans *Tickets*, l'action « Imprimer les tickets sélectionnés » fait un PDF A4 d'une sélection.
- **Accès scanner** : nom, identifiant (ex. `scan1`), code généré, date de fin facultative ; la fiche donne le lien de la
  page de scan, l'identifiant, le code et un bouton « Envoyer par WhatsApp ». Décocher *actif* ou « Nouveau code »
  déconnecte le téléphone au scan suivant. *Entrées* liste chaque entrée (jour, heure, scanner).
- **Cartes staff** : nom, prénom, fonction (Staff, Sécurité, Organisation, Artiste, Presse, Bénévole, Technique),
  photo, format carte de membre 85,6 × 54 mm au style de l'affiche (PNG/PDF, export groupé). Leur QR est accepté par le scanner
  (« STAFF ✓ », passages illimités) tant que la carte est active.
- **Commandes** : filtres, recherche, renvoi des tickets, *marquer payée*, annulation, export CSV/Excel.
- **Invitations** : tickets gratuits (invités, presse, artistes) envoyés par e-mail.
- **Tickets** : aperçu du QR, régénération, annulation, remise à zéro de l'entrée.
- **Journal des scans** et **utilisateurs** (groupe *Scanner* pour l'équipe d'entrée).
