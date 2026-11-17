# RADAR PHONE

Mini-application mobile et API de supervision des appels, préparées pour une future liaison téléphonique.

## Fonctions de cette version

- Un seul espace de contacts : **Dossier contacts**.
- Import multiple de fichiers CSV, XLSX et XLSM (15 Mo maximum par fichier).
- Détection des colonnes téléphone, prénom, nom, e-mail, province, commune et date de naissance.
- Import sans en-tête avec détection de la colonne téléphone et prise en charge du format historique.
- Mise à jour d'un contact lorsque son numéro existe déjà.
- Recherche du contact à chaque événement d'appel.
- Historique durable avec copie des coordonnées du contact au moment de l'appel : nom, téléphone, e-mail, province, commune, date de naissance et autres champs importés.
- Statuts d'appel pris en charge : `incoming`, `answered`, `missed`, `rejected`.
- Diffusion en direct au tableau de bord par WebSocket.
- Identifiant d'événement facultatif pour éviter d'enregistrer deux fois la même notification.

## Zoiper / SIP

**Zoiper sur iPhone n'est pas encore connecté.** Le serveur et l'interface sont préparés pour recevoir les événements d'appel, y compris les appels manqués. Cela ne prouve pas que l'application Zoiper iOS puisse transmettre ces événements directement à un serveur tiers. Il faudra vérifier le mécanisme disponible sur ton installation avant d'activer la liaison. Le statut SIP « Registered » ne suffit pas, à lui seul, à transmettre les appels.

## Démarrage local

1. Créer un environnement virtuel Python.
2. Installer : `pip install -r requirements.txt`.
3. Définir `DATABASE_URL` et un long `RADAR_API_TOKEN`.
4. Lancer : `uvicorn app.main:app --reload`.
5. Ouvrir `http://127.0.0.1:8000`.

## Render

Le fichier `render.yaml` prévoit un service web Python, un disque persistant de 1 Go monté sur `/var/data`, SQLite et un jeton généré. Vérifier que le disque est attaché et que `DATABASE_URL` et `RADAR_API_TOKEN` sont présents dans les variables d'environnement. Ne pas supprimer une base existante avant d'avoir sauvegardé les données.

## API d'événement d'appel

POST `/api/calls/events` (ou alias `/api/calls/incoming`) avec `Authorization: Bearer VOTRE_JETON`.

Exemple d'appel manqué :

```json
{
  "event_id": "identifiant-unique-de-l-evenement",
  "phone": "+33123456789",
  "status": "missed",
  "source": "zoiper-bridge"
}
```

Pour un appel décroché, envoyer `"status": "answered"`. Le serveur associe le contact et sauvegarde une copie de ses coordonnées dans l'historique.

## Sécurité et données personnelles

- Ne jamais committer de fichiers contacts réels, données personnelles, mots de passe SIP, jetons ou fichiers `.env`.
- Les numéros, noms, e-mails et dates de naissance sont des données personnelles. Restreindre l'accès, prévoir une durée de conservation et respecter les obligations légales applicables.
- Le jeton API partagé sert à la préparation et aux tests ; avant un usage réel, ajouter une authentification nominative, des rôles, des journaux d'accès et des protections adaptées.
