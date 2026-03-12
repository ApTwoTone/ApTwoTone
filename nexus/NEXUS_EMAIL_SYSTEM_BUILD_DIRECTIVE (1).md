# NEXUS EMAIL MARKETING SYSTEM — COMPLETE BUILD DIRECTIVE FOR CLAUDE CODE

## HOW TO USE THIS FILE
Open VS Code. Open Claude Code (Cmd+Shift+P > Claude Code or your terminal). Paste this entire document as your prompt. Claude Code will read it, explore ~/nexus/, and build everything.

---

## OVERVIEW
Build a complete B2B cold email outreach system inside Nexus Network (~/nexus/) that:
1. Stores all 72+ qualified B2B leads in a database
2. Generates personalized cold emails for each lead
3. Routes EVERY email through Telegram for my approval before sending
4. Tracks all outreach with a web dashboard
5. Never sends duplicate emails to anyone
6. Monitors Gmail for replies and notifies me via Telegram

## CRITICAL RULES

### SECURITY
- ZERO auto-send. Every single outbound email must be approved by me via Telegram BEFORE it leaves the system.
- Flow: System generates email -> Sends preview to Telegram -> I tap APPROVE or DENY -> Only then does it send
- If I deny, log it and never re-send that email unless I manually trigger retry.
- Respect existing SecurityGate module and all 10 security gates.

### DUPLICATE PREVENTION
- Sunstone Winery (Sophia Burge, sophia@sunstonewinery.com) has ALREADY been contacted. Pre-load as status CONTACTED so the system never emails them again.
- Before generating ANY email, check: has this email address EVER been sent to? If yes, SKIP.
- Before generating ANY email, check: has this business name been contacted? If yes, SKIP.
- If a previously-contacted business emails US back, route that reply to Telegram for my review. I decide the response.

### TESTING
- For ALL email testing, use ONLY: kaiescobar09@gmail.com
- For ALL SMS/phone testing, use ONLY: 818-448-9055
- NEVER use any other email or phone for testing. NEVER use a real lead email for testing.
- Test mode clearly labeled in logs: "[TEST MODE] Sending to kaiescobar09@gmail.com"

### PRICING IN EMAILS
- NEVER include dollar amounts in cold emails ($1,000, $1,200, $1,500, etc.)
- NEVER say "all-inclusive"
- Mention referral fee as "a referral fee for every booking" without exact amount
- Pricing only shared AFTER a business responds and we enter direct conversation

---

## PHASE 1: DATABASE

Create next available migration in core/db_migrate.py.

### Table: b2b_leads

Columns:
- id: INTEGER PRIMARY KEY AUTOINCREMENT
- business_name: TEXT NOT NULL
- category: TEXT (Private Venue, Party Rental, Event Planner, Bounce House, Church, Winery)
- city: TEXT
- state: TEXT DEFAULT 'CA'
- distance_miles: REAL
- pricing_tier: INTEGER (1, 2, or 3)
- contact_name: TEXT
- email: TEXT
- phone: TEXT
- website: TEXT
- rating: TEXT (HOT, WARM, COOL)
- section: TEXT (A through F from lead list)
- lead_number: INTEGER (1-72)
- email_status: TEXT DEFAULT 'not_sent' (not_sent, pending_approval, approved, sent, replied)
- email_sent_at: DATETIME
- email_content: TEXT (store the actual email body that was sent)
- reply_status: TEXT DEFAULT 'none' (none, positive, negative, unsubscribed)
- reply_content: TEXT
- reply_received_at: DATETIME
- outcome: TEXT DEFAULT 'not_contacted' (not_contacted, in_sequence, interested, partner, not_interested, no_response)
- telegram_approval_status: TEXT (pending, approved, denied)
- telegram_message_id: INTEGER
- notes: TEXT
- created_at: DATETIME DEFAULT CURRENT_TIMESTAMP
- updated_at: DATETIME DEFAULT CURRENT_TIMESTAMP
- is_duplicate: BOOLEAN DEFAULT 0
- do_not_contact: BOOLEAN DEFAULT 0

Unique constraints: UNIQUE(email), UNIQUE(business_name, city)

---

## PHASE 2: LEAD IMPORTER (core/b2b_import.py)

Import these 72 leads. Use this city-to-distance lookup:

San Fernando=0, Sylmar=3, Pacoima=4, Shadow Hills=5, Sun Valley=6, Panorama City=5, Mission Hills=3, Granada Hills=5, North Hollywood=8, Van Nuys=6, Lake Balboa=7, Canoga Park=10, Reseda=8, Northridge=7, West Hills=11, Tarzana=12, Encino=12, Chatsworth=10, Woodland Hills=14, Burbank=13, Glendale=16, Sherman Oaks=12, Studio City=12, Calabasas=18, Simi Valley=20, Moorpark=25, Topanga=22, Agoura Hills=22, Westlake Village=25, Porter Ranch=8, Somis=30, Malibu=35, Canyon Country=20, Santa Clarita=20, Agua Dulce=25, La Canada Flintridge=22, Beverly Hills=25, Pasadena=22, Los Angeles=20

Tier: 0-10mi=1, 10-20mi=2, 20+mi=3

### SECTION A: Private Outdoor Wedding Venues

TIER 1:
1|Orcutt Ranch|Private Venue|West Hills|||(818) 346-7449|laparks.org/horticulture/orcutt-ranch|HOT
2|Japanese Garden (Suiho-en)|Private Venue|Van Nuys|||(818) 756-8166|thejapanesegarden.com|HOT
3|40 Palms Ranch|Private Venue|Van Nuys|||Via website|40palmtrees.com|HOT
4|The Stonehurst|Private Venue|Shadow Hills||||HOT
5|Worthe Ranch|Private Venue|Van Nuys|||Via website|wortheranch.com|HOT
6|Reptacular Animals Ranch|Private Venue|Sylmar|||Via website|reptacularranch.com|HOT
7|AcreRanch|Private Venue|Sylmar|||Via Peerspace|Instagram @AcreRanch|HOT
8|Chateau Lemay|Private Venue|Van Nuys|||Via Peerspace||HOT

TIER 2:
9|Hummingbird Nest Ranch|Private Venue|Simi Valley||info@hummingbirdnestranch.com|(805) 915-7780|hummingbirdnestranch.com|HOT
10|Maravilla Gardens|Private Venue|Moorpark|||(805) 479-0433|maravillagardens.com|HOT
11|Quail Ranch|Private Venue|Simi Valley||||HOT
12|The 1909|Private Venue|Topanga||info@the1909.com|(310) 455-1909|the1909.com|HOT
13|Castaway Burbank|Private Venue|Burbank|||(818) 848-6691|castawayburbank.com|HOT
14|Cornell Winery|Private Venue|Agoura Hills|||(818) 735-3522|cornellwinery.com|HOT
15|Saddle Peak Lodge|Private Venue|Calabasas|||(818) 222-3888|saddlepeaklodge.com|WARM
16|King Gillette Ranch|Private Venue|Calabasas|||(805) 370-2301||HOT
17|Strathearn Historical Park|Private Venue|Simi Valley|||(805) 526-6453|simivalleyhistoricalsociety.org|HOT
18|Leonis Adobe Museum|Private Venue|Calabasas|||(818) 222-6511|leonisadobemuseum.org|WARM
19|Hartley Botanica|Private Venue|Somis|||(805) 386-4578|hartleybotanica.com|HOT
20|Eden Gardens|Private Venue|Moorpark||||HOT
21|Walnut Grove at Tierra Rejada|Private Venue|Moorpark|||Via website|walnutgrovetr.com|HOT
22|Brookview Ranch|Private Venue|Agoura Hills|||Via website|brookviewranch.com|HOT
23|Sportsmens Lodge Event Center|Private Venue|Studio City|||(818) 769-4700|sportsmenslodgeevents.com|HOT
24|Brand Park|Private Venue|Glendale|||(818) 548-2051|brandlibrary.org|HOT

TIER 3:
25|Calamigos Ranch|Private Venue|Malibu||events@calamigos.com|(818) 889-6280|calamigos.com|HOT
26|Cielo Farms|Private Venue|Malibu|||(310) 980-9981|cielofarms.com|HOT
27|Saddlerock Ranch|Private Venue|Malibu|||(310) 456-0624|malibufamilywines.com|HOT
28|Malibu Rocky Oaks|Private Venue|Malibu|||(818) 735-0251||HOT
29|Triunfo Creek Vineyards|Private Venue|Agoura Hills|||(818) 706-8670|triunfocreekvineyards.com|HOT
30|Villa de Palma|Private Venue|Canyon Country||||HOT
31|Descanso Gardens|Private Venue|La Canada Flintridge|||(818) 949-4200|descansogardens.org|HOT
32|Greystone Mansion|Private Venue|Beverly Hills|||(310) 285-6830|greystonemansion.org|HOT

### SECTION B: Wineries
33|Agua Dulce Winery|Winery|Agua Dulce|||(661) 268-7402|aguadulcewinery.com|HOT
34|Reyes Winery|Winery|Agua Dulce|||(661) 268-1865|reyeswinery.com|HOT
35|Malibu Wines|Winery|Malibu|||(818) 865-0605|malibuwines.com|HOT

### SECTION C: Event Planners
36|A Savvy Event|Event Planner|Calabasas|||(818) 676-0600|asavvyevent.com|HOT
37|International Event Company|Event Planner|Sherman Oaks|||(818) 788-0051|internationaleventco.com|WARM
38|Fancy That! Events|Event Planner|Burbank|||Via website|fancythatevents.com|WARM
39|LVL Weddings and Events|Event Planner|Los Angeles|||Via website|lvlweddings.com|HOT
40|Bash Please|Event Planner|Los Angeles|||Via website|bashplease.com|HOT
41|Sonia Sharma Events|Event Planner|Los Angeles|||Via website|soniasharmaevents.com|HOT
42|Sterling Engagements|Event Planner|Los Angeles|||Via website|sterlingengagements.com|HOT
43|Details Details|Event Planner|Los Angeles|||Via website|aboutdetailsdetails.com|HOT
44|Intertwined Events|Event Planner|Los Angeles|||Via website|intertwinedevents.com|HOT
45|GATHER Events|Event Planner|Los Angeles|||Via website|gatherevents.com|HOT
46|Utopian Events|Event Planner|Los Angeles||||HOT
47|Raakhe Weddings and Events|Event Planner|Los Angeles||||HOT
48|Crimson Bleu Events|Event Planner|Los Angeles||||HOT
49|KIS cubed Events|Event Planner|Los Angeles||||HOT

### SECTION D: Tent and Party Rental
50|Raphaels Party Rentals|Party Rental|Panorama City|||(818) 786-3749||HOT
51|Big Blue Sky Party Rentals|Party Rental|Van Nuys|||(818) 786-5394||HOT
52|Fiesta Party Rentals|Party Rental|Pacoima|||(818) 896-3912||HOT
53|Palace Party Rental|Party Rental|Sun Valley|||(818) 768-2344||HOT
54|Town and Country Event Rentals|Party Rental|Van Nuys|||(818) 908-4211||HOT
55|A Rental Connection|Party Rental|North Hollywood|||(818) 982-3332||HOT
56|Bob Gail Special Events|Party Rental|Burbank|||(818) 954-9494||HOT
57|Platinum Event Rentals|Party Rental|Chatsworth|||(818) 709-1920||HOT
58|Undercover Tent and Party|Party Rental|Woodland Hills|||(818) 346-1500||HOT
59|Avalon Tent and Party|Party Rental|Woodland Hills|||(818) 346-2411||HOT
60|Glendale Party Rental|Party Rental|Glendale|||(818) 246-7373||WARM

### SECTION E: Bounce House
61|Magic Jump Rentals|Bounce House|Pacoima|||(818) 890-1500||HOT
62|Jolly Jumps|Bounce House|Sun Valley|||(818) 768-4FUN||HOT
63|SoCal Bounce Inflatables|Bounce House|Canoga Park|||(818) 730-8488||HOT
64|Valley Party Jumpers|Bounce House|Panorama City|||(818) 402-5000||HOT
65|All About Fun Entertainment|Bounce House|Los Angeles|||(818) 994-4455||HOT

### SECTION F: Churches
66|Shepherd Church|Church|Porter Ranch|||(818) 831-9333||HOT
67|Grace Community Church|Church|Sun Valley|||(818) 909-5500||HOT
68|The Church On The Way|Church|Van Nuys|||(818) 779-8000||HOT
69|San Fernando Mission Parish|Church|Mission Hills|||(818) 361-0186||HOT
70|St. Genevieve Church|Church|Panorama City|||(818) 894-6081||HOT
71|St. Mary Armenian Apostolic|Church|Glendale|||(818) 244-5799||HOT
72|Hindu Temple Malibu|Church|Calabasas|||(818) 880-5552||HOT

### PRE-LOAD (already contacted):
73|Sunstone Winery|Winery|Santa Ynez|Sophia Burge|sophia@sunstonewinery.com|||HOT|email_1_sent|in_sequence|notes:First reply received. Pricing discussion in progress.

---

## PHASE 3: EMAIL TEMPLATE (core/cold_email.py)

### IMPORTANT: ONE EMAIL ONLY. NO FOLLOW-UP SEQUENCE.
We send ONE cold email per business. That is it. No Email 2, 3, or 4. No follow-ups. No drip sequence. If they reply, I handle the conversation manually. If they do not reply, we move on.

### The Single Cold Email:
Subject: Luxury restroom solution for {business_name} events

Hi there,

I am reaching out from Zoar Bathroom Rentals here in the San Fernando Valley. We provide a luxury 4-stall restroom trailer for outdoor weddings and events featuring flushing toilets, running water, air conditioning, full-size mirrors, and dark wood interiors.

We work with venues and event businesses across the area and offer our partners a referral fee for every booking that comes through their recommendation. We handle delivery, setup, cleaning, and pickup so there is zero work on your end.

Would you be open to a quick chat about how this could work for {business_name}?

Best,
Kai Escobar
Zoar Bathroom Rentals
(424) 235-8979
zoarbathroomrental.com

### CAN-SPAM footer (append to every email):

---
Zoar Bathroom Rentals | San Fernando Valley, CA
If you do not wish to receive future emails, reply with "unsubscribe" and we will remove you immediately.

---

## PHASE 4: TELEGRAM APPROVAL WORKFLOW

Build into existing Telegram bot (integrations/telegram_bot.py).

### For each email in a batch, send this Telegram message:

NEW B2B EMAIL FOR APPROVAL

To: {business_name}
Email: {email_address}
City: {city} ({distance_miles} mi - Tier {pricing_tier})
Category: {category}
Rating: {rating}
--- EMAIL PREVIEW ---
Subject: {subject}

{full_email_body}
--- END PREVIEW ---

[APPROVE] [DENY]

- APPROVE and DENY are inline keyboard buttons
- APPROVE: send email via Gmail SMTP, update database, confirm to Telegram
- DENY: log denial, confirm to Telegram, do NOT send

### Telegram Commands:
- /b2b_status - Summary stats
- /generate_batch - Generate todays approval requests (10-15 emails)
- /b2b_lead {id} - Show lead details
- /b2b_search {term} - Search by business name or city

### Reply Monitoring:
- Check Gmail inbox every 30 min for replies
- Match by sender email against b2b_leads table
- On match, send Telegram notification with reply content
- Auto-detect "unsubscribe" in replies and set do_not_contact = 1

---

## PHASE 5: WEB DASHBOARD

### GET /api/b2b/dashboard - HTML table showing all leads with:
- Business name, category, city, tier, rating
- Email, phone, website
- Status (color coded)
- Last email sent date
- View button (shows full email content)

### GET /api/b2b/leads - JSON list with filter params (?tier=1, ?status=sent, etc.)
### GET /api/b2b/leads/{id} - Full lead detail with email sent
### PATCH /api/b2b/leads/{id} - Update fields
### POST /api/b2b/import - Re-import leads
### GET /api/b2b/digest - Generate batch (JSON)

---

## PHASE 6: GMAIL INTEGRATION

- Send FROM: zoarbathrooms@gmail.com
- Reply-To: zoarbathrooms@gmail.com
- List-Unsubscribe header
- Track Message-ID for threading
- Use IMAP for reply monitoring

---

## IMPLEMENTATION ORDER

1. Database migration (b2b_leads table)
2. Lead importer (72 leads + Sunstone)
3. Email templates
4. Telegram approval workflow
5. Gmail send + reply monitoring
6. Web dashboard
7. Test ENTIRE flow with kaiescobar09@gmail.com ONLY

## TESTING PROTOCOL

1. Import leads - verify 73 in database (72 + Sunstone)
2. Generate batch - verify Telegram receives approval requests
3. Approve one test - verify it sends to kaiescobar09@gmail.com NOT a real lead
4. Check dashboard shows email as sent
5. Reply from kaiescobar09@gmail.com - verify Telegram gets notification
6. Generate batch again - verify test lead is skipped
7. Verify Sunstone is never included in any batch

ONLY after ALL tests pass with kaiescobar09@gmail.com should real leads be contacted.

## FILES TO CREATE/MODIFY

New:
- core/b2b_leads.py
- core/b2b_import.py
- core/cold_email.py
- core/cold_email_digest.py
- core/b2b_dashboard.py

Modify:
- core/db_migrate.py (add migration)
- integrations/telegram_bot.py (approval workflow + commands)
- integrations/messaging.py (B2B email function)
- main.py (register routes)

Do NOT modify: SecurityGate, existing leads table, Facebook campaigns, quote system.

Read all existing code first. Show me your plan before building. Go.
