# Auto Follow-up Service

Service de gestion des relances automatiques pour les mails de prospection.

## Fonctionnalités

- **Planification des relances** : Crée automatiquement des relances à J+3, J+7, J+10 et J+180
- **Annulation intelligente** : Annule les relances si le prospect répond
- **Traitement périodique** : Endpoint pour traiter les relances en attente (à appeler via Cloud Scheduler)

## Endpoints

### POST /schedule-followups
Planifie les relances pour un draft envoyé.

```json
{
  "draft_id": "uuid-du-draft"
}
```

### POST /cancel-followups
Annule toutes les relances d'un draft (appelé quand le prospect répond).

```json
{
  "draft_id": "uuid-du-draft"
}
```

### POST /process-pending-followups
Traite les relances en attente (à appeler périodiquement via Cloud Scheduler).

**Processus** :
1. Récupère les relances dont `scheduled_for <= now` et `status == "scheduled"`
2. Vérifie que le draft original n'a pas reçu de réponse (`has_reply != true`)
3. **Récupère les informations à jour depuis Odoo** via `x_external_id`
4. Appelle mail-writer avec le numéro de relance approprié (1-4)
5. Marque la relance comme `sent` ou `error`

**Avantage** : Les informations du contact (nom, fonction, description) sont récupérées dynamiquement depuis Odoo, garantissant qu'elles sont toujours à jour même si elles ont changé depuis l'email initial.

## Variables d'environnement

- `DRAFT_COLLECTION` : Collection Firestore des drafts (default: "email_drafts")
- `FOLLOWUP_COLLECTION` : Collection Firestore des relances (default: "email_followups")
- `MAIL_WRITER_URL` : URL du service mail-writer pour générer les relances
- `ODOO_DB_URL` : URL de base Odoo (ex: https://lightandshutter.odoo.com)
- `ODOO_SECRET` : Token d'authentification Odoo

**Important** : Les variables `ODOO_DB_URL` et `ODOO_SECRET` sont requises pour récupérer les informations à jour des contacts depuis Odoo lors du traitement des relances.

## Déploiement

```bash
gcloud run deploy auto-followup \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars DRAFT_COLLECTION=email_drafts,FOLLOWUP_COLLECTION=email_followups,MAIL_WRITER_URL=https://mail-writer-xxx.run.app,ODOO_DB_URL=https://lightandshutter.odoo.com,ODOO_SECRET=your_token
```

## Configuration Cloud Scheduler

Créer un job pour traiter les relances toutes les heures :

```bash
gcloud scheduler jobs create http process-followups \
  --location=europe-west1 \
  --schedule="0 * * * *" \
  --uri="https://auto-followup-xxx.a.run.app/process-pending-followups" \
  --http-method=POST \
  --time-zone="Europe/Paris"
```

## Structure Firestore

### Collection email_followups
```json
{
  "draft_id": "uuid-du-draft-original",
  "version_group_id": "uuid-du-groupe",
  "x_external_id": "pharow-company-id",  // Important: utilisé pour récupérer infos Odoo
  "to": "prospect@example.com",
  "subject": "Sujet original",
  "scheduled_for": "2025-11-27T10:00:00Z",
  "days_after_initial": 3,  // 3, 7, 10, ou 180
  "status": "scheduled | sent | cancelled | processing | error",
  "created_at": "2025-11-24T10:00:00Z",
  "sent_at": "2025-11-27T10:15:00Z",
  "draft_id_created": "uuid-du-nouveau-draft",  // ID du draft de relance créé
  "cancelled_at": "...",
  "cancellation_reason": "prospect_replied | manual | ...",
  "error": "error message if status == error"
}
```

**Note** : Le champ `x_external_id` est crucial car il permet de faire le lien avec Odoo pour récupérer les informations actuelles du contact lors du traitement de la relance.
