import csv
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from ..models import Assembly, Attendance, Ballot, Proxy
from . import majority


def build_csv(db: Session, assembly: Assembly) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)

    writer.writerow(["Versammlung", assembly.title, assembly.date.strftime("%d.%m.%Y")])
    writer.writerow([])

    writer.writerow(["Anwesenheitsliste"])
    writer.writerow(["Mitglied", "Eingecheckt um"])
    for a in db.query(Attendance).filter(Attendance.assembly_id == assembly.id).all():
        writer.writerow([a.member.name, a.checked_in_at.strftime("%d.%m.%Y %H:%M")])
    writer.writerow([])

    writer.writerow(["Vollmachten (Satzung §12 Abs. 4)"])
    writer.writerow(["Übertragend", "Empfangend", "Weisung", "Status"])
    for p in db.query(Proxy).filter(Proxy.assembly_id == assembly.id).all():
        writer.writerow([p.from_member.name, p.to_member.name, p.instruction or "", p.status.value])
    writer.writerow([])

    for ballot in db.query(Ballot).filter(Ballot.assembly_id == assembly.id).all():
        writer.writerow([f"Wahlgang: {ballot.title}"])
        writer.writerow(["Typ", ballot.kind.value, "Modus", "geheim" if ballot.secret else "offen"])
        if ballot.status.value == "geschlossen":
            result = majority.compute_result(db, ballot)
            for choice, count in result.counts.items():
                writer.writerow([choice, count])
            writer.writerow(["nicht abgestimmt", result.not_voted])
            writer.writerow(["Ergebnis", "angenommen" if result.passed else "abgelehnt"])
        else:
            writer.writerow(["Status", ballot.status.value])
        writer.writerow([])

    return buf.getvalue()


def build_pdf(db: Session, assembly: Assembly) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=20 * mm, bottomMargin=20 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=20, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], spaceBefore=14, spaceAfter=6)
    body = styles["BodyText"]

    story = [
        Paragraph("LA eRacing e.V. &mdash; Protokoll", title_style),
        Paragraph(f"{assembly.title} &middot; {assembly.date.strftime('%d.%m.%Y')}", body),
        Spacer(1, 10 * mm),
    ]

    story.append(Paragraph("Anwesenheitsliste", h2))
    attendance_rows = [["Mitglied", "Eingecheckt um"]]
    for a in db.query(Attendance).filter(Attendance.assembly_id == assembly.id).all():
        attendance_rows.append([a.member.name, a.checked_in_at.strftime("%d.%m.%Y %H:%M")])
    story.append(_table(attendance_rows))

    story.append(Paragraph("Vollmachten (Satzung §12 Abs. 4)", h2))
    proxy_rows = [["Übertragend", "Empfangend", "Weisung", "Status"]]
    for p in db.query(Proxy).filter(Proxy.assembly_id == assembly.id).all():
        proxy_rows.append([p.from_member.name, p.to_member.name, p.instruction or "—", p.status.value])
    if len(proxy_rows) == 1:
        proxy_rows.append(["—", "—", "—", "—"])
    story.append(_table(proxy_rows))

    story.append(Paragraph("Wahlgänge", h2))
    for ballot in db.query(Ballot).filter(Ballot.assembly_id == assembly.id).all():
        story.append(Paragraph(f"<b>{ballot.title}</b>", body))
        mode = "geheim" if ballot.secret else "offen"
        story.append(Paragraph(f"{ballot.kind.value} &middot; {mode}", body))
        if ballot.status.value == "geschlossen":
            result = majority.compute_result(db, ballot)
            rows = [["Option", "Anzahl"]]
            for choice, count in result.counts.items():
                rows.append([choice, str(count)])
            rows.append(["nicht abgestimmt", str(result.not_voted)])
            story.append(_table(rows))
            verdict = "angenommen" if result.passed else "abgelehnt"
            story.append(Paragraph(f"Ergebnis: <b>{verdict}</b>", body))
        else:
            story.append(Paragraph(f"Status: {ballot.status.value}", body))
        story.append(Spacer(1, 6 * mm))

    doc.build(story)
    return buf.getvalue()


def _table(rows) -> Table:
    t = Table(rows, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), colors.black),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return t
