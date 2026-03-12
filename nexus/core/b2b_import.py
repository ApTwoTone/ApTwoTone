"""
B2B Lead Importer — Loads 72 qualified B2B leads + 1 pre-contacted (Sunstone).
Idempotent: INSERT OR IGNORE respects UNIQUE constraints on email and (business_name, city).
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".nexus" / "memory.db"

# ── City → Distance (miles from San Fernando) ────────────────────────────────
CITY_DISTANCE = {
    "San Fernando": 0, "Sylmar": 3, "Pacoima": 4, "Shadow Hills": 5,
    "Sun Valley": 6, "Panorama City": 5, "Mission Hills": 3, "Granada Hills": 5,
    "North Hollywood": 8, "Van Nuys": 6, "Lake Balboa": 7, "Canoga Park": 10,
    "Reseda": 8, "Northridge": 7, "West Hills": 11, "Tarzana": 12,
    "Encino": 12, "Chatsworth": 10, "Woodland Hills": 14, "Burbank": 13,
    "Glendale": 16, "Sherman Oaks": 12, "Studio City": 12, "Calabasas": 18,
    "Simi Valley": 20, "Moorpark": 25, "Topanga": 22, "Agoura Hills": 22,
    "Westlake Village": 25, "Porter Ranch": 8, "Somis": 30, "Malibu": 35,
    "Canyon Country": 20, "Santa Clarita": 20, "Agua Dulce": 25,
    "La Canada Flintridge": 22, "Beverly Hills": 25, "Pasadena": 22,
    "Los Angeles": 20, "Santa Ynez": 60,
}


def _tier(distance: float) -> int:
    if distance <= 10:
        return 1
    if distance <= 20:
        return 2
    return 3


# ── Lead Data ─────────────────────────────────────────────────────────────────
# Format: (lead_number, business_name, category, city, contact_name, email, phone, website, rating, section)

LEADS = [
    # SECTION A: Private Outdoor Wedding Venues — TIER 1
    (1,  "Orcutt Ranch",                      "Private Venue", "West Hills",           "", "", "(818) 346-7449", "laparks.org/horticulture/orcutt-ranch", "HOT", "A"),
    (2,  "Japanese Garden (Suiho-en)",         "Private Venue", "Van Nuys",             "", "", "(818) 756-8166", "thejapanesegarden.com",                 "HOT", "A"),
    (3,  "40 Palms Ranch",                     "Private Venue", "Van Nuys",             "", "", "Via website",    "40palmtrees.com",                       "HOT", "A"),
    (4,  "The Stonehurst",                     "Private Venue", "Shadow Hills",         "", "", "",               "",                                      "HOT", "A"),
    (5,  "Worthe Ranch",                       "Private Venue", "Van Nuys",             "", "", "Via website",    "wortheranch.com",                       "HOT", "A"),
    (6,  "Reptacular Animals Ranch",           "Private Venue", "Sylmar",               "", "", "Via website",    "reptacularranch.com",                   "HOT", "A"),
    (7,  "AcreRanch",                          "Private Venue", "Sylmar",               "", "", "Via Peerspace",  "Instagram @AcreRanch",                  "HOT", "A"),
    (8,  "Chateau Lemay",                      "Private Venue", "Van Nuys",             "", "", "Via Peerspace",  "",                                      "HOT", "A"),
    # SECTION A: TIER 2
    (9,  "Hummingbird Nest Ranch",             "Private Venue", "Simi Valley",          "", "info@hummingbirdnestranch.com", "(805) 915-7780", "hummingbirdnestranch.com",  "HOT",  "A"),
    (10, "Maravilla Gardens",                  "Private Venue", "Moorpark",             "", "",                              "(805) 479-0433", "maravillagardens.com",      "HOT",  "A"),
    (11, "Quail Ranch",                        "Private Venue", "Simi Valley",          "", "",                              "",               "",                          "HOT",  "A"),
    (12, "The 1909",                           "Private Venue", "Topanga",              "", "info@the1909.com",              "(310) 455-1909", "the1909.com",               "HOT",  "A"),
    (13, "Castaway Burbank",                   "Private Venue", "Burbank",              "", "",                              "(818) 848-6691", "castawayburbank.com",       "HOT",  "A"),
    (14, "Cornell Winery",                     "Private Venue", "Agoura Hills",         "", "",                              "(818) 735-3522", "cornellwinery.com",         "HOT",  "A"),
    (15, "Saddle Peak Lodge",                  "Private Venue", "Calabasas",            "", "",                              "(818) 222-3888", "saddlepeaklodge.com",       "WARM", "A"),
    (16, "King Gillette Ranch",                "Private Venue", "Calabasas",            "", "",                              "(805) 370-2301", "",                          "HOT",  "A"),
    (17, "Strathearn Historical Park",         "Private Venue", "Simi Valley",          "", "",                              "(805) 526-6453", "simivalleyhistoricalsociety.org", "HOT", "A"),
    (18, "Leonis Adobe Museum",                "Private Venue", "Calabasas",            "", "",                              "(818) 222-6511", "leonisadobemuseum.org",     "WARM", "A"),
    (19, "Hartley Botanica",                   "Private Venue", "Somis",                "", "",                              "(805) 386-4578", "hartleybotanica.com",       "HOT",  "A"),
    (20, "Eden Gardens",                       "Private Venue", "Moorpark",             "", "",                              "",               "",                          "HOT",  "A"),
    (21, "Walnut Grove at Tierra Rejada",      "Private Venue", "Moorpark",             "", "",                              "",               "walnutgrovetr.com",         "HOT",  "A"),
    (22, "Brookview Ranch",                    "Private Venue", "Agoura Hills",         "", "",                              "",               "brookviewranch.com",        "HOT",  "A"),
    (23, "Sportsmens Lodge Event Center",      "Private Venue", "Studio City",          "", "",                              "(818) 769-4700", "sportsmenslodgeevents.com", "HOT",  "A"),
    (24, "Brand Park",                         "Private Venue", "Glendale",             "", "",                              "(818) 548-2051", "brandlibrary.org",          "HOT",  "A"),
    # SECTION A: TIER 3
    (25, "Calamigos Ranch",                    "Private Venue", "Malibu",               "", "events@calamigos.com",          "(818) 889-6280", "calamigos.com",             "HOT",  "A"),
    (26, "Cielo Farms",                        "Private Venue", "Malibu",               "", "",                              "(310) 980-9981", "cielofarms.com",            "HOT",  "A"),
    (27, "Saddlerock Ranch",                   "Private Venue", "Malibu",               "", "",                              "(310) 456-0624", "malibufamilywines.com",     "HOT",  "A"),
    (28, "Malibu Rocky Oaks",                  "Private Venue", "Malibu",               "", "",                              "(818) 735-0251", "",                          "HOT",  "A"),
    (29, "Triunfo Creek Vineyards",            "Private Venue", "Agoura Hills",         "", "",                              "(818) 706-8670", "triunfocreekvineyards.com", "HOT",  "A"),
    (30, "Villa de Palma",                     "Private Venue", "Canyon Country",       "", "",                              "",               "",                          "HOT",  "A"),
    (31, "Descanso Gardens",                   "Private Venue", "La Canada Flintridge", "", "",                              "(818) 949-4200", "descansogardens.org",       "HOT",  "A"),
    (32, "Greystone Mansion",                  "Private Venue", "Beverly Hills",        "", "",                              "(310) 285-6830", "greystonemansion.org",      "HOT",  "A"),
    # SECTION B: Wineries
    (33, "Agua Dulce Winery",                  "Winery",        "Agua Dulce",           "", "",                              "(661) 268-7402", "aguadulcewinery.com",       "HOT",  "B"),
    (34, "Reyes Winery",                       "Winery",        "Agua Dulce",           "", "",                              "(661) 268-1865", "reyeswinery.com",           "HOT",  "B"),
    (35, "Malibu Wines",                       "Winery",        "Malibu",               "", "",                              "(818) 865-0605", "malibuwines.com",           "HOT",  "B"),
    # SECTION C: Event Planners
    (36, "A Savvy Event",                      "Event Planner", "Calabasas",            "", "",                              "(818) 676-0600", "asavvyevent.com",           "HOT",  "C"),
    (37, "International Event Company",        "Event Planner", "Sherman Oaks",         "", "",                              "(818) 788-0051", "internationaleventco.com",  "WARM", "C"),
    (38, "Fancy That! Events",                 "Event Planner", "Burbank",              "", "",                              "",               "fancythatevents.com",       "WARM", "C"),
    (39, "LVL Weddings and Events",            "Event Planner", "Los Angeles",          "", "",                              "",               "lvlweddings.com",           "HOT",  "C"),
    (40, "Bash Please",                        "Event Planner", "Los Angeles",          "", "",                              "",               "bashplease.com",            "HOT",  "C"),
    (41, "Sonia Sharma Events",                "Event Planner", "Los Angeles",          "", "",                              "",               "soniasharmaevents.com",     "HOT",  "C"),
    (42, "Sterling Engagements",               "Event Planner", "Los Angeles",          "", "",                              "",               "sterlingengagements.com",   "HOT",  "C"),
    (43, "Details Details",                    "Event Planner", "Los Angeles",          "", "",                              "",               "aboutdetailsdetails.com",   "HOT",  "C"),
    (44, "Intertwined Events",                 "Event Planner", "Los Angeles",          "", "",                              "",               "intertwinedevents.com",     "HOT",  "C"),
    (45, "GATHER Events",                      "Event Planner", "Los Angeles",          "", "",                              "",               "gatherevents.com",          "HOT",  "C"),
    (46, "Utopian Events",                     "Event Planner", "Los Angeles",          "", "",                              "",               "",                          "HOT",  "C"),
    (47, "Raakhe Weddings and Events",         "Event Planner", "Los Angeles",          "", "",                              "",               "",                          "HOT",  "C"),
    (48, "Crimson Bleu Events",                "Event Planner", "Los Angeles",          "", "",                              "",               "",                          "HOT",  "C"),
    (49, "KIS cubed Events",                   "Event Planner", "Los Angeles",          "", "",                              "",               "",                          "HOT",  "C"),
    # SECTION D: Tent and Party Rental
    (50, "Raphaels Party Rentals",             "Party Rental",  "Panorama City",        "", "",                              "(818) 786-3749", "",                          "HOT",  "D"),
    (51, "Big Blue Sky Party Rentals",         "Party Rental",  "Van Nuys",             "", "",                              "(818) 786-5394", "",                          "HOT",  "D"),
    (52, "Fiesta Party Rentals",               "Party Rental",  "Pacoima",              "", "",                              "(818) 896-3912", "",                          "HOT",  "D"),
    (53, "Palace Party Rental",                "Party Rental",  "Sun Valley",           "", "",                              "(818) 768-2344", "",                          "HOT",  "D"),
    (54, "Town and Country Event Rentals",     "Party Rental",  "Van Nuys",             "", "",                              "(818) 908-4211", "",                          "HOT",  "D"),
    (55, "A Rental Connection",                "Party Rental",  "North Hollywood",      "", "",                              "(818) 982-3332", "",                          "HOT",  "D"),
    (56, "Bob Gail Special Events",            "Party Rental",  "Burbank",              "", "",                              "(818) 954-9494", "",                          "HOT",  "D"),
    (57, "Platinum Event Rentals",             "Party Rental",  "Chatsworth",           "", "",                              "(818) 709-1920", "",                          "HOT",  "D"),
    (58, "Undercover Tent and Party",          "Party Rental",  "Woodland Hills",       "", "",                              "(818) 346-1500", "",                          "HOT",  "D"),
    (59, "Avalon Tent and Party",              "Party Rental",  "Woodland Hills",       "", "",                              "(818) 346-2411", "",                          "HOT",  "D"),
    (60, "Glendale Party Rental",              "Party Rental",  "Glendale",             "", "",                              "(818) 246-7373", "",                          "WARM", "D"),
    # SECTION E: Bounce House
    (61, "Magic Jump Rentals",                 "Bounce House",  "Pacoima",              "", "",                              "(818) 890-1500", "",                          "HOT",  "E"),
    (62, "Jolly Jumps",                        "Bounce House",  "Sun Valley",           "", "",                              "(818) 768-4FUN", "",                          "HOT",  "E"),
    (63, "SoCal Bounce Inflatables",           "Bounce House",  "Canoga Park",          "", "",                              "(818) 730-8488", "",                          "HOT",  "E"),
    (64, "Valley Party Jumpers",               "Bounce House",  "Panorama City",        "", "",                              "(818) 402-5000", "",                          "HOT",  "E"),
    (65, "All About Fun Entertainment",        "Bounce House",  "Los Angeles",          "", "",                              "(818) 994-4455", "",                          "HOT",  "E"),
    # SECTION F: Churches
    (66, "Shepherd Church",                    "Church",        "Porter Ranch",         "", "",                              "(818) 831-9333", "",                          "HOT",  "F"),
    (67, "Grace Community Church",             "Church",        "Sun Valley",           "", "",                              "(818) 909-5500", "",                          "HOT",  "F"),
    (68, "The Church On The Way",              "Church",        "Van Nuys",             "", "",                              "(818) 779-8000", "",                          "HOT",  "F"),
    (69, "San Fernando Mission Parish",        "Church",        "Mission Hills",        "", "",                              "(818) 361-0186", "",                          "HOT",  "F"),
    (70, "St. Genevieve Church",               "Church",        "Panorama City",        "", "",                              "(818) 894-6081", "",                          "HOT",  "F"),
    (71, "St. Mary Armenian Apostolic",        "Church",        "Glendale",             "", "",                              "(818) 244-5799", "",                          "HOT",  "F"),
    (72, "Hindu Temple Malibu",                "Church",        "Calabasas",            "", "",                              "(818) 880-5552", "",                          "HOT",  "F"),
]

# Pre-contacted lead — Sunstone Winery (already in conversation)
PRELOADED = [
    (73, "Sunstone Winery", "Winery", "Santa Ynez", "Sophia Burge", "sophia@sunstonewinery.com", "", "", "HOT", "B"),
]


def import_leads(db_path=None):
    """Import all B2B leads into the database. Idempotent via INSERT OR IGNORE."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))

    inserted = 0
    skipped = 0

    for lead_number, name, category, city, contact, email, phone, website, rating, section in LEADS + PRELOADED:
        distance = CITY_DISTANCE.get(city, 0)
        tier = _tier(distance)

        # Sunstone: pre-load as already contacted
        is_sunstone = (name == "Sunstone Winery")
        email_status = "sent" if is_sunstone else "not_sent"
        outcome = "in_sequence" if is_sunstone else "not_contacted"
        notes = "First reply received. Pricing discussion in progress." if is_sunstone else ""
        email_sent_at = "2025-01-01 00:00:00" if is_sunstone else None

        try:
            conn.execute("""
                INSERT OR IGNORE INTO b2b_leads
                (business_name, category, city, state, distance_miles, pricing_tier,
                 contact_name, email, phone, website, rating, section, lead_number,
                 email_status, email_sent_at, outcome, notes)
                VALUES (?, ?, ?, 'CA', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                name, category, city, distance, tier,
                contact, email, phone, website, rating, section, lead_number,
                email_status, email_sent_at, outcome, notes,
            ))
            if conn.total_changes:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.IntegrityError:
            skipped += 1

    conn.commit()

    # Verify count
    total = conn.execute("SELECT COUNT(*) FROM b2b_leads").fetchone()[0]
    conn.close()

    print(f"[B2B Import] Inserted: {inserted}, Skipped: {skipped}, Total in DB: {total}")
    return {"inserted": inserted, "skipped": skipped, "total": total}


if __name__ == "__main__":
    from core.db_migrate import run_migrations
    run_migrations()
    result = import_leads()
    print(f"Done: {result}")
