from __future__ import annotations
"""
Quote & Invoice PDF Generator  (Phase 7/11)
- Generates professional PDF quotes/invoices with Zoar Bathroom Rentals branding
- Uses reportlab for PDF (falls back to formatted text if not installed)
- SQLite tracking at ~/.nexus/memory.db
- Outputs to ~/.nexus/quotes/ and ~/.nexus/invoices/
- Trigger: Telegram /quote, CRM pipeline, or direct API call
"""

import sqlite3
import json
import re
from pathlib import Path
from datetime import datetime, timedelta

# ── reportlab (optional) ─────────────────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.colors import HexColor, white, black
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    HAS_REPORTLAB = True
except ImportError:
    HAS_REPORTLAB = False

# ── Paths ─────────────────────────────────────────────────────────────────────
DB_PATH = Path.home() / ".nexus" / "memory.db"
QUOTES_DIR = Path.home() / ".nexus" / "quotes"
INVOICES_DIR = Path.home() / ".nexus" / "invoices"

# ── Business Info ─────────────────────────────────────────────────────────────
BUSINESS = {
    "name": "Zoar Bathroom Rentals",
    "tagline": "Luxury Restroom Trailer Rental",
    "phone": "(424) 235-8979",
    "email": "zoarbathrooms@gmail.com",
    "website": "zoarbathroomrental.com",
    "address": "Los Angeles, CA",
}

# ── Colour palette ────────────────────────────────────────────────────────────
CLR_NAVY = "#1a1a2e"
CLR_GOLD = "#e8b825"
CLR_DARK = "#333333"
CLR_LIGHT = "#f5f5f5"
CLR_WHITE = "#ffffff"
CLR_MID_GRAY = "#888888"

# ── What's Included bullet points ────────────────────────────────────────────
WHATS_INCLUDED = [
    "4 private luxury stalls",
    "Flushing toilets & running water",
    "Climate control (AC/Heat)",
    "LED lighting & chrome fixtures",
    "Bluetooth speakers",
    "Vanity mirrors & premium soap",
    "Fully self-contained (no hookups needed)",
    "Delivery, setup, & pickup included",
]

TERMS = [
    "50% deposit required to confirm booking",
    "Remaining balance due 7 days before event",
    "Flat, level surface required for trailer placement",
    "Free delivery within 30 miles of Los Angeles",
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Database
# ═══════════════════════════════════════════════════════════════════════════════

def init_quote_db():
    """Create quotes and invoices tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        quote_number TEXT UNIQUE NOT NULL,
        lead_id INTEGER,
        client_name TEXT NOT NULL,
        client_email TEXT DEFAULT '',
        client_phone TEXT DEFAULT '',
        event_type TEXT DEFAULT '',
        event_date TEXT DEFAULT '',
        event_location TEXT DEFAULT '',
        guest_count INTEGER DEFAULT 0,
        line_items TEXT DEFAULT '[]',
        subtotal REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        total REAL DEFAULT 0,
        notes TEXT DEFAULT '',
        status TEXT DEFAULT 'draft',
        pdf_path TEXT DEFAULT '',
        expires_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        sent_at TEXT,
        accepted_at TEXT
    );

    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_number TEXT UNIQUE NOT NULL,
        quote_id INTEGER,
        lead_id INTEGER,
        client_name TEXT NOT NULL,
        client_email TEXT DEFAULT '',
        client_phone TEXT DEFAULT '',
        event_type TEXT DEFAULT '',
        event_date TEXT DEFAULT '',
        line_items TEXT DEFAULT '[]',
        subtotal REAL DEFAULT 0,
        discount REAL DEFAULT 0,
        total REAL DEFAULT 0,
        notes TEXT DEFAULT '',
        status TEXT DEFAULT 'unpaid',
        pdf_path TEXT DEFAULT '',
        payment_terms TEXT DEFAULT 'Due upon receipt',
        created_at TEXT DEFAULT (datetime('now')),
        paid_at TEXT,
        due_date TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_quotes_lead ON quotes(lead_id);
    CREATE INDEX IF NOT EXISTS idx_quotes_status ON quotes(status);
    CREATE INDEX IF NOT EXISTS idx_quotes_number ON quotes(quote_number);
    CREATE INDEX IF NOT EXISTS idx_invoices_lead ON invoices(lead_id);
    CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
    CREATE INDEX IF NOT EXISTS idx_invoices_number ON invoices(invoice_number);
    """)
    conn.commit()
    conn.close()
    print("[QuoteGen] Database tables ready")


def _db():
    """Return a connection with row_factory set."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ── Auto-incrementing numbers ─────────────────────────────────────────────────

def _next_quote_number() -> str:
    """Generate next quote number: ZBR-Q-0001, ZBR-Q-0002, etc."""
    conn = _db()
    row = conn.execute("SELECT MAX(id) FROM quotes").fetchone()
    conn.close()
    next_id = (row[0] or 0) + 1
    return f"ZBR-Q-{next_id:04d}"


def _next_invoice_number() -> str:
    """Generate next invoice number: ZBR-I-0001, ZBR-I-0002, etc."""
    conn = _db()
    row = conn.execute("SELECT MAX(id) FROM invoices").fetchone()
    conn.close()
    next_id = (row[0] or 0) + 1
    return f"ZBR-I-{next_id:04d}"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_date(dt_str: str) -> str:
    """Try to format a date string into a human-readable form."""
    if not dt_str:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y",
                "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(dt_str.strip(), fmt).strftime("%B %d, %Y")
        except ValueError:
            continue
    return dt_str  # return as-is if we can't parse


def _fmt_currency(amount: float) -> str:
    """Format a number as $1,100.00"""
    return f"${amount:,.2f}"


# ── Lead lookup helper ────────────────────────────────────────────────────────

def _fetch_lead(lead_id: int) -> dict | None:
    """Fetch lead details from the leads table."""
    if not lead_id:
        return None
    try:
        conn = _db()
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Quote Operations
# ═══════════════════════════════════════════════════════════════════════════════

def create_quote(
    client_name: str,
    event_type: str = "",
    event_date: str = "",
    event_location: str = "",
    guest_count: int = 0,
    price: float = 1100.0,
    client_email: str = "",
    client_phone: str = "",
    lead_id: int = None,
    include_attendant: bool = False,
    attendant_price: float = 200.0,
    include_premium: bool = False,
    premium_price: float = 150.0,
    discount: float = 0.0,
    notes: str = "",
) -> dict:
    """
    Create a quote, save to DB, and generate PDF.
    Returns: {"ok": True, "quote_number": "ZBR-Q-0001", "pdf_path": "...", "total": 1100.0}
    """
    init_quote_db()

    # Auto-fetch lead details if lead_id provided
    if lead_id and not client_name:
        lead = _fetch_lead(lead_id)
        if lead:
            client_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            client_email = client_email or lead.get("email", "")
            client_phone = client_phone or lead.get("phone", "")

    if not client_name:
        return {"ok": False, "error": "client_name is required"}

    # Build line items
    line_items = [
        {"description": "Luxury 4-Stall Restroom Trailer Rental", "amount": price},
    ]
    subtotal = price

    if include_attendant:
        line_items.append({"description": "On-Site Attendant", "amount": attendant_price})
        subtotal += attendant_price

    if include_premium:
        line_items.append({"description": "Premium Package Upgrade", "amount": premium_price})
        subtotal += premium_price

    total = subtotal - discount
    quote_number = _next_quote_number()
    now = _now()
    expires_at = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    # Insert into DB
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        """INSERT INTO quotes
           (quote_number, lead_id, client_name, client_email, client_phone,
            event_type, event_date, event_location, guest_count,
            line_items, subtotal, discount, total, notes, status, expires_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
        (quote_number, lead_id, client_name, client_email, client_phone,
         event_type, event_date, event_location, guest_count,
         json.dumps(line_items), subtotal, discount, total, notes, expires_at, now),
    )
    quote_id = cur.lastrowid
    conn.commit()

    # Build quote_data dict for PDF generation
    quote_data = {
        "id": quote_id,
        "quote_number": quote_number,
        "lead_id": lead_id,
        "client_name": client_name,
        "client_email": client_email,
        "client_phone": client_phone,
        "event_type": event_type,
        "event_date": event_date,
        "event_location": event_location,
        "guest_count": guest_count,
        "line_items": line_items,
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
        "notes": notes,
        "status": "draft",
        "expires_at": expires_at,
        "created_at": now,
    }

    # Generate PDF
    pdf_path = generate_quote_pdf(quote_data)

    # Update DB with pdf_path
    conn.execute("UPDATE quotes SET pdf_path = ? WHERE id = ?", (pdf_path, quote_id))
    conn.commit()
    conn.close()

    print(f"[QuoteGen] Quote {quote_number} created -> {pdf_path}")
    return {
        "ok": True,
        "quote_id": quote_id,
        "quote_number": quote_number,
        "pdf_path": pdf_path,
        "total": total,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF Generation  (reportlab)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_styles():
    """Create custom ParagraphStyles for the quote/invoice PDFs."""
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        "BizName", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=22, leading=26,
        textColor=HexColor(CLR_WHITE), alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "BizTagline", parent=styles["Normal"],
        fontName="Helvetica", fontSize=11, leading=14,
        textColor=HexColor(CLR_GOLD), alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "BizContact", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, leading=12,
        textColor=HexColor("#cccccc"), alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "SectionTitle", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=HexColor(CLR_NAVY), spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "BodyText2", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=13,
        textColor=HexColor(CLR_DARK),
    ))
    styles.add(ParagraphStyle(
        "BodyBold", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=10, leading=13,
        textColor=HexColor(CLR_DARK),
    ))
    styles.add(ParagraphStyle(
        "SmallGray", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8, leading=10,
        textColor=HexColor(CLR_MID_GRAY),
    ))
    styles.add(ParagraphStyle(
        "DocTitle", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=16, leading=20,
        textColor=HexColor(CLR_NAVY), alignment=TA_LEFT,
    ))
    styles.add(ParagraphStyle(
        "TotalLabel", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=12, leading=15,
        textColor=HexColor(CLR_NAVY), alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "TotalValue", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=14, leading=17,
        textColor=HexColor(CLR_NAVY), alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        "CheckMark", parent=styles["Normal"],
        fontName="Helvetica", fontSize=10, leading=14,
        textColor=HexColor(CLR_DARK),
    ))
    styles.add(ParagraphStyle(
        "FooterCTA", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=HexColor(CLR_WHITE), alignment=TA_CENTER,
    ))
    styles.add(ParagraphStyle(
        "FooterSub", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, leading=12,
        textColor=HexColor("#cccccc"), alignment=TA_CENTER,
    ))
    return styles


def generate_quote_pdf(quote_data: dict) -> str:
    """
    Generate a professional PDF quote using reportlab.
    Falls back to text file if reportlab is not installed.
    Returns path to generated file.
    """
    if not HAS_REPORTLAB:
        return _generate_text_quote(quote_data)

    QUOTES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{quote_data['quote_number']}.pdf"
    filepath = str(QUOTES_DIR / filename)

    doc = SimpleDocTemplate(
        filepath,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = _build_styles()
    elements = []
    page_width = letter[0] - 1.2 * inch  # usable width

    # ── Header (navy background) ──────────────────────────────────────────────
    header_data = [[
        Paragraph(BUSINESS["name"], styles["BizName"]),
    ], [
        Paragraph(BUSINESS["tagline"], styles["BizTagline"]),
    ], [
        Paragraph(
            f'{BUSINESS["phone"]}  |  {BUSINESS["email"]}',
            styles["BizContact"],
        ),
    ], [
        Paragraph(BUSINESS["website"], styles["BizContact"]),
    ]]
    header_table = Table(header_data, colWidths=[page_width])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(CLR_NAVY)),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (0, 0), 18),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 14))

    # ── Gold accent bar ───────────────────────────────────────────────────────
    elements.append(HRFlowable(
        width="100%", thickness=3, color=HexColor(CLR_GOLD),
        spaceAfter=10, spaceBefore=0,
    ))

    # ── Quote title + dates ───────────────────────────────────────────────────
    created_fmt = _fmt_date(quote_data.get("created_at", ""))
    expires_fmt = _fmt_date(quote_data.get("expires_at", ""))
    title_data = [
        [
            Paragraph(f'QUOTE  #{quote_data["quote_number"]}', styles["DocTitle"]),
            Paragraph(f"Date: {created_fmt}", styles["BodyText2"]),
        ],
        [
            Paragraph("", styles["BodyText2"]),
            Paragraph(f"Valid Until: {expires_fmt}", styles["BodyText2"]),
        ],
    ]
    title_table = Table(title_data, colWidths=[page_width * 0.55, page_width * 0.45])
    title_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(title_table)
    elements.append(Spacer(1, 12))

    # ── Prepared For ──────────────────────────────────────────────────────────
    elements.append(Paragraph("PREPARED FOR:", styles["SectionTitle"]))
    elements.append(Paragraph(quote_data.get("client_name", ""), styles["BodyBold"]))
    contact_parts = []
    if quote_data.get("client_phone"):
        contact_parts.append(quote_data["client_phone"])
    if quote_data.get("client_email"):
        contact_parts.append(quote_data["client_email"])
    if contact_parts:
        elements.append(Paragraph("  |  ".join(contact_parts), styles["BodyText2"]))
    elements.append(Spacer(1, 10))

    # ── Thin divider ──────────────────────────────────────────────────────────
    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=HexColor("#dddddd"),
        spaceAfter=8, spaceBefore=4,
    ))

    # ── Event Details ─────────────────────────────────────────────────────────
    has_event = any([
        quote_data.get("event_type"),
        quote_data.get("event_date"),
        quote_data.get("event_location"),
        quote_data.get("guest_count"),
    ])
    if has_event:
        elements.append(Paragraph("EVENT DETAILS:", styles["SectionTitle"]))
        if quote_data.get("event_type"):
            elements.append(Paragraph(
                f'Type: {quote_data["event_type"]}', styles["BodyText2"]))
        if quote_data.get("event_date"):
            elements.append(Paragraph(
                f'Date: {_fmt_date(quote_data["event_date"])}', styles["BodyText2"]))
        if quote_data.get("event_location"):
            elements.append(Paragraph(
                f'Location: {quote_data["event_location"]}', styles["BodyText2"]))
        if quote_data.get("guest_count"):
            elements.append(Paragraph(
                f'Estimated Guests: ~{quote_data["guest_count"]}', styles["BodyText2"]))
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(
            width="100%", thickness=0.5, color=HexColor("#dddddd"),
            spaceAfter=8, spaceBefore=4,
        ))

    # ── Line Items Table ──────────────────────────────────────────────────────
    line_items = quote_data.get("line_items", [])
    if isinstance(line_items, str):
        line_items = json.loads(line_items)

    # Header row
    item_rows = [[
        Paragraph("DESCRIPTION", styles["BodyBold"]),
        Paragraph("AMOUNT", ParagraphStyle(
            "AmtHdr", parent=styles["BodyBold"], alignment=TA_RIGHT)),
    ]]

    for item in line_items:
        item_rows.append([
            Paragraph(item["description"], styles["BodyText2"]),
            Paragraph(_fmt_currency(item["amount"]), ParagraphStyle(
                "AmtVal", parent=styles["BodyText2"], alignment=TA_RIGHT)),
        ])

    # Included items (always show)
    included_items = [
        "Delivery & Setup",
        "Pickup & Cleaning",
        "Climate Control (AC/Heat)",
    ]
    for inc in included_items:
        item_rows.append([
            Paragraph(f"    {inc}", styles["SmallGray"]),
            Paragraph("Included", ParagraphStyle(
                "IncVal", parent=styles["SmallGray"], alignment=TA_RIGHT)),
        ])

    # Subtotal, Discount, Total
    item_rows.append([
        Paragraph("Subtotal", ParagraphStyle(
            "SubLbl", parent=styles["BodyText2"], alignment=TA_RIGHT)),
        Paragraph(_fmt_currency(quote_data.get("subtotal", 0)), ParagraphStyle(
            "SubVal", parent=styles["BodyText2"], alignment=TA_RIGHT)),
    ])
    if quote_data.get("discount", 0) > 0:
        item_rows.append([
            Paragraph("Discount", ParagraphStyle(
                "DiscLbl", parent=styles["BodyText2"], alignment=TA_RIGHT)),
            Paragraph(f'-{_fmt_currency(quote_data["discount"])}', ParagraphStyle(
                "DiscVal", parent=styles["BodyText2"], alignment=TA_RIGHT,
                textColor=HexColor("#cc0000"))),
        ])
    item_rows.append([
        Paragraph("TOTAL", styles["TotalLabel"]),
        Paragraph(_fmt_currency(quote_data.get("total", 0)), styles["TotalValue"]),
    ])

    desc_width = page_width * 0.7
    amt_width = page_width * 0.3
    item_table = Table(item_rows, colWidths=[desc_width, amt_width])

    # Compute dynamic styling
    num_line_items = len(line_items)
    num_included = len(included_items)
    header_row = 0
    line_end = num_line_items  # exclusive
    incl_end = line_end + num_included
    total_row = len(item_rows) - 1

    table_cmds = [
        # Header
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(CLR_NAVY)),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(CLR_WHITE)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        # Alternating rows for line items
        ("LINEBELOW", (0, 0), (-1, 0), 1, HexColor(CLR_GOLD)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        # Separator above subtotal
        ("LINEABOVE", (0, incl_end + 1), (-1, incl_end + 1), 0.5, HexColor("#cccccc")),
        # Total row highlight
        ("LINEABOVE", (0, total_row), (-1, total_row), 1.5, HexColor(CLR_NAVY)),
        ("BACKGROUND", (0, total_row), (-1, total_row), HexColor(CLR_LIGHT)),
    ]

    # Alternate row shading for line-item rows
    for i in range(1, line_end + 1):
        if i % 2 == 0:
            table_cmds.append(
                ("BACKGROUND", (0, i), (-1, i), HexColor(CLR_LIGHT)))

    item_table.setStyle(TableStyle(table_cmds))
    elements.append(item_table)
    elements.append(Spacer(1, 14))

    # ── What's Included ───────────────────────────────────────────────────────
    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=HexColor("#dddddd"),
        spaceAfter=8, spaceBefore=4,
    ))
    elements.append(Paragraph("WHAT'S INCLUDED:", styles["SectionTitle"]))

    # Two-column layout for included items
    mid = (len(WHATS_INCLUDED) + 1) // 2
    left_col = WHATS_INCLUDED[:mid]
    right_col = WHATS_INCLUDED[mid:]

    inc_rows = []
    for i in range(max(len(left_col), len(right_col))):
        left_txt = f"  {left_col[i]}" if i < len(left_col) else ""
        right_txt = f"  {right_col[i]}" if i < len(right_col) else ""
        inc_rows.append([
            Paragraph(left_txt, styles["CheckMark"]),
            Paragraph(right_txt, styles["CheckMark"]),
        ])

    inc_table = Table(inc_rows, colWidths=[page_width * 0.5, page_width * 0.5])
    inc_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(inc_table)
    elements.append(Spacer(1, 10))

    # ── Terms & Conditions ────────────────────────────────────────────────────
    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=HexColor("#dddddd"),
        spaceAfter=8, spaceBefore=4,
    ))
    elements.append(Paragraph("TERMS & CONDITIONS:", styles["SectionTitle"]))
    for term in TERMS:
        elements.append(Paragraph(f"  {term}", styles["BodyText2"]))
    elements.append(Spacer(1, 6))

    # ── Notes ─────────────────────────────────────────────────────────────────
    if quote_data.get("notes"):
        elements.append(Spacer(1, 4))
        elements.append(Paragraph("NOTES:", styles["SectionTitle"]))
        elements.append(Paragraph(quote_data["notes"], styles["BodyText2"]))
        elements.append(Spacer(1, 6))

    # ── Footer CTA (navy background) ─────────────────────────────────────────
    elements.append(Spacer(1, 10))
    footer_data = [
        [Paragraph(
            "To confirm your booking, reply CONFIRM or call us",
            styles["FooterCTA"],
        )],
        [Paragraph(
            f'{BUSINESS["phone"]}  |  {BUSINESS["email"]}  |  {BUSINESS["website"]}',
            styles["FooterSub"],
        )],
    ]
    footer_table = Table(footer_data, colWidths=[page_width])
    footer_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(CLR_NAVY)),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 4),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(footer_table)

    # ── Build PDF ─────────────────────────────────────────────────────────────
    doc.build(elements)
    return filepath


def _generate_text_quote(quote_data: dict) -> str:
    """Fallback: generate a formatted text file when reportlab is unavailable."""
    print("[QuoteGen] WARNING: reportlab not installed. Generating text quote.")
    print("[QuoteGen] Install with: pip install reportlab")

    QUOTES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{quote_data['quote_number']}.txt"
    filepath = str(QUOTES_DIR / filename)

    line_items = quote_data.get("line_items", [])
    if isinstance(line_items, str):
        line_items = json.loads(line_items)

    sep = "=" * 50
    thin = "-" * 50

    lines = [
        sep,
        f"  {BUSINESS['name'].upper()}",
        f"  {BUSINESS['tagline']}",
        f"  {BUSINESS['phone']}  |  {BUSINESS['email']}",
        f"  {BUSINESS['website']}",
        sep,
        "",
        f"  QUOTE #{quote_data['quote_number']}",
        f"  Date: {_fmt_date(quote_data.get('created_at', ''))}",
        f"  Valid Until: {_fmt_date(quote_data.get('expires_at', ''))}",
        "",
        thin,
        f"  PREPARED FOR: {quote_data.get('client_name', '')}",
    ]

    contact_parts = []
    if quote_data.get("client_phone"):
        contact_parts.append(quote_data["client_phone"])
    if quote_data.get("client_email"):
        contact_parts.append(quote_data["client_email"])
    if contact_parts:
        lines.append(f"  {' | '.join(contact_parts)}")

    lines.append("")

    if quote_data.get("event_type") or quote_data.get("event_date"):
        lines.append("  EVENT DETAILS:")
        if quote_data.get("event_type"):
            lines.append(f"    Type: {quote_data['event_type']}")
        if quote_data.get("event_date"):
            lines.append(f"    Date: {_fmt_date(quote_data['event_date'])}")
        if quote_data.get("event_location"):
            lines.append(f"    Location: {quote_data['event_location']}")
        if quote_data.get("guest_count"):
            lines.append(f"    Guests: ~{quote_data['guest_count']}")
        lines.append("")

    lines.append(thin)
    lines.append(f"  {'DESCRIPTION':<35} {'AMOUNT':>12}")
    lines.append(f"  {'-'*35} {'-'*12}")

    for item in line_items:
        lines.append(f"  {item['description']:<35} {_fmt_currency(item['amount']):>12}")

    lines.append(f"  {'Delivery & Setup':<35} {'Included':>12}")
    lines.append(f"  {'Pickup & Cleaning':<35} {'Included':>12}")
    lines.append(f"  {'Climate Control (AC/Heat)':<35} {'Included':>12}")
    lines.append(f"  {'-'*35} {'-'*12}")
    lines.append(f"  {'Subtotal':<35} {_fmt_currency(quote_data.get('subtotal', 0)):>12}")

    if quote_data.get("discount", 0) > 0:
        lines.append(
            f"  {'Discount':<35} {'-' + _fmt_currency(quote_data['discount']):>12}")

    lines.append(f"  {'TOTAL':<35} {_fmt_currency(quote_data.get('total', 0)):>12}")
    lines.append(thin)
    lines.append("")

    lines.append("  WHAT'S INCLUDED:")
    for item in WHATS_INCLUDED:
        lines.append(f"    [x] {item}")
    lines.append("")

    lines.append("  TERMS & CONDITIONS:")
    for term in TERMS:
        lines.append(f"    * {term}")
    lines.append("")

    if quote_data.get("notes"):
        lines.append(f"  NOTES: {quote_data['notes']}")
        lines.append("")

    lines.append(sep)
    lines.append(f"  To confirm: Reply CONFIRM or call {BUSINESS['phone']}")
    lines.append(sep)

    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    return filepath


# ═══════════════════════════════════════════════════════════════════════════════
#  Quote Queries
# ═══════════════════════════════════════════════════════════════════════════════

def get_quote(quote_id: int = None, quote_number: str = None) -> dict | None:
    """Fetch a single quote by id or number."""
    init_quote_db()
    conn = _db()
    if quote_id:
        row = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    elif quote_number:
        row = conn.execute(
            "SELECT * FROM quotes WHERE quote_number = ?", (quote_number,)
        ).fetchone()
    else:
        conn.close()
        return None
    conn.close()
    if not row:
        return None
    d = dict(row)
    # Parse line_items JSON
    try:
        d["line_items"] = json.loads(d.get("line_items", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["line_items"] = []
    return d


def list_quotes(status: str = None, lead_id: int = None) -> list[dict]:
    """List quotes, optionally filtered by status or lead_id."""
    init_quote_db()
    conn = _db()
    query = "SELECT * FROM quotes WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if lead_id:
        query += " AND lead_id = ?"
        params.append(lead_id)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        try:
            d["line_items"] = json.loads(d.get("line_items", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["line_items"] = []
        results.append(d)
    return results


def update_quote_status(quote_id: int, status: str) -> dict:
    """Update a quote's status. Valid: draft, sent, accepted, declined, expired."""
    valid = {"draft", "sent", "accepted", "declined", "expired"}
    if status not in valid:
        return {"ok": False, "error": f"Invalid status. Must be one of: {valid}"}

    init_quote_db()
    conn = sqlite3.connect(str(DB_PATH))
    now = _now()

    extra = ""
    params = [status]
    if status == "sent":
        extra = ", sent_at = ?"
        params.append(now)
    elif status == "accepted":
        extra = ", accepted_at = ?"
        params.append(now)

    params.append(quote_id)
    conn.execute(f"UPDATE quotes SET status = ?{extra} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"ok": True, "quote_id": quote_id, "status": status}


def get_expiring_quotes() -> list[dict]:
    """Get quotes expiring in the next 24 hours that are still draft or sent."""
    init_quote_db()
    conn = _db()
    now = _now()
    tomorrow = (datetime.utcnow() + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT * FROM quotes WHERE status IN ('draft', 'sent') "
        "AND expires_at BETWEEN ? AND ? ORDER BY expires_at ASC",
        (now, tomorrow),
    ).fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        try:
            d["line_items"] = json.loads(d.get("line_items", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["line_items"] = []
        results.append(d)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Invoice Operations
# ═══════════════════════════════════════════════════════════════════════════════

def create_invoice(
    quote_id: int = None,
    client_name: str = "",
    client_email: str = "",
    client_phone: str = "",
    event_type: str = "",
    event_date: str = "",
    line_items: list = None,
    total: float = 0,
    discount: float = 0,
    lead_id: int = None,
    payment_terms: str = "Due upon receipt",
    notes: str = "",
) -> dict:
    """
    Create an invoice (optionally from a quote). Generate PDF.
    Returns: {"ok": True, "invoice_number": "ZBR-I-0001", "pdf_path": "...", "total": ...}
    """
    init_quote_db()

    # If creating from a quote, pull all data from the quote
    if quote_id:
        quote = get_quote(quote_id=quote_id)
        if not quote:
            return {"ok": False, "error": f"Quote {quote_id} not found"}
        client_name = client_name or quote.get("client_name", "")
        client_email = client_email or quote.get("client_email", "")
        client_phone = client_phone or quote.get("client_phone", "")
        event_type = event_type or quote.get("event_type", "")
        event_date = event_date or quote.get("event_date", "")
        lead_id = lead_id or quote.get("lead_id")
        if line_items is None:
            line_items = quote.get("line_items", [])
        if total == 0:
            total = quote.get("total", 0)
        if discount == 0:
            discount = quote.get("discount", 0)

    # Auto-fetch lead details if lead_id provided and no client_name
    if lead_id and not client_name:
        lead = _fetch_lead(lead_id)
        if lead:
            client_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
            client_email = client_email or lead.get("email", "")
            client_phone = client_phone or lead.get("phone", "")

    if not client_name:
        return {"ok": False, "error": "client_name is required"}

    if line_items is None:
        line_items = []

    # Compute subtotal from line_items if not given
    subtotal = sum(item.get("amount", 0) for item in line_items)
    if total == 0:
        total = subtotal - discount

    invoice_number = _next_invoice_number()
    now = _now()
    due_date = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.execute(
        """INSERT INTO invoices
           (invoice_number, quote_id, lead_id, client_name, client_email, client_phone,
            event_type, event_date, line_items, subtotal, discount, total,
            notes, status, payment_terms, created_at, due_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unpaid', ?, ?, ?)""",
        (invoice_number, quote_id, lead_id, client_name, client_email, client_phone,
         event_type, event_date, json.dumps(line_items), subtotal, discount, total,
         notes, payment_terms, now, due_date),
    )
    invoice_id = cur.lastrowid
    conn.commit()

    invoice_data = {
        "id": invoice_id,
        "invoice_number": invoice_number,
        "quote_id": quote_id,
        "lead_id": lead_id,
        "client_name": client_name,
        "client_email": client_email,
        "client_phone": client_phone,
        "event_type": event_type,
        "event_date": event_date,
        "line_items": line_items,
        "subtotal": subtotal,
        "discount": discount,
        "total": total,
        "notes": notes,
        "status": "unpaid",
        "payment_terms": payment_terms,
        "created_at": now,
        "due_date": due_date,
    }

    pdf_path = generate_invoice_pdf(invoice_data)

    conn.execute("UPDATE invoices SET pdf_path = ? WHERE id = ?", (pdf_path, invoice_id))
    conn.commit()
    conn.close()

    # If created from a quote, mark the quote as accepted
    if quote_id:
        update_quote_status(quote_id, "accepted")

    print(f"[QuoteGen] Invoice {invoice_number} created -> {pdf_path}")
    return {
        "ok": True,
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "pdf_path": pdf_path,
        "total": total,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Invoice PDF Generation
# ═══════════════════════════════════════════════════════════════════════════════

def generate_invoice_pdf(invoice_data: dict) -> str:
    """Generate an invoice PDF. Similar layout to quote but with INVOICE header."""
    if not HAS_REPORTLAB:
        return _generate_text_invoice(invoice_data)

    INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{invoice_data['invoice_number']}.pdf"
    filepath = str(INVOICES_DIR / filename)

    doc = SimpleDocTemplate(
        filepath,
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = _build_styles()
    elements = []
    page_width = letter[0] - 1.2 * inch

    # ── Header ────────────────────────────────────────────────────────────────
    header_data = [[
        Paragraph(BUSINESS["name"], styles["BizName"]),
    ], [
        Paragraph(BUSINESS["tagline"], styles["BizTagline"]),
    ], [
        Paragraph(
            f'{BUSINESS["phone"]}  |  {BUSINESS["email"]}',
            styles["BizContact"],
        ),
    ], [
        Paragraph(BUSINESS["website"], styles["BizContact"]),
    ]]
    header_table = Table(header_data, colWidths=[page_width])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(CLR_NAVY)),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (0, 0), 18),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 14))

    # Gold accent bar
    elements.append(HRFlowable(
        width="100%", thickness=3, color=HexColor(CLR_GOLD),
        spaceAfter=10, spaceBefore=0,
    ))

    # ── Invoice title + dates ─────────────────────────────────────────────────
    created_fmt = _fmt_date(invoice_data.get("created_at", ""))
    due_fmt = _fmt_date(invoice_data.get("due_date", ""))
    title_data = [
        [
            Paragraph(f'INVOICE  #{invoice_data["invoice_number"]}', styles["DocTitle"]),
            Paragraph(f"Date: {created_fmt}", styles["BodyText2"]),
        ],
        [
            Paragraph("", styles["BodyText2"]),
            Paragraph(f"Due: {due_fmt}", styles["BodyText2"]),
        ],
    ]
    if invoice_data.get("payment_terms"):
        title_data.append([
            Paragraph("", styles["BodyText2"]),
            Paragraph(
                f'Terms: {invoice_data["payment_terms"]}',
                styles["SmallGray"],
            ),
        ])

    title_table = Table(title_data, colWidths=[page_width * 0.55, page_width * 0.45])
    title_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(title_table)
    elements.append(Spacer(1, 12))

    # ── Bill To ───────────────────────────────────────────────────────────────
    elements.append(Paragraph("BILL TO:", styles["SectionTitle"]))
    elements.append(Paragraph(invoice_data.get("client_name", ""), styles["BodyBold"]))
    contact_parts = []
    if invoice_data.get("client_phone"):
        contact_parts.append(invoice_data["client_phone"])
    if invoice_data.get("client_email"):
        contact_parts.append(invoice_data["client_email"])
    if contact_parts:
        elements.append(Paragraph("  |  ".join(contact_parts), styles["BodyText2"]))
    elements.append(Spacer(1, 10))

    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=HexColor("#dddddd"),
        spaceAfter=8, spaceBefore=4,
    ))

    # ── Event Details ─────────────────────────────────────────────────────────
    has_event = invoice_data.get("event_type") or invoice_data.get("event_date")
    if has_event:
        elements.append(Paragraph("EVENT:", styles["SectionTitle"]))
        if invoice_data.get("event_type"):
            elements.append(Paragraph(
                f'Type: {invoice_data["event_type"]}', styles["BodyText2"]))
        if invoice_data.get("event_date"):
            elements.append(Paragraph(
                f'Date: {_fmt_date(invoice_data["event_date"])}', styles["BodyText2"]))
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(
            width="100%", thickness=0.5, color=HexColor("#dddddd"),
            spaceAfter=8, spaceBefore=4,
        ))

    # ── Line Items ────────────────────────────────────────────────────────────
    line_items = invoice_data.get("line_items", [])
    if isinstance(line_items, str):
        line_items = json.loads(line_items)

    item_rows = [[
        Paragraph("DESCRIPTION", styles["BodyBold"]),
        Paragraph("AMOUNT", ParagraphStyle(
            "IAmtHdr", parent=styles["BodyBold"], alignment=TA_RIGHT)),
    ]]

    for item in line_items:
        item_rows.append([
            Paragraph(item["description"], styles["BodyText2"]),
            Paragraph(_fmt_currency(item["amount"]), ParagraphStyle(
                "IAmtVal", parent=styles["BodyText2"], alignment=TA_RIGHT)),
        ])

    # Subtotal / Discount / Total
    item_rows.append([
        Paragraph("Subtotal", ParagraphStyle(
            "ISubLbl", parent=styles["BodyText2"], alignment=TA_RIGHT)),
        Paragraph(_fmt_currency(invoice_data.get("subtotal", 0)), ParagraphStyle(
            "ISubVal", parent=styles["BodyText2"], alignment=TA_RIGHT)),
    ])
    if invoice_data.get("discount", 0) > 0:
        item_rows.append([
            Paragraph("Discount", ParagraphStyle(
                "IDiscLbl", parent=styles["BodyText2"], alignment=TA_RIGHT)),
            Paragraph(f'-{_fmt_currency(invoice_data["discount"])}', ParagraphStyle(
                "IDiscVal", parent=styles["BodyText2"], alignment=TA_RIGHT,
                textColor=HexColor("#cc0000"))),
        ])
    item_rows.append([
        Paragraph("AMOUNT DUE", styles["TotalLabel"]),
        Paragraph(_fmt_currency(invoice_data.get("total", 0)), styles["TotalValue"]),
    ])

    desc_width = page_width * 0.7
    amt_width = page_width * 0.3
    item_table = Table(item_rows, colWidths=[desc_width, amt_width])

    num_line_items = len(line_items)
    total_row = len(item_rows) - 1

    table_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(CLR_NAVY)),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(CLR_WHITE)),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 1, HexColor(CLR_GOLD)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEABOVE", (0, num_line_items + 1), (-1, num_line_items + 1),
         0.5, HexColor("#cccccc")),
        ("LINEABOVE", (0, total_row), (-1, total_row), 1.5, HexColor(CLR_NAVY)),
        ("BACKGROUND", (0, total_row), (-1, total_row), HexColor(CLR_LIGHT)),
    ]
    for i in range(1, num_line_items + 1):
        if i % 2 == 0:
            table_cmds.append(
                ("BACKGROUND", (0, i), (-1, i), HexColor(CLR_LIGHT)))

    item_table.setStyle(TableStyle(table_cmds))
    elements.append(item_table)
    elements.append(Spacer(1, 14))

    # ── Payment Info ──────────────────────────────────────────────────────────
    elements.append(HRFlowable(
        width="100%", thickness=0.5, color=HexColor("#dddddd"),
        spaceAfter=8, spaceBefore=4,
    ))
    elements.append(Paragraph("PAYMENT INFORMATION:", styles["SectionTitle"]))
    elements.append(Paragraph(
        f'Terms: {invoice_data.get("payment_terms", "Due upon receipt")}',
        styles["BodyText2"],
    ))
    elements.append(Paragraph(
        "Accepted methods: Zelle, Venmo, check, or cash",
        styles["BodyText2"],
    ))
    elements.append(Spacer(1, 6))

    # ── Notes ─────────────────────────────────────────────────────────────────
    if invoice_data.get("notes"):
        elements.append(Paragraph("NOTES:", styles["SectionTitle"]))
        elements.append(Paragraph(invoice_data["notes"], styles["BodyText2"]))
        elements.append(Spacer(1, 6))

    # ── Footer ────────────────────────────────────────────────────────────────
    elements.append(Spacer(1, 10))
    footer_data = [
        [Paragraph("Thank you for your business!", styles["FooterCTA"])],
        [Paragraph(
            f'{BUSINESS["phone"]}  |  {BUSINESS["email"]}  |  {BUSINESS["website"]}',
            styles["FooterSub"],
        )],
    ]
    footer_table = Table(footer_data, colWidths=[page_width])
    footer_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(CLR_NAVY)),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 14),
        ("TOPPADDING", (0, 1), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -2), 4),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
    ]))
    elements.append(footer_table)

    doc.build(elements)
    return filepath


def _generate_text_invoice(invoice_data: dict) -> str:
    """Fallback text invoice when reportlab is not installed."""
    print("[QuoteGen] WARNING: reportlab not installed. Generating text invoice.")
    print("[QuoteGen] Install with: pip install reportlab")

    INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{invoice_data['invoice_number']}.txt"
    filepath = str(INVOICES_DIR / filename)

    line_items = invoice_data.get("line_items", [])
    if isinstance(line_items, str):
        line_items = json.loads(line_items)

    sep = "=" * 50
    thin = "-" * 50

    lines = [
        sep,
        f"  {BUSINESS['name'].upper()}",
        f"  {BUSINESS['tagline']}",
        f"  {BUSINESS['phone']}  |  {BUSINESS['email']}",
        f"  {BUSINESS['website']}",
        sep,
        "",
        f"  INVOICE #{invoice_data['invoice_number']}",
        f"  Date: {_fmt_date(invoice_data.get('created_at', ''))}",
        f"  Due: {_fmt_date(invoice_data.get('due_date', ''))}",
        f"  Terms: {invoice_data.get('payment_terms', 'Due upon receipt')}",
        "",
        thin,
        f"  BILL TO: {invoice_data.get('client_name', '')}",
    ]

    contact_parts = []
    if invoice_data.get("client_phone"):
        contact_parts.append(invoice_data["client_phone"])
    if invoice_data.get("client_email"):
        contact_parts.append(invoice_data["client_email"])
    if contact_parts:
        lines.append(f"  {' | '.join(contact_parts)}")

    lines.append("")

    if invoice_data.get("event_type") or invoice_data.get("event_date"):
        lines.append("  EVENT:")
        if invoice_data.get("event_type"):
            lines.append(f"    Type: {invoice_data['event_type']}")
        if invoice_data.get("event_date"):
            lines.append(f"    Date: {_fmt_date(invoice_data['event_date'])}")
        lines.append("")

    lines.append(thin)
    lines.append(f"  {'DESCRIPTION':<35} {'AMOUNT':>12}")
    lines.append(f"  {'-'*35} {'-'*12}")

    for item in line_items:
        lines.append(f"  {item['description']:<35} {_fmt_currency(item['amount']):>12}")

    lines.append(f"  {'-'*35} {'-'*12}")
    lines.append(f"  {'Subtotal':<35} {_fmt_currency(invoice_data.get('subtotal', 0)):>12}")

    if invoice_data.get("discount", 0) > 0:
        lines.append(
            f"  {'Discount':<35} {'-' + _fmt_currency(invoice_data['discount']):>12}")

    lines.append(f"  {'AMOUNT DUE':<35} {_fmt_currency(invoice_data.get('total', 0)):>12}")
    lines.append(thin)
    lines.append("")

    lines.append("  PAYMENT INFORMATION:")
    lines.append(f"    Terms: {invoice_data.get('payment_terms', 'Due upon receipt')}")
    lines.append("    Accepted: Zelle, Venmo, check, or cash")
    lines.append("")

    if invoice_data.get("notes"):
        lines.append(f"  NOTES: {invoice_data['notes']}")
        lines.append("")

    lines.append(sep)
    lines.append("  Thank you for your business!")
    lines.append(f"  {BUSINESS['phone']}  |  {BUSINESS['email']}")
    lines.append(sep)

    with open(filepath, "w") as f:
        f.write("\n".join(lines))

    return filepath


# ═══════════════════════════════════════════════════════════════════════════════
#  Invoice Queries
# ═══════════════════════════════════════════════════════════════════════════════

def get_invoice(invoice_id: int = None, invoice_number: str = None) -> dict | None:
    """Fetch a single invoice by id or number."""
    init_quote_db()
    conn = _db()
    if invoice_id:
        row = conn.execute(
            "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()
    elif invoice_number:
        row = conn.execute(
            "SELECT * FROM invoices WHERE invoice_number = ?", (invoice_number,)
        ).fetchone()
    else:
        conn.close()
        return None
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["line_items"] = json.loads(d.get("line_items", "[]"))
    except (json.JSONDecodeError, TypeError):
        d["line_items"] = []
    return d


def list_invoices(status: str = None) -> list[dict]:
    """List invoices, optionally filtered by status."""
    init_quote_db()
    conn = _db()
    if status:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM invoices ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        try:
            d["line_items"] = json.loads(d.get("line_items", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["line_items"] = []
        results.append(d)
    return results


def mark_invoice_paid(invoice_id: int) -> dict:
    """Mark an invoice as paid."""
    init_quote_db()
    now = _now()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        "UPDATE invoices SET status = 'paid', paid_at = ? WHERE id = ?",
        (now, invoice_id),
    )
    conn.commit()
    conn.close()
    print(f"[QuoteGen] Invoice {invoice_id} marked as paid")
    return {"ok": True, "invoice_id": invoice_id, "status": "paid", "paid_at": now}


def get_overdue_invoices() -> list[dict]:
    """Get invoices that are unpaid and past their due_date."""
    init_quote_db()
    conn = _db()
    now = _now()
    rows = conn.execute(
        "SELECT * FROM invoices WHERE status = 'unpaid' AND due_date < ? "
        "ORDER BY due_date ASC",
        (now,),
    ).fetchall()
    conn.close()
    results = []
    for row in rows:
        d = dict(row)
        try:
            d["line_items"] = json.loads(d.get("line_items", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["line_items"] = []
        results.append(d)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  Quote Stats
# ═══════════════════════════════════════════════════════════════════════════════

def get_quote_stats() -> dict:
    """
    Return aggregate quote statistics:
    total_quotes, accepted, pending, declined, expired,
    total_revenue (sum of accepted quote totals).
    """
    init_quote_db()
    conn = _db()

    total = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
    accepted = conn.execute(
        "SELECT COUNT(*) FROM quotes WHERE status = 'accepted'"
    ).fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM quotes WHERE status IN ('draft', 'sent')"
    ).fetchone()[0]
    declined = conn.execute(
        "SELECT COUNT(*) FROM quotes WHERE status = 'declined'"
    ).fetchone()[0]
    expired = conn.execute(
        "SELECT COUNT(*) FROM quotes WHERE status = 'expired'"
    ).fetchone()[0]

    revenue_row = conn.execute(
        "SELECT COALESCE(SUM(total), 0) FROM quotes WHERE status = 'accepted'"
    ).fetchone()
    total_revenue = revenue_row[0] if revenue_row else 0

    # Invoice stats
    invoices_total = conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]
    invoices_paid = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE status = 'paid'"
    ).fetchone()[0]
    invoices_unpaid = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE status = 'unpaid'"
    ).fetchone()[0]
    invoices_overdue = conn.execute(
        "SELECT COUNT(*) FROM invoices WHERE status = 'unpaid' AND due_date < ?",
        (_now(),),
    ).fetchone()[0]
    collected_row = conn.execute(
        "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE status = 'paid'"
    ).fetchone()
    total_collected = collected_row[0] if collected_row else 0

    conn.close()

    return {
        "quotes": {
            "total": total,
            "accepted": accepted,
            "pending": pending,
            "declined": declined,
            "expired": expired,
            "total_revenue": total_revenue,
        },
        "invoices": {
            "total": invoices_total,
            "paid": invoices_paid,
            "unpaid": invoices_unpaid,
            "overdue": invoices_overdue,
            "total_collected": total_collected,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Telegram Command Parser
# ═══════════════════════════════════════════════════════════════════════════════

def parse_quote_command(text: str, lead_data: dict = None) -> dict:
    """
    Parse /quote variations from Telegram:
      /quote 15 june15 thousand-oaks 1100
      /quote lead_id=15
      /quote Sarah Johnson wedding june15 thousand-oaks 1100 guests=150
      /quote client_name="Sarah Johnson" price=1200

    Returns dict with parsed fields suitable for create_quote().
    """
    if not text:
        return {}

    # Strip the /quote command prefix
    text = text.strip()
    if text.lower().startswith("/quote"):
        text = text[6:].strip()
    if text.lower().startswith("/invoice"):
        text = text[8:].strip()

    result = {}

    # Extract key=value pairs first
    kv_pattern = r'(\w+)\s*=\s*(?:"([^"]+)"|(\S+))'
    for match in re.finditer(kv_pattern, text):
        key = match.group(1).lower()
        value = match.group(2) or match.group(3)

        if key == "lead_id":
            result["lead_id"] = int(value)
        elif key == "price":
            result["price"] = float(value)
        elif key in ("client_name", "name", "client"):
            result["client_name"] = value
        elif key in ("event_type", "type", "event"):
            result["event_type"] = value
        elif key in ("event_date", "date"):
            result["event_date"] = value
        elif key in ("event_location", "location", "loc"):
            result["event_location"] = value.replace("-", " ").title()
        elif key in ("guest_count", "guests"):
            result["guest_count"] = int(value)
        elif key in ("client_email", "email"):
            result["client_email"] = value
        elif key in ("client_phone", "phone"):
            result["client_phone"] = value
        elif key == "attendant":
            result["include_attendant"] = value.lower() in ("yes", "true", "1")
        elif key == "premium":
            result["include_premium"] = value.lower() in ("yes", "true", "1")
        elif key == "discount":
            result["discount"] = float(value)
        elif key == "notes":
            result["notes"] = value

    # Remove kv pairs from text to parse remaining positional args
    remaining = re.sub(kv_pattern, "", text).strip()

    if remaining and "lead_id" not in result and "client_name" not in result:
        # Try positional parsing: /quote [lead_id] [date] [location] [price]
        tokens = remaining.split()

        for token in tokens:
            # Check if it's a lead_id (pure number, typically small)
            if re.match(r"^\d{1,5}$", token) and "lead_id" not in result and "price" not in result:
                # Could be lead_id or price — if < 100 assume lead_id, else price
                val = int(token)
                if val < 100 and "lead_id" not in result:
                    result["lead_id"] = val
                else:
                    result["price"] = float(val)
                continue

            # Check if it's a price (number, possibly with decimal or comma)
            if re.match(r"^\$?[\d,]+\.?\d*$", token):
                result["price"] = float(token.replace("$", "").replace(",", ""))
                continue

            # Check if it looks like a date
            date_patterns = [
                (r"^(\w+)(\d{1,2})$", None),  # june15
                (r"^(\d{1,2})/(\d{1,2})$", None),  # 6/15
                (r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", None),  # 6/15/2026
            ]
            is_date = False
            for pat, _ in date_patterns:
                m = re.match(pat, token, re.IGNORECASE)
                if m:
                    result["event_date"] = token
                    is_date = True
                    break
            if is_date:
                continue

            # Otherwise treat as location or event type
            if "event_location" not in result:
                result["event_location"] = token.replace("-", " ").title()
            elif "event_type" not in result:
                result["event_type"] = token.title()

    # If we have lead_id but no client name, fetch from DB
    if result.get("lead_id") and "client_name" not in result:
        if lead_data:
            result["client_name"] = (
                f"{lead_data.get('first_name', '')} {lead_data.get('last_name', '')}".strip()
            )
            result.setdefault("client_email", lead_data.get("email", ""))
            result.setdefault("client_phone", lead_data.get("phone", ""))
        else:
            lead = _fetch_lead(result["lead_id"])
            if lead:
                result["client_name"] = (
                    f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip()
                )
                result.setdefault("client_email", lead.get("email", ""))
                result.setdefault("client_phone", lead.get("phone", ""))

    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Formatted text output for Telegram (no PDF needed)
# ═══════════════════════════════════════════════════════════════════════════════

def format_quote_for_telegram(quote_data: dict) -> str:
    """Format a quote summary for Telegram message."""
    line_items = quote_data.get("line_items", [])
    if isinstance(line_items, str):
        try:
            line_items = json.loads(line_items)
        except (json.JSONDecodeError, TypeError):
            line_items = []

    lines = [
        f"QUOTE #{quote_data.get('quote_number', 'N/A')}",
        f"Client: {quote_data.get('client_name', 'N/A')}",
    ]

    if quote_data.get("event_type"):
        lines.append(f"Event: {quote_data['event_type']}")
    if quote_data.get("event_date"):
        lines.append(f"Date: {_fmt_date(quote_data['event_date'])}")
    if quote_data.get("event_location"):
        lines.append(f"Location: {quote_data['event_location']}")

    lines.append("")
    for item in line_items:
        lines.append(f"  {item['description']}: {_fmt_currency(item['amount'])}")

    if quote_data.get("discount", 0) > 0:
        lines.append(f"  Discount: -{_fmt_currency(quote_data['discount'])}")

    lines.append(f"\nTOTAL: {_fmt_currency(quote_data.get('total', 0))}")
    lines.append(f"Valid until: {_fmt_date(quote_data.get('expires_at', ''))}")
    lines.append(f"Status: {quote_data.get('status', 'draft')}")

    if quote_data.get("pdf_path"):
        lines.append(f"\nPDF: {quote_data['pdf_path']}")

    return "\n".join(lines)


def format_invoice_for_telegram(invoice_data: dict) -> str:
    """Format an invoice summary for Telegram message."""
    line_items = invoice_data.get("line_items", [])
    if isinstance(line_items, str):
        try:
            line_items = json.loads(line_items)
        except (json.JSONDecodeError, TypeError):
            line_items = []

    lines = [
        f"INVOICE #{invoice_data.get('invoice_number', 'N/A')}",
        f"Client: {invoice_data.get('client_name', 'N/A')}",
    ]

    if invoice_data.get("event_type"):
        lines.append(f"Event: {invoice_data['event_type']}")
    if invoice_data.get("event_date"):
        lines.append(f"Date: {_fmt_date(invoice_data['event_date'])}")

    lines.append("")
    for item in line_items:
        lines.append(f"  {item['description']}: {_fmt_currency(item['amount'])}")

    if invoice_data.get("discount", 0) > 0:
        lines.append(f"  Discount: -{_fmt_currency(invoice_data['discount'])}")

    lines.append(f"\nAMOUNT DUE: {_fmt_currency(invoice_data.get('total', 0))}")
    lines.append(f"Due: {_fmt_date(invoice_data.get('due_date', ''))}")
    lines.append(f"Terms: {invoice_data.get('payment_terms', 'Due upon receipt')}")
    lines.append(f"Status: {invoice_data.get('status', 'unpaid')}")

    if invoice_data.get("pdf_path"):
        lines.append(f"\nPDF: {invoice_data['pdf_path']}")

    return "\n".join(lines)
