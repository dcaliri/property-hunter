"""Email digest transport for the notify stage (US4).

Builds a plain-text + HTML digest covering all pending detections and delivers
it over SMTP with exponential-backoff retries. Notification rows are written
per detection for dedupe; a single send covers all rows in the pass.
"""

from __future__ import annotations

import html as html_module
import logging
import time
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib

from property_hunter.config import Settings
from property_hunter.db import Repository
from property_hunter.models import NotificationRecord
from property_hunter.util import utcnow

logger = logging.getLogger("property_hunter.notify")


class SMTPError(Exception):
    """Raised when email delivery fails."""


class Transport:
    """Email transport interface (stubbed in tests)."""

    def send(self, *, sender: str, recipient: str, subject: str, html: str, text: str) -> None:
        raise NotImplementedError


class SMTPTransport(Transport):
    def __init__(self, smtp):
        self.smtp = smtp

    def send(self, *, sender, recipient, subject, html, text):
        if not self.smtp.host:
            raise SMTPError("SMTP not configured (set SMTP_HOST)")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = sender or self.smtp.sender
        msg["To"] = recipient
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        try:
            with smtplib.SMTP(self.smtp.host, self.smtp.port, timeout=30) as server:
                if self.smtp.tls:
                    server.starttls()
                if self.smtp.user:
                    server.login(self.smtp.user, self.smtp.password)
                server.sendmail(msg["From"], [recipient], msg.as_string())
        except SMTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SMTPError(str(exc)) from exc


def _money(cents: int | None) -> str:
    if cents is None:
        return "n/d"
    return f"${cents / 100:,.0f} USD"


def _signal_lines(signals: list[dict]) -> list[str]:
    lines = []
    for sig in signals:
        if not sig["satisfied"]:
            continue
        label = {
            "undervaluation": "Subvaluado vs. valor estimado",
            "yield": "Rendimiento anual esperado",
            "price_drop": "Baja de precio reciente",
        }.get(sig["type"], sig["type"])
        if sig["type"] == "undervaluation" and isinstance(sig.get("expected"), (int, float)):
            lines.append(f"  - {label}: precio {_money(sig.get('observed'))} vs. valor estimado {_money(sig.get('expected'))} (umbral {sig['threshold']:.0%})")
        else:
            lines.append(f"  - {label}: {sig.get('observed')} (umbral {sig['threshold']})")
    return lines


def build_digest(settings: Settings, repo: Repository, detections: list,
                 narrative: str | None = None) -> tuple[str, str, str]:
    """Return (subject, html, text) for the given detections."""
    lines: list[str] = []
    html_parts: list[str] = []
    if narrative:
        lines.append(narrative.strip())
        lines.append("")
        html_parts.append(f"<p>{html_module.escape(narrative.strip())}</p>")

    for det in detections:
        listing = repo.get_listing(det["listing_id"])
        name = listing["street_address"] or f"{listing['barrio']} ({listing['region']})"
        signals = json.loads(det["signals"])
        asking = next((s.get("observed") for s in signals
                       if s["type"] == "undervaluation" and isinstance(s.get("observed"), (int, float))), None)
        lines.append(name)
        lines.append(f"  Precio: {_money(asking)}")
        lines.extend(_signal_lines(signals))
        lines.append(f"  Detalle: {listing['source_url']}")
        lines.append("")
        html_parts.append(
            f"<li><a href='{html_module.escape(listing['source_url'])}'>{html_module.escape(name)}</a><br/>"
            f"Señales: {', '.join(s['type'] for s in signals if s['satisfied'])}</li>"
        )

    subject = f"[property-hunter] {len(detections)} oportunidad(es) detectadas"
    text = "\n".join(lines).strip() or "Sin novedades."
    html = f"<html><body><p>{len(detections)} oportunidad(es) detectadas</p><ul>{''.join(html_parts)}</ul></body></html>"
    return subject, html, text


def run_notify(settings: Settings, repo: Repository, run_id: int | None = None,
               transport: Transport | None = None, llm_transport=None,
               sleep=time.sleep) -> dict:
    """Deliver one digest email covering all pending detections."""
    created = run_id is None
    run_id = run_id or repo.create_run(trigger="notify")
    now = utcnow()
    transport = transport or SMTPTransport(settings.smtp)

    counts = {"sent": 0, "failed": 0, "skipped": 0, "attempts": 0}
    detections = repo.unnotified_detections()
    if not detections:
        counts["skipped"] = 1
        if created:
            repo.finish_run(run_id, "ok", now)
        repo.conn.commit()
        return counts

    narrative = ""
    if settings.llm.enabled:
        from property_hunter.llm.enrich import enrich_descriptions
        from property_hunter.llm.narrative import build_narrative
        enrich_descriptions(settings, repo, transport=llm_transport)
        narrative = build_narrative(settings, repo, detections, transport=llm_transport)
    subject, html, text = build_digest(settings, repo, detections, narrative=narrative)

    notification_ids = []
    for det in detections:
        nid = repo.insert_notification(NotificationRecord(
            detection_id=det["id"], run_id=run_id, channel="email",
            recipient=settings.smtp.recipient, status="pending", created_at=now,
        ))
        notification_ids.append(nid)
    repo.conn.commit()

    max_attempts = max(1, settings.notify.max_attempts)
    error: str | None = None
    for attempt in range(1, max_attempts + 1):
        counts["attempts"] += 1
        try:
            transport.send(sender=settings.smtp.sender, recipient=settings.smtp.recipient,
                           subject=subject, html=html, text=text)
            sent_at = utcnow()
            stored_narrative = narrative or None
            for nid in notification_ids:
                repo.update_notification_status(nid, "sent", attempt, None, sent_at,
                                                digest_narrative=stored_narrative,
                                                llm_enriched=bool(stored_narrative))
            counts["sent"] += len(notification_ids)
            break
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            if attempt < max_attempts:
                sleep(settings.notify.retry_backoff_base_seconds * attempt)
    else:
        for nid in notification_ids:
            repo.update_notification_status(nid, "failed", max_attempts, error)
        counts["failed"] += len(notification_ids)

    if created:
        repo.finish_run(run_id, "ok", utcnow())
    repo.conn.commit()
    logger.info("notify complete", extra={
        "ctx_run_id": run_id, "ctx_sent": counts["sent"], "ctx_failed": counts["failed"],
        "ctx_attempts": counts["attempts"]})
    return counts
