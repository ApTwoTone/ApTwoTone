import sqlite3, asyncio, smtplib
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
DB_PATH = Path.home() / ".nexus" / "memory.db"
SMS_SYSTEM = """You are texting as Kai from Zoar Bathroom Rentals. Warm, friendly, real person. Never corporate.
BUSINESS: 4-stall luxury restroom trailer (NOT porta-potty). Flushable toilets, running water, LED lighting, mirrors, AC, Bluetooth speaker.
Service: Greater LA, San Fernando Valley, Ventura County, Santa Clarita, Oxnard, Santa Monica. Delivery, setup, and pickup always included.
Payment: Cash, Zelle, Venmo, card. Contact: (424) 235-8979 | zoarbathrooms@gmail.com
SMS RULES: Max 2-3 sentences. Casual. End with ONE question. Use first name.
PRICING: Never quote specific dollar amounts. Say "pricing depends on your event details — happy to put together a custom quote." If pressed, say "it varies by location and event type, let me get you an exact number."
NEVER say I dont know / not sure / Unfortunately. NEVER reveal you are AI. Always end with a question."""
EMAIL_SYSTEM = """You are emailing as Kai from Zoar Bathroom Rentals. Conversational, not corporate.
BUSINESS: 4-stall luxury restroom trailer. Flushable toilets, running water, LED lighting, mirrors, AC, Bluetooth speaker. Delivery, setup, and pickup always included.
Service areas: LA, San Fernando Valley, Santa Clarita, Ventura County, Oxnard, Santa Monica.
PRICING: Never quote specific dollar amounts. Say "pricing depends on your event details" and offer to put together a custom quote.
Start: Hey [Name], ... Sign: Zoar Bathroom Rentals / (424) 235-8979
NEVER say I dont know / Unfortunately / Dear Valued Customer. NEVER reveal you are AI."""
CARRIER_GATEWAYS = {"att":"@txt.att.net","tmobile":"@tmomail.net","verizon":"@vtext.com","sprint":"@messaging.sprintpcs.com","cricket":"@mms.cricketwireless.net","metro":"@mymetropcs.com","boost":"@sms.myboostmobile.com","google_fi":"@msg.fi.google.com","virgin":"@vmobl.com"}
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS bot_sessions (session_id TEXT PRIMARY KEY, contact_name TEXT DEFAULT "", contact_phone TEXT DEFAULT "", contact_email TEXT DEFAULT "", carrier TEXT DEFAULT "tmobile", channel TEXT DEFAULT "sms", created_at TEXT DEFAULT "", last_active TEXT DEFAULT "", notes TEXT DEFAULT "");
    CREATE TABLE IF NOT EXISTS bot_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts TEXT DEFAULT "", role TEXT, content TEXT, subject TEXT DEFAULT "", sent INTEGER DEFAULT 0, send_status TEXT DEFAULT "draft", send_error TEXT DEFAULT "", model_used TEXT DEFAULT "");
    """)
    conn.commit(); conn.close()
init_db()
def upsert_session(session_id, name="", phone="", email="", carrier="tmobile", channel="sms", notes=""):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""INSERT INTO bot_sessions (session_id,contact_name,contact_phone,contact_email,carrier,channel,notes) VALUES (?,?,?,?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET contact_name=excluded.contact_name, contact_phone=excluded.contact_phone, contact_email=excluded.contact_email, carrier=excluded.carrier, channel=excluded.channel, notes=excluded.notes, last_active=datetime("now")""", (session_id,name,phone,email,carrier,channel,notes))
    conn.commit(); conn.close()
def save_msg(session_id, role, content, subject="", sent=False, status="draft", error="", model=""):
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT INTO bot_messages (session_id,role,content,subject,sent,send_status,send_error,model_used) VALUES (?,?,?,?,?,?,?,?)", (session_id,role,content,subject,int(sent),status,error,model))
    conn.execute("UPDATE bot_sessions SET last_active=datetime(\"now\") WHERE session_id=?", (session_id,))
    conn.commit(); conn.close()
def get_session(sid):
    conn = sqlite3.connect(str(DB_PATH))
    r = conn.execute("SELECT session_id,contact_name,contact_phone,contact_email,carrier,channel,notes,last_active FROM bot_sessions WHERE session_id=?", (sid,)).fetchone()
    conn.close()
    if not r: return None
    return {"session_id":r[0],"name":r[1],"phone":r[2],"email":r[3],"carrier":r[4],"channel":r[5],"notes":r[6],"last_active":r[7]}
def get_messages(sid):
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("SELECT id,ts,role,content,subject,sent,send_status,send_error,model_used FROM bot_messages WHERE session_id=? ORDER BY ts ASC", (sid,)).fetchall()
    conn.close()
    return [{"id":r[0],"ts":r[1],"role":r[2],"content":r[3],"subject":r[4],"sent":bool(r[5]),"status":r[6],"error":r[7],"model":r[8]} for r in rows]
def get_all_sessions():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute("""SELECT s.session_id,s.contact_name,s.contact_phone,s.contact_email,s.channel,s.last_active,COUNT(m.id),SUM(CASE WHEN m.sent=1 THEN 1 ELSE 0 END) FROM bot_sessions s LEFT JOIN bot_messages m ON s.session_id=m.session_id GROUP BY s.session_id ORDER BY s.last_active DESC LIMIT 50""").fetchall()
    conn.close()
    return [{"session_id":r[0],"name":r[1],"phone":r[2],"email":r[3],"channel":r[4],"last_active":r[5],"msg_count":r[6],"sent_count":r[7]} for r in rows]
async def ai_generate(provider, system, prompt, history=None, max_tokens=280):
    msgs = [{"role":h["role"],"content":h["content"]} for h in (history or [])[-8:]]
    msgs.append({"role":"user","content":prompt})
    r = await provider.chat(model_id=provider.resolve("general"), messages=msgs, system=system, max_tokens=max_tokens)
    return r.get("content","").strip(), r.get("model","")
async def gen_reply(provider, channel, incoming, contact_name, history=None):
    parts = (contact_name or "").split()
    first = parts[0] if parts else "there"
    system = SMS_SYSTEM if channel=="sms" else EMAIL_SYSTEM
    return await ai_generate(provider, system, f"Customer ({first}) says: {incoming}\n\nWrite your reply as Kai.", history)
async def gen_initial(provider, channel, contact_name, source="facebook"):
    parts = (contact_name or "").split()
    first = parts[0] if parts else "there"
    month = datetime.now().strftime("%B")
    system = SMS_SYSTEM if channel=="sms" else EMAIL_SYSTEM
    # Source-aware messaging — different opener per lead source
    source_lower = (source or "").lower()
    if "facebook" in source_lower:
        source_phrase = "reached out through our ad"
    elif "website" in source_lower:
        source_phrase = "visited our website"
    elif "marketplace" in source_lower or "craigslist" in source_lower:
        source_phrase = "showed interest from our listing"
    elif "the_knot" in source_lower or "theknot" in source_lower:
        source_phrase = "found us on The Knot"
    elif "weddingwire" in source_lower:
        source_phrase = "found us on WeddingWire"
    elif "google" in source_lower or "gbp" in source_lower:
        source_phrase = "found us on Google"
    elif "referral" in source_lower:
        source_phrase = "was referred to us"
    elif "instagram" in source_lower:
        source_phrase = "reached out on Instagram"
    else:
        source_phrase = "showed interest in Zoar Bathroom Rentals"
    if channel=="sms":
        prompt = f"Write a first SMS to {first} who {source_phrase} about restroom trailer rental. Introduce as Kai, ask about their event."
    else:
        prompt = f"Write first outreach email to {first} who {source_phrase}. First line must be: SUBJECT: [subject]. Then blank line, then body."
    content, model = await ai_generate(provider, system, prompt, max_tokens=350)
    subject = "Your Event Restroom Rental - Zoar Bathroom Rentals"
    body = content
    if channel=="email":
        for i,line in enumerate(content.split("\n")):
            if line.upper().startswith("SUBJECT:"):
                subject=line.split(":",1)[1].strip(); body="\n".join(content.split("\n")[i+1:]).strip(); break
    return body, subject, model
async def gen_followup(provider, contact_name, followup_num=0):
    parts = (contact_name or "").split()
    first = parts[0] if parts else "there"
    month = datetime.now().strftime("%B")
    prompt = f"Short friendly SMS follow-up to {first}, no response in {'2' if followup_num==0 else '4'} days. {'Mention '+month+' dates filling.' if followup_num==0 else 'Final check-in tone.'} Under 2 sentences."
    return await ai_generate(provider, SMS_SYSTEM, prompt, max_tokens=100)
def _clean_phone(p):
    d="".join(c for c in str(p) if c.isdigit())
    if len(d)==11 and d[0]=="1": d=d[1:]
    return d
def _smtp_send(gmail, app_pw, to_addr, msg_string):
    """Synchronous SMTP send with proper cleanup."""
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    try:
        server.login(gmail, app_pw)
        server.sendmail(gmail, to_addr, msg_string)
    finally:
        server.quit()

async def send_sms(gmail, app_pw, phone, message, carrier="tmobile", approval_id=None):
    from integrations.messaging import outbound_gate
    digits = _clean_phone(phone)
    if len(digits) != 10:
        return {"ok": False, "error": f"Invalid phone: need 10 digits, got {len(digits)}"}
    # OUTBOUND GATE: Kill switch + approval verification
    gate = outbound_gate("sms", digits, "", message,
                         approval_id=approval_id, code_path="zoar_bot.send_sms")
    if not gate["ok"]:
        return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}
    gw = CARRIER_GATEWAYS.get(carrier, "@tmomail.net")
    to = f"{digits}{gw}"
    msg = MIMEText(message)
    msg["From"] = gmail
    msg["To"] = to
    msg["Subject"] = ""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: _smtp_send(gmail, app_pw, to, msg.as_string()))
        return {"ok": True, "to": to}
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def send_email(gmail, app_pw, to_email, subject, body, approval_id=None, html_body=None):
    from integrations.messaging import outbound_gate
    if not to_email or "@" not in to_email:
        return {"ok": False, "error": "Invalid email address"}
    # OUTBOUND GATE: Kill switch + approval verification
    gate = outbound_gate("email", to_email, "", body,
                         approval_id=approval_id, code_path="zoar_bot.send_email")
    if not gate["ok"]:
        return {"ok": False, "error": f"BLOCKED: {gate['reason']}", "method": "blocked"}
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Zoar Bathroom Rentals <{gmail}>"
    msg["To"] = to_email
    msg["Reply-To"] = gmail
    msg["Subject"] = subject
    msg["List-Unsubscribe"] = f"<mailto:{gmail}?subject=unsubscribe>"
    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: _smtp_send(gmail, app_pw, to_email, msg.as_string()))
        return {"ok": True, "to": to_email}
    except Exception as e:
        return {"ok": False, "error": str(e)}
