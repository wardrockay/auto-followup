import os
import json
import time
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from google.cloud import firestore
import requests

from structured_logger import get_logger, log_request_context, logger

app = Flask(__name__)

# Activer le middleware de logging
log_request_context(app)

# Configuration
DRAFT_COLLECTION = os.environ.get("DRAFT_COLLECTION", "email_drafts")
FOLLOWUP_COLLECTION = os.environ.get("FOLLOWUP_COLLECTION", "email_followups")
MAIL_WRITER_URL = os.environ.get("MAIL_WRITER_URL", "").rstrip("/")
ODOO_DB_URL = os.environ.get("ODOO_DB_URL", "").rstrip("/")
ODOO_SECRET = os.environ.get("ODOO_SECRET", "")

# Configuration des relances (en jours ouvrés)
FOLLOWUP_SCHEDULE = [3, 7, 10, 180]  # J+3, J+7, J+10, J+180 (jours ouvrés)

# Mapping des jours vers le numéro de relance
DAYS_TO_FOLLOWUP_NUMBER = {
    3: 1,    # J+3 = première relance
    7: 2,    # J+7 = deuxième relance
    10: 3,   # J+10 = troisième relance
    180: 4   # J+180 = relance long terme
}

# Firestore client
db = firestore.Client()


def now_utc():
    return datetime.now(timezone.utc)


def get_french_holidays(year: int) -> set:
    """
    Retourne les jours fériés français pour une année donnée.
    Inclut les jours fériés fixes et les jours fériés mobiles (Pâques, Ascension, Pentecôte).
    """
    holidays = set()
    
    # Jours fériés fixes
    holidays.add(datetime(year, 1, 1).date())    # Jour de l'an
    holidays.add(datetime(year, 5, 1).date())    # Fête du travail
    holidays.add(datetime(year, 5, 8).date())    # Victoire 1945
    holidays.add(datetime(year, 7, 14).date())   # Fête nationale
    holidays.add(datetime(year, 8, 15).date())   # Assomption
    holidays.add(datetime(year, 11, 1).date())   # Toussaint
    holidays.add(datetime(year, 11, 11).date())  # Armistice
    holidays.add(datetime(year, 12, 25).date())  # Noël
    
    # Calcul de Pâques (algorithme de Meeus/Jones/Butcher)
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter = datetime(year, month, day).date()
    
    # Jours fériés mobiles basés sur Pâques
    holidays.add(easter + timedelta(days=1))     # Lundi de Pâques
    holidays.add(easter + timedelta(days=39))    # Ascension
    holidays.add(easter + timedelta(days=50))    # Lundi de Pentecôte
    
    return holidays


def is_business_day(date: datetime) -> bool:
    """
    Vérifie si une date est un jour ouvré (pas weekend, pas jour férié).
    """
    if isinstance(date, datetime):
        date = date.date()
    
    # Weekend (samedi = 5, dimanche = 6)
    if date.weekday() >= 5:
        return False
    
    # Jours fériés
    holidays = get_french_holidays(date.year)
    if date in holidays:
        return False
    
    return True


def next_business_day(date: datetime) -> datetime:
    """
    Si la date tombe un weekend ou jour férié, retourne le prochain jour ouvré.
    Sinon retourne la date telle quelle.
    """
    original_time = date.time() if isinstance(date, datetime) else None
    
    if isinstance(date, datetime):
        current_date = date.date()
    else:
        current_date = date
    
    # Avancer jusqu'au prochain jour ouvré
    while not is_business_day(current_date):
        current_date = current_date + timedelta(days=1)
    
    # Reconstruire le datetime avec l'heure originale
    if original_time:
        return datetime.combine(current_date, original_time, tzinfo=date.tzinfo)
    else:
        return datetime.combine(current_date, datetime.min.time(), tzinfo=timezone.utc)


def add_business_days(start_date: datetime, business_days: int) -> datetime:
    """
    Ajoute un nombre de jours ouvrés à une date.
    Retourne une date qui est forcément un jour ouvré.
    """
    current = start_date
    days_added = 0
    
    while days_added < business_days:
        current = current + timedelta(days=1)
        if is_business_day(current):
            days_added += 1
    
    return current


def now_utc():
    return datetime.now(timezone.utc)


def get_email_history(draft_id: str, version_group_id: str) -> list:
    """
    Récupère l'historique de tous les emails envoyés pour ce prospect (email initial + relances précédentes).
    
    Returns:
        Liste de dicts avec subject et body de chaque email envoyé
    """
    try:
        email_history = []
        
        # Récupérer tous les drafts envoyés pour ce version_group_id
        drafts_ref = db.collection(DRAFT_COLLECTION).where("version_group_id", "==", version_group_id).where("status", "==", "sent").order_by("sent_at")
        
        for draft_doc in drafts_ref.stream():
            draft_data = draft_doc.to_dict()
            sent_at = draft_data.get("sent_at")
            # Convertir DatetimeWithNanoseconds en ISO string pour sérialisation JSON
            if sent_at and hasattr(sent_at, 'isoformat'):
                sent_at_str = sent_at.isoformat()
            else:
                sent_at_str = str(sent_at) if sent_at else None
            
            email_history.append({
                "subject": draft_data.get("subject", ""),
                "body": draft_data.get("body", ""),
                "sent_at": sent_at_str
            })
            logger.debug("Email trouvé dans historique", extra={"extra_fields": {
                "sent_at": sent_at_str,
                "version_group_id": version_group_id
            }})
        
        logger.info("Historique emails récupéré", extra={"extra_fields": {
            "email_count": len(email_history),
            "draft_id": draft_id
        }})
        return email_history
        
    except Exception as e:
        logger.error("Erreur récupération historique", extra={"extra_fields": {
            "draft_id": draft_id,
            "error": str(e)
        }})
        return []


def get_contact_info_from_odoo(x_external_id: str) -> dict:
    """
    Récupère les informations du contact depuis Odoo via l'external_id.
    
    Returns:
        dict avec first_name, last_name, email, website, partner_name, function, description, odoo_id
    """
    if not ODOO_DB_URL or not ODOO_SECRET:
        logger.error("Configuration Odoo manquante", extra={"extra_fields": {
            "has_odoo_url": bool(ODOO_DB_URL),
            "has_odoo_secret": bool(ODOO_SECRET)
        }})
        return {}
    
    if not x_external_id:
        logger.warning("x_external_id vide pour récupération Odoo")
        return {}
    
    try:
        odoo_url = f"{ODOO_DB_URL}/json/2/crm.lead/search_read"
        odoo_payload = {
            "domain": [["x_external_id", "ilike", x_external_id]],
            "fields": [
                "id",
                "email_normalized",
                "website",
                "contact_name",
                "partner_name",
                "function",
                "description"
            ]
        }
        odoo_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ODOO_SECRET}"
        }
        
        start_time = time.time()
        logger.info("Appel API Odoo", extra={"extra_fields": {
            "x_external_id": x_external_id,
            "operation": "get_contact_info"
        }})
        response = requests.post(odoo_url, json=odoo_payload, headers=odoo_headers, timeout=15)
        response.raise_for_status()
        odoo_data = response.json()
        duration_ms = int((time.time() - start_time) * 1000)
        
        if not odoo_data or len(odoo_data) == 0:
            logger.warning("Lead non trouvé dans Odoo", extra={"extra_fields": {
                "x_external_id": x_external_id,
                "duration_ms": duration_ms
            }})
            return {}
        
        lead = odoo_data[0]
        logger.info("Lead récupéré depuis Odoo", extra={"extra_fields": {
            "odoo_id": lead.get('id'),
            "x_external_id": x_external_id,
            "duration_ms": duration_ms
        }})
        
        # Extraire les informations
        contact_name = lead.get("contact_name", "")
        name_parts = contact_name.split(" ", 1) if contact_name else ["", ""]
        
        return {
            "first_name": name_parts[0] if len(name_parts) > 0 else "",
            "last_name": name_parts[1] if len(name_parts) > 1 else "",
            "email": lead.get("email_normalized", ""),
            "website": lead.get("website", ""),
            "partner_name": lead.get("partner_name", ""),
            "function": lead.get("function", ""),
            "description": lead.get("description", ""),
            "odoo_id": lead.get("id")
        }
        
    except Exception as e:
        logger.error("Erreur API Odoo", extra={"extra_fields": {
            "x_external_id": x_external_id,
            "error": str(e),
            "error_type": type(e).__name__
        }})
        return {}


@app.route("/schedule-followups", methods=["POST"])
def schedule_followups():
    """
    Planifie les relances automatiques pour un draft envoyé.
    
    Attend un JSON du type:
    {
      "draft_id": "uuid-du-draft"
    }
    
    Crée des documents dans la collection email_followups pour chaque relance planifiée.
    """
    try:
        data = request.get_json(silent=True) or {}
        draft_id = data.get("draft_id")
        
        if not draft_id:
            return jsonify({"status": "error", "error": "draft_id is required"}), 400
        
        # Récupérer le draft
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return jsonify({"status": "error", "error": "Draft not found"}), 404
        
        draft_data = doc.to_dict()
        
        # Vérifier que le draft a bien été envoyé
        if draft_data.get("status") != "sent":
            return jsonify({"status": "error", "error": "Draft not sent yet"}), 400
        
        # Vérifier si le prospect a déjà répondu
        if draft_data.get("has_reply"):
            return jsonify({"status": "cancelled", "message": "Prospect already replied, no followups scheduled"}), 200
        
        sent_at = draft_data.get("sent_at")
        if not sent_at:
            return jsonify({"status": "error", "error": "sent_at not found"}), 400
        
        # Créer les relances planifiées (en jours ouvrés)
        followups_created = []
        
        for days_after in FOLLOWUP_SCHEDULE:
            # Calculer la date en jours ouvrés (pas weekends ni jours fériés)
            followup_date = add_business_days(sent_at, days_after)
            
            followup_data = {
                "draft_id": draft_id,
                "version_group_id": draft_data.get("version_group_id"),
                "x_external_id": draft_data.get("x_external_id"),
                "to": draft_data.get("to"),
                "subject": draft_data.get("subject"),
                "scheduled_for": followup_date,
                "business_days_after": days_after,  # Jours ouvrés
                "days_after_initial": days_after,   # Gardé pour compatibilité
                "status": "scheduled",  # scheduled, sent, cancelled
                "created_at": now_utc(),
            }
            
            # Créer le document de relance
            followup_ref = db.collection(FOLLOWUP_COLLECTION).document()
            followup_ref.set(followup_data)
            
            followups_created.append({
                "id": followup_ref.id,
                "scheduled_for": followup_date.isoformat(),
                "business_days_after": days_after
            })
            
            logger.info("Relance planifiée", extra={"extra_fields": {
                "draft_id": draft_id,
                "followup_id": followup_ref.id,
                "days_after": days_after,
                "scheduled_for": followup_date.isoformat()
            }})
        
        # Mettre à jour le draft avec les relances planifiées
        doc_ref.update({
            "followups_scheduled": True,
            "followup_ids": [f["id"] for f in followups_created]
        })
        
        logger.info("Relances créées avec succès", extra={"extra_fields": {
            "draft_id": draft_id,
            "followups_count": len(followups_created)
        }})
        
        return jsonify({
            "status": "ok",
            "followups_created": len(followups_created),
            "followups": followups_created
        }), 200
        
    except Exception as e:
        logger.error("Erreur schedule_followups", extra={"extra_fields": {
            "error": str(e),
            "error_type": type(e).__name__
        }})
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/cancel-followups", methods=["POST"])
def cancel_followups():
    """
    Annule toutes les relances planifiées pour un draft SAUF celle à J+180 (quand le prospect répond).
    
    Attend un JSON du type:
    {
      "draft_id": "uuid-du-draft"
    }
    """
    try:
        data = request.get_json(silent=True) or {}
        draft_id = data.get("draft_id")
        
        if not draft_id:
            return jsonify({"status": "error", "error": "draft_id is required"}), 400
        
        # Récupérer toutes les relances planifiées pour ce draft
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("draft_id", "==", draft_id).where("status", "==", "scheduled")
        
        cancelled_count = 0
        kept_count = 0
        
        for followup_doc in followups_ref.stream():
            followup_data = followup_doc.to_dict()
            days_after = followup_data.get("days_after_initial", 0)
            
            # Garder uniquement la relance à J+180, annuler toutes les autres
            if days_after == 180:
                logger.info("Relance J+180 conservée", extra={"extra_fields": {
                    "followup_id": followup_doc.id,
                    "draft_id": draft_id,
                    "days_after": days_after
                }})
                kept_count += 1
            else:
                followup_doc.reference.update({
                    "status": "cancelled",
                    "cancelled_at": now_utc(),
                    "cancellation_reason": "prospect_replied"
                })
                cancelled_count += 1
                logger.info("Relance annulée (prospect a répondu)", extra={"extra_fields": {
                    "followup_id": followup_doc.id,
                    "draft_id": draft_id,
                    "days_after": days_after
                }})
        
        logger.info("Annulation relances terminée", extra={"extra_fields": {
            "draft_id": draft_id,
            "cancelled_count": cancelled_count,
            "kept_count": kept_count
        }})
        
        return jsonify({
            "status": "ok",
            "cancelled_count": cancelled_count,
            "kept_count": kept_count,
            "message": f"{cancelled_count} relance(s) annulée(s), {kept_count} relance(s) conservée(s) (J+180)"
        }), 200
        
    except Exception as e:
        logger.error("Erreur cancel_followups", extra={"extra_fields": {
            "draft_id": draft_id,
            "error": str(e),
            "error_type": type(e).__name__
        }})
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/process-pending-followups", methods=["POST"])
def process_pending_followups():
    """
    Endpoint à appeler périodiquement (via Cloud Scheduler par exemple)
    pour traiter les relances en attente.
    
    Parcourt les relances planifiées dont la date est dépassée,
    vérifie qu'il n'y a pas eu de réponse, et envoie la relance.
    """
    try:
        now = now_utc()
        
        # Récupérer les relances à traiter
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("status", "==", "scheduled").where("scheduled_for", "<=", now)
        
        processed_count = 0
        skipped_count = 0
        
        for followup_doc in followups_ref.stream():
            followup_data = followup_doc.to_dict()
            draft_id = followup_data.get("draft_id")
            
            # Vérifier si le draft original a reçu une réponse
            draft_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
            draft = draft_ref.get()
            
            if not draft.exists:
                logger.warning("Draft non trouvé pour relance", extra={"extra_fields": {
                    "draft_id": draft_id,
                    "followup_id": followup_doc.id
                }})
                followup_doc.reference.update({"status": "error", "error": "draft_not_found"})
                continue
            
            draft_data = draft.to_dict()
            
            # Si le prospect a répondu, annuler cette relance
            if draft_data.get("has_reply"):
                logger.info("Relance annulée - prospect a répondu", extra={"extra_fields": {
                    "followup_id": followup_doc.id,
                    "draft_id": draft_id
                }})
                followup_doc.reference.update({
                    "status": "cancelled",
                    "cancelled_at": now,
                    "cancellation_reason": "prospect_replied"
                })
                skipped_count += 1
                continue
            
            # Récupérer le numéro de relance basé sur days_after_initial
            days_after = followup_data.get("days_after_initial", 3)
            followup_number = DAYS_TO_FOLLOWUP_NUMBER.get(days_after, 1)
            
            logger.info("Traitement relance", extra={"extra_fields": {
                "followup_id": followup_doc.id,
                "days_after": days_after,
                "followup_number": followup_number,
                "to": draft_data.get('to'),
                "draft_id": draft_id
            }})
            
            # Générer et envoyer le mail de relance via mail-writer
            if MAIL_WRITER_URL:
                try:
                    # Récupérer les infos depuis Odoo en utilisant x_external_id
                    x_external_id = followup_data.get("x_external_id", "")
                    contact_info = get_contact_info_from_odoo(x_external_id)
                    
                    if not contact_info:
                        # Fallback : utiliser les infos du draft si disponibles
                        logger.warning("Fallback vers draft (Odoo indisponible)", extra={"extra_fields": {
                            "x_external_id": x_external_id,
                            "draft_id": draft_id
                        }})
                        contact_name = draft_data.get("contact_name", "")
                        name_parts = contact_name.split(" ", 1) if contact_name else ["", ""]
                        
                        contact_info = {
                            "first_name": name_parts[0] if len(name_parts) > 0 else "",
                            "last_name": name_parts[1] if len(name_parts) > 1 else "",
                            "email": draft_data.get("to"),
                            "website": draft_data.get("website", ""),
                            "partner_name": draft_data.get("partner_name", ""),
                            "function": draft_data.get("function", ""),
                            "description": draft_data.get("description", ""),
                            "odoo_id": draft_data.get("odoo_id")
                        }
                    
                    # Récupérer l'historique des emails précédents
                    version_group_id = followup_data.get("version_group_id", "")
                    email_history = get_email_history(draft_id, version_group_id)
                    
                    # Récupérer les infos de thread pour envoyer la relance en réponse au mail initial
                    gmail_thread_id = draft_data.get("gmail_thread_id")
                    gmail_message_id = draft_data.get("gmail_message_id")
                    original_subject = draft_data.get("subject", "")
                    
                    logger.debug("Thread info récupéré", extra={"extra_fields": {
                        "gmail_thread_id": gmail_thread_id,
                        "gmail_message_id": gmail_message_id,
                        "draft_id": draft_id
                    }})
                    
                    mail_writer_payload = {
                        "first_name": contact_info.get("first_name", ""),
                        "last_name": contact_info.get("last_name", ""),
                        "email": contact_info.get("email", "") or draft_data.get("to"),
                        "website": contact_info.get("website", ""),
                        "partner_name": contact_info.get("partner_name", ""),
                        "function": contact_info.get("function", ""),
                        "description": contact_info.get("description", ""),
                        "x_external_id": x_external_id,
                        "odoo_id": contact_info.get("odoo_id"),
                        "followup_number": followup_number,  # Numéro de relance
                        "version_group_id": version_group_id,
                        "email_history": email_history,  # Historique complet
                        # Infos de thread pour envoyer en réponse
                        "reply_to_thread_id": gmail_thread_id,
                        "reply_to_message_id": gmail_message_id,
                        "original_subject": original_subject
                    }
                    
                    start_time = time.time()
                    logger.info("Appel mail-writer", extra={"extra_fields": {
                        "followup_number": followup_number,
                        "followup_id": followup_doc.id,
                        "draft_id": draft_id
                    }})
                    response = requests.post(
                        MAIL_WRITER_URL,
                        json=mail_writer_payload,
                        timeout=60
                    )
                    duration_ms = int((time.time() - start_time) * 1000)
                    
                    if response.status_code == 200:
                        result = response.json()
                        logger.info("Relance générée avec succès", extra={"extra_fields": {
                            "followup_id": followup_doc.id,
                            "draft_id_created": result.get("draft", {}).get("draft_id"),
                            "duration_ms": duration_ms,
                            "status": "success"
                        }})
                        
                        # Marquer comme envoyée
                        followup_doc.reference.update({
                            "status": "sent",
                            "sent_at": now,
                            "draft_id_created": result.get("draft", {}).get("draft_id")
                        })
                        processed_count += 1
                    else:
                        logger.error("Erreur mail-writer", extra={"extra_fields": {
                            "followup_id": followup_doc.id,
                            "status_code": response.status_code,
                            "duration_ms": duration_ms,
                            "response_preview": response.text[:200] if response.text else None
                        }})
                        followup_doc.reference.update({
                            "status": "error",
                            "error": f"HTTP {response.status_code}",
                            "error_details": response.text
                        })
                        
                except Exception as mail_error:
                    logger.error("Exception appel mail-writer", extra={"extra_fields": {
                        "followup_id": followup_doc.id,
                        "error": str(mail_error),
                        "error_type": type(mail_error).__name__
                    }})
                    followup_doc.reference.update({
                        "status": "error",
                        "error": str(mail_error)
                    })
            else:
                logger.warning("MAIL_WRITER_URL non configuré", extra={"extra_fields": {
                    "followup_id": followup_doc.id
                }})
                followup_doc.reference.update({
                    "status": "error",
                    "error": "MAIL_WRITER_URL not configured"
                })
        
        logger.info("Traitement relances terminé", extra={"extra_fields": {
            "processed_count": processed_count,
            "skipped_count": skipped_count
        }})
        
        return jsonify({
            "status": "ok",
            "processed": processed_count,
            "skipped": skipped_count
        }), 200
        
    except Exception as e:
        logger.error("Erreur process_pending_followups", extra={"extra_fields": {
            "error": str(e),
            "error_type": type(e).__name__
        }})
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/retry-failed-followups", methods=["POST"])
def retry_failed_followups():
    """
    Remet les followups en erreur à scheduled pour les réessayer.
    Utile pour l'administration après correction de bugs.
    """
    try:
        # Récupérer les followups en erreur
        followups_ref = db.collection(FOLLOWUP_COLLECTION).where("status", "==", "error")
        
        reset_count = 0
        for followup_doc in followups_ref.stream():
            followup_doc.reference.update({
                "status": "scheduled",
                "error": None,
                "error_details": None
            })
            reset_count += 1
            logger.info("Followup remis en scheduled", extra={"extra_fields": {
                "followup_id": followup_doc.id
            }})
        
        return jsonify({
            "status": "ok",
            "message": f"Reset {reset_count} followups to scheduled"
        }), 200
        
    except Exception as e:
        logger.error("Erreur retry-failed-followups", extra={"extra_fields": {
            "error": str(e)
        }})
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "healthy"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=True)
