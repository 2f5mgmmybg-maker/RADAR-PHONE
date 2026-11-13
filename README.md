# RADAR PHONE

Tableau de supervision téléphonique avec un seul répertoire **Dossier contacts** et recherche automatique du correspondant à partir du numéro reçu.

## Fonctions présentes

- Interface web mobile et API FastAPI.
- Import de plusieurs fichiers CSV, XLSX et XLSM dans le même dossier.
- Détection des colonnes de téléphone, prénom, nom, province, commune et date de naissance.
- Import de fichiers sans ligne d'en-tête : détection automatique de la colonne téléphone et prise en charge d'un ordre conventionnel des champs.
- Mise à jour d'une fiche existante lorsque le même numéro est réimporté, plutôt que création systématique d'un doublon.
- Recherche exacte du contact lors de la réception d'un événement d'appel.
- Historique des événements reçus et diffusion en direct par WebSocket.
- Vérification automatisée de la syntaxe Python et du démarrage de l'API via GitHub Actions.

## Limite importante : Zoiper

**Zoiper n'est pas encore connecté à l'application.** Le statut « Registered » d'un compte SIP ne transmet pas à lui seul les événements d'appel à RADAR PHONE. Il faut une source autorisée et compatible : API de PBX (AMI/ARI), API du fournisseur SIP, flux CDR/événements ou passerelle. Tant que cette intégration n'est pas réalisée, les appels ne s'afficheront pas automatiquement depuis Zoiper.

## Lancement local

1. Créer un environnement virtuel Python.
2. Installer les dépendances : `pip install -r requirements.txt`.
3. Copier `.env.example` vers `.env` et définir `DATABASE_URL` et un long `RADAR_API_TOKEN`.
4. Lancer : `uvicorn app.main:app --reload`.
5. Ouvrir `http://127.0.0.1:8000`.

## Render

Le fichier `render.yaml` configure un service web, le démarrage sur le port fourni par Render (`$PORT`), le contrôle de santé `/health`, une base PostgreSQL et un jeton généré. Après déploiement, vérifier dans Render que la base a bien été créée et que `DATABASE_URL` et `RADAR_API_TOKEN` sont présents. Le fichier YAML ne prouve pas à lui seul que le service Render existant utilise ces réglages.

## API d'événement d'appel

Envoyer un POST JSON à `/api/calls/incoming` avec l'en-tête `Authorization: Bearer VOTRE_JETON` :

```json
{"phone":"+33123456789"}
```

L'API enregistre l'événement, recherche le numéro dans Dossier contacts et diffuse le résultat au tableau de bord.

## Sécurité et données

- Ne jamais committer de fichiers Excel/CSV réels, données de contacts, mots de passe SIP, jetons ou fichiers `.env`.
- Les numéros, noms et dates de naissance sont des données personnelles : limiter l'accès aux personnes autorisées, définir une durée de conservation et respecter les obligations légales applicables.
- La version actuelle utilise un jeton API partagé ; elle n'est pas encore une solution multi-utilisateur de production. Il faudra ajouter une authentification nominative, des rôles, des journaux d'accès et des protections adaptées avant un usage réel.
- Les imports sont limités à 15 Mo par fichier.
