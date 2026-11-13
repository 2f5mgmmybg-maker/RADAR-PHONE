# RADAR PHONE

Tableau de supervision téléphonique avec un seul répertoire **Dossier contacts** et recherche automatique du correspondant à partir du numéro reçu.

## État actuel

Cette première version fournit :
- une interface web de supervision ;
- une API FastAPI ;
- import de plusieurs fichiers CSV et XLSX dans le même dossier de contacts ;
- détection de colonnes usuelles (téléphone, prénom, nom, province, commune, date de naissance) ;
- recherche automatique d'une fiche lors de la réception d'un événement d'appel ;
- diffusion des événements en temps réel au tableau de bord via WebSocket ;
- stockage PostgreSQL en production (SQLite uniquement pour le développement local).

**Important : Zoiper n'est pas encore connecté.** Le statut « Registered » d'un compte SIP ne donne pas à lui seul accès aux événements d'appel. Il faudra raccorder une API PBX (AMI/ARI), l'API du fournisseur SIP, un flux CDR/événements ou une passerelle autorisée.

## Lancement local

1. Créer un environnement virtuel Python.
2. Installer les dépendances : `pip install -r requirements.txt`.
3. Copier `.env.example` vers `.env` et définir `DATABASE_URL` et un long `RADAR_API_TOKEN`.
4. Lancer : `uvicorn app.main:app --reload`.
5. Ouvrir `http://127.0.0.1:8000`.

## Déploiement Render

Le fichier `render.yaml` décrit le service web et sa base PostgreSQL. Après import du dépôt dans Render, vérifier le plan de base disponible et les variables d'environnement. Le jeton API est généré par Render via `generateValue`.

## API d'événement d'appel

Envoyer un POST JSON à `/api/calls/incoming` avec l'en-tête `Authorization: Bearer VOTRE_JETON` :

```json
{"phone":"+33123456789"}
```

L'API journalise l'événement et cherche automatiquement le numéro dans Dossier contacts.

## Sécurité et données

- Ne jamais committer de fichiers Excel/CSV réels, données de contacts, mots de passe SIP, jetons ou fichiers `.env`.
- Les numéros, noms et dates de naissance sont des données personnelles : limiter l'accès aux personnes autorisées, définir une durée de conservation et respecter les obligations légales applicables.
- Cette première version utilise un jeton API partagé ; avant un usage réel multi-utilisateur, ajouter une authentification nominative, des rôles, des journaux d'accès et des protections adaptées.
- Le serveur ne reçoit aucune donnée de contact tant qu'un fichier n'est pas importé par un utilisateur autorisé.
