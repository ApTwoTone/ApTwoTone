"""
Daily Posting Engine — 30+ listing angles in English & Spanish

Generates marketplace listings for FB Marketplace, Craigslist, and FB Page
across 30+ unique angles targeting every service niche.

Runs as a background task: every morning at 8 AM PT, it generates today's
consolidated briefing and sends it to Kai via Telegram. Kai copies and
posts manually (no API for marketplace) or we auto-post to FB Page.

Content strategy:
  - Each day gets 2-3 marketplace listings (different platforms/angles)
  - 1 FB Page organic post (auto-posted via fb_page.py)
  - All angles rotate to avoid duplicate content
  - Spanish listings target ES-speaking SoCal audience
"""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

DB_PATH = Path.home() / ".nexus" / "memory.db"
LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Public business info (NEVER personal) ─────────────────────────────────
BIZ_PHONE = "(424) 235-8979"
BIZ_EMAIL = "zoarbathrooms@gmail.com"
BIZ_WEBSITE = "https://zoarbathroomrental.com"
BIZ_NAME = "Zoar Bathroom Rental"
SERVICE_AREA = "Los Angeles, San Fernando Valley, Ventura, Santa Clarita, and all of SoCal"
SERVICE_AREA_ES = "Los Ángeles, San Fernando Valley, Ventura, Santa Clarita y todo el sur de California"

# ── Image sets (rotate per listing) ───────────────────────────────────────
IMAGE_SETS = [
    ["web-000.jpg", "web-004.jpg", "web-006.jpg", "web-010.jpg", "web-024.jpg"],
    ["web-002.jpg", "web-010.jpg", "web-000.jpg", "web-014.jpg", "web-006.jpg"],
    ["web-024.jpg", "web-002.jpg", "web-014.jpg", "web-004.jpg", "web-000.jpg"],
    ["web-006.jpg", "web-000.jpg", "web-024.jpg", "web-002.jpg", "web-010.jpg"],
    ["web-014.jpg", "web-024.jpg", "web-004.jpg", "web-006.jpg", "web-002.jpg"],
    ["web-010.jpg", "web-006.jpg", "web-002.jpg", "web-024.jpg", "web-004.jpg"],
]

# ═══════════════════════════════════════════════════════════════════════════
# LISTING ANGLES — 30+ unique angles in English + Spanish
# Each angle: id, niche, lang, titles[], descriptions[], price_range, tags[]
# ═══════════════════════════════════════════════════════════════════════════

LISTING_ANGLES = [
    # ── WEDDING: 6 English angles ──────────────────────────────────────
    {
        "id": "wedding_vineyard_en",
        "niche": "wedding",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer for Vineyard Weddings — SoCal",
            "5-Star Portable Restroom — Perfect for Vineyard Receptions",
            "Elegant Restroom Trailer Rental — Wine Country Weddings",
        ],
        "descriptions": [
            (
                "Hosting a vineyard wedding? Your guests deserve better than a plastic porta potty.\n\n"
                "Our luxury 4-stall restroom trailer features AC, running water, hardwood-style floors, "
                "full-length mirrors, and a Bluetooth speaker. It looks like part of the venue.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                "Includes delivery, setup, and pickup.\n\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["wedding", "vineyard", "outdoor"],
    },
    {
        "id": "wedding_beach_en",
        "niche": "wedding",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer — Beach Weddings & Coastal Events",
            "Premium Portable Restroom for Beach Weddings — SoCal",
            "Beach Wedding Restroom Trailer Rental — Malibu to Santa Barbara",
        ],
        "descriptions": [
            (
                "Planning a beach wedding? Sand and porta potties don't mix.\n\n"
                "Our luxury restroom trailer has 4 private stalls with AC, running water, "
                "hardwood floors, and full-length mirrors. Your guests can freshen up in comfort.\n\n"
                "We deliver to any beach venue from Malibu to Santa Barbara.\n\n"
                f"Call or text: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1200, 1500),
        "tags": ["wedding", "beach", "coastal"],
    },
    {
        "id": "wedding_backyard_en",
        "niche": "wedding",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer — Backyard Weddings & Home Events",
            "Backyard Wedding? You Need This Luxury Restroom Trailer",
            "Premium Portable Bathroom for Backyard Weddings — SoCal",
        ],
        "descriptions": [
            (
                "Backyard wedding but your house doesn't have enough bathrooms?\n\n"
                "Our luxury restroom trailer solves that instantly. 4 private stalls with "
                "AC, running water, hardwood floors, and mirrors. It tucks right into your "
                "driveway or side yard.\n\n"
                "Your guests will be impressed — guaranteed.\n\n"
                f"Delivery + setup + pickup included.\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1300),
        "tags": ["wedding", "backyard", "home"],
    },
    {
        "id": "wedding_estate_en",
        "niche": "wedding",
        "lang": "en",
        "titles": [
            "Estate Wedding Restroom Trailer — Luxury Portable Rental",
            "Private Estate Wedding? Upgrade to a Luxury Restroom Trailer",
            "5-Star Restroom Trailer for Estate Weddings — SoCal",
        ],
        "descriptions": [
            (
                "Your estate wedding deserves restrooms that match the venue.\n\n"
                "Our luxury 4-stall trailer blends seamlessly with high-end settings. "
                "AC, running water, hardwood-style floors, full-length mirrors, ambient lighting.\n\n"
                "We've served estates across Malibu, Pacific Palisades, Pasadena, and beyond.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Book your date: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1200, 1600),
        "tags": ["wedding", "estate", "luxury"],
    },
    {
        "id": "wedding_rustic_en",
        "niche": "wedding",
        "lang": "en",
        "titles": [
            "Rustic Wedding Restroom Trailer Rental — Ranch & Barn Venues",
            "Barn Wedding? Luxury Portable Restroom Trailer — SoCal",
            "Ranch Wedding Restroom Solution — Luxury 4-Stall Trailer",
        ],
        "descriptions": [
            (
                "Rustic venue, luxury restrooms. That's the combo your guests will love.\n\n"
                "Our trailer has 4 private stalls with AC, running water, hardwood floors, "
                "and full-length mirrors. Perfect for ranch weddings, barn venues, and "
                "outdoor celebrations where plumbing isn't available.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Text for pricing: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["wedding", "rustic", "ranch", "barn"],
    },
    {
        "id": "wedding_generic_en",
        "niche": "wedding",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer for Weddings — Your Guests Will Thank You",
            "Premium 4-Stall Restroom Trailer — Wedding & Reception Rental",
            "Elegant Portable Restroom — Wedding Season SoCal",
        ],
        "descriptions": [
            (
                "Stop worrying about your guests' comfort. Our luxury 4-stall restroom trailer "
                "has AC, running water, hardwood floors, and full-length mirrors.\n\n"
                "Your guests will think it's part of the venue.\n\n"
                f"Serving all of SoCal: {SERVICE_AREA}.\n"
                "We handle delivery, setup, and pickup.\n\n"
                f"Get your free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["wedding", "general"],
    },

    # ── WEDDING: 3 Spanish angles ──────────────────────────────────────
    {
        "id": "wedding_general_es",
        "niche": "wedding",
        "lang": "es",
        "titles": [
            "Baño de Lujo Portátil para Bodas — Alquiler SoCal",
            "Tráiler de Baño de Lujo para Bodas — 4 Cabinas con AC",
            "Renta de Baño Premium para Tu Boda — Sur de California",
        ],
        "descriptions": [
            (
                "¿Planeando una boda al aire libre? Tus invitados merecen algo mejor que un baño portátil de plástico.\n\n"
                "Nuestro tráiler de lujo tiene 4 cabinas privadas con aire acondicionado, "
                "agua corriente, pisos de madera, espejos de cuerpo entero y bocina Bluetooth.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                "Incluye entrega, instalación y recolección.\n\n"
                f"Cotización gratis: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["wedding", "spanish"],
    },
    {
        "id": "wedding_backyard_es",
        "niche": "wedding",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Bodas en Casa — Portátil con AC",
            "¿Boda en tu Patio? Renta un Baño de Lujo — SoCal",
            "Tráiler de Baño Premium para Eventos en Casa",
        ],
        "descriptions": [
            (
                "¿Boda en tu casa pero no tienes suficientes baños?\n\n"
                "Nuestro tráiler de baño de lujo resuelve eso al instante. 4 cabinas privadas "
                "con aire acondicionado, agua corriente, pisos de madera y espejos.\n\n"
                "Cabe en tu entrada o al lado de tu casa.\n\n"
                f"Entrega + instalación incluida.\n"
                f"Llama o envía texto: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1300),
        "tags": ["wedding", "backyard", "spanish"],
    },
    {
        "id": "wedding_vineyard_es",
        "niche": "wedding",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Bodas en Viñedos — SoCal",
            "Tráiler de Baño Premium para Bodas al Aire Libre",
        ],
        "descriptions": [
            (
                "¿Boda en un viñedo o rancho? Tu evento merece baños a la altura.\n\n"
                "Nuestro tráiler de lujo tiene 4 cabinas con AC, agua, pisos elegantes y espejos. "
                "Se ve como parte del lugar.\n\n"
                f"Servimos todo el sur de California.\n"
                f"Cotización gratis: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["wedding", "vineyard", "spanish"],
    },

    # ── QUINCEAÑERA ────────────────────────────────────────────────────
    {
        "id": "quince_en",
        "niche": "quinceanera",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer for Quinceañeras — SoCal Rental",
            "Quinceañera Restroom Trailer — Luxury Portable Bathroom",
            "Premium Restroom for Quinceañera Parties — LA & SoCal",
        ],
        "descriptions": [
            (
                "Make her quinceañera unforgettable — even the restrooms.\n\n"
                "Our luxury 4-stall trailer has AC, running water, hardwood floors, "
                "mirrors, and ambient lighting. Perfect for backyard and outdoor celebrations.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1300),
        "tags": ["quinceanera", "party"],
    },
    {
        "id": "quince_es",
        "niche": "quinceanera",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Quinceañeras — Alquiler en SoCal",
            "Tráiler de Baño Premium para tu Quinceañera — LA",
            "Renta de Baño Portátil de Lujo — Quinceañeras y Fiestas",
        ],
        "descriptions": [
            (
                "Haz su quinceañera inolvidable — hasta los baños.\n\n"
                "Nuestro tráiler tiene 4 cabinas privadas con aire acondicionado, "
                "agua corriente, pisos de madera, espejos y bocina Bluetooth.\n\n"
                "Ideal para fiestas en casa, salones al aire libre y jardines.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Cotización gratis: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1300),
        "tags": ["quinceanera", "party", "spanish"],
    },

    # ── GRADUATION PARTIES ─────────────────────────────────────────────
    {
        "id": "graduation_en",
        "niche": "graduation",
        "lang": "en",
        "titles": [
            "Graduation Party Restroom Trailer — Luxury Portable Rental",
            "Hosting a Graduation Party? Luxury Restroom Trailer — SoCal",
            "Premium Portable Restroom for Graduation Celebrations",
        ],
        "descriptions": [
            (
                "Throwing a big graduation party? Don't make your guests wait in line "
                "for one bathroom.\n\n"
                "Our luxury trailer has 4 stalls with AC, running water, mirrors, and "
                "hardwood floors. Handles any crowd comfortably.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Book your date: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1200),
        "tags": ["graduation", "party"],
    },
    {
        "id": "graduation_es",
        "niche": "graduation",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Fiesta de Graduación — SoCal",
            "Tráiler de Baño Premium para Graduaciones — Alquiler",
        ],
        "descriptions": [
            (
                "¿Gran fiesta de graduación? No hagas que tus invitados esperen.\n\n"
                "Nuestro tráiler tiene 4 cabinas con AC, agua, espejos y pisos elegantes. "
                "Perfecto para fiestas grandes.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Reserva: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1200),
        "tags": ["graduation", "party", "spanish"],
    },

    # ── BIRTHDAY / BACKYARD PARTIES ────────────────────────────────────
    {
        "id": "birthday_en",
        "niche": "party",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer for Backyard Parties — SoCal",
            "Hosting a Big Party? Luxury Portable Restroom Rental",
            "Premium Restroom Trailer — Birthday Parties & Celebrations",
        ],
        "descriptions": [
            (
                "Big party, small house? Our luxury restroom trailer handles the crowd.\n\n"
                "4 private stalls with AC, running water, hardwood floors, and mirrors. "
                "Drop it in your driveway and your guests stay comfortable all night.\n\n"
                "Great for birthdays, anniversaries, holiday gatherings, and block parties.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["party", "birthday", "backyard"],
    },
    {
        "id": "birthday_es",
        "niche": "party",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Fiestas en Casa — Alquiler SoCal",
            "Tráiler de Baño para Cumpleaños y Celebraciones",
            "Renta Baño Portátil de Lujo — Fiestas y Eventos",
        ],
        "descriptions": [
            (
                "¿Fiesta grande, casa pequeña? Nuestro tráiler de baño resuelve eso.\n\n"
                "4 cabinas privadas con AC, agua, pisos de madera y espejos. "
                "Se estaciona en tu entrada y tus invitados estarán cómodos toda la noche.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Cotización: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["party", "birthday", "spanish"],
    },

    # ── CORPORATE EVENTS ───────────────────────────────────────────────
    {
        "id": "corporate_gala_en",
        "niche": "corporate",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer — Corporate Events & Galas",
            "Executive Portable Restroom — Corporate Event Rental SoCal",
            "Premium Restroom Trailer for Company Events & Retreats",
        ],
        "descriptions": [
            (
                "Your corporate event needs restrooms that match the professionalism.\n\n"
                "Our luxury 4-stall trailer has AC, running water, hardwood floors, and mirrors. "
                "Perfect for outdoor galas, company picnics, client events, and team retreats.\n\n"
                f"Flexible rental: day, weekend, or multi-day.\n"
                f"Serving {SERVICE_AREA}.\n\n"
                f"Get a quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1200, 1500),
        "tags": ["corporate", "gala", "professional"],
    },
    {
        "id": "corporate_outdoor_en",
        "niche": "corporate",
        "lang": "en",
        "titles": [
            "Outdoor Corporate Event? Luxury Restroom Trailer — SoCal",
            "Company Picnic Restroom Rental — Premium Portable Trailer",
            "Executive Restroom Trailer — Outdoor Business Events LA",
        ],
        "descriptions": [
            (
                "Taking the team outside? Make sure the facilities match.\n\n"
                "Our luxury restroom trailer is the go-to for corporate outdoor events. "
                "4 stalls, AC, running water, hardwood floors. Your clients and team will be impressed.\n\n"
                f"Delivery + setup + pickup included.\n"
                f"Serving {SERVICE_AREA}.\n\n"
                f"Call: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["corporate", "outdoor", "picnic"],
    },
    {
        "id": "corporate_es",
        "niche": "corporate",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Eventos Corporativos — SoCal",
            "Tráiler de Baño Premium para Empresas y Eventos",
        ],
        "descriptions": [
            (
                "Evento corporativo al aire libre? Tus clientes merecen lo mejor.\n\n"
                "Nuestro tráiler tiene 4 cabinas con AC, agua corriente, pisos de madera y espejos. "
                "Ideal para eventos de empresa, retiros y celebraciones.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Cotización: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1200, 1500),
        "tags": ["corporate", "spanish"],
    },

    # ── CONSTRUCTION ───────────────────────────────────────────────────
    {
        "id": "construction_residential_en",
        "niche": "construction",
        "lang": "en",
        "titles": [
            "Luxury Restroom Trailer — Home Renovations & Remodels",
            "Remodeling Your Home? Rent a Luxury Portable Restroom",
            "Premium Portable Restroom for Home Construction Projects",
        ],
        "descriptions": [
            (
                "Home remodel taking out your only bathroom? We've got you covered.\n\n"
                "Our luxury trailer has 4 stalls with AC, running water, and flushing toilets. "
                "Your family and contractors stay comfortable during the project.\n\n"
                f"Weekly and monthly rates available.\n"
                f"Serving {SERVICE_AREA}.\n\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (800, 1100),
        "tags": ["construction", "remodel", "residential"],
    },
    {
        "id": "construction_commercial_en",
        "niche": "construction",
        "lang": "en",
        "titles": [
            "Executive Restroom Trailer — Construction Sites SoCal",
            "Crew Comfort Restroom — Construction & Job Site Rental",
            "Luxury Portable Restroom for Construction — Weekly & Monthly",
        ],
        "descriptions": [
            (
                "Your crew deserves better than a standard porta potty.\n\n"
                "Our luxury 4-stall trailer has AC, running water, flushing toilets, and "
                "handwash stations. Happy crew = productive crew.\n\n"
                "Day, weekly, and monthly rental available.\n"
                f"Serving {SERVICE_AREA}.\n\n"
                f"Text or call: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["construction", "commercial", "crew"],
    },
    {
        "id": "construction_es",
        "niche": "construction",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Construcción — Renta Semanal y Mensual",
            "Tráiler de Baño para Sitios de Construcción — SoCal",
        ],
        "descriptions": [
            (
                "Tu equipo merece más que un baño portátil básico.\n\n"
                "Nuestro tráiler tiene 4 cabinas con AC, agua corriente y sanitarios. "
                "Disponible por día, semana o mes.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Cotización: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["construction", "spanish"],
    },

    # ── FESTIVALS & CONCERTS ───────────────────────────────────────────
    {
        "id": "festival_en",
        "niche": "festival",
        "lang": "en",
        "titles": [
            "VIP Restroom Trailer — Festivals & Concerts SoCal",
            "Luxury Portable Restroom for Festivals — Upgrade Your Event",
            "Festival Restroom Trailer Rental — VIP Experience LA",
        ],
        "descriptions": [
            (
                "VIP restrooms for your festival or concert.\n\n"
                "Our luxury trailer has 4 stalls with AC, running water, mirrors, and "
                "hardwood floors. Perfect for the VIP section or artist area.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Book now: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1200, 1600),
        "tags": ["festival", "concert", "vip"],
    },
    {
        "id": "festival_es",
        "niche": "festival",
        "lang": "es",
        "titles": [
            "Baño VIP para Festivales y Conciertos — SoCal",
            "Tráiler de Baño de Lujo para Eventos Musicales",
        ],
        "descriptions": [
            (
                "Baños VIP para tu festival o concierto.\n\n"
                "4 cabinas con AC, agua, espejos y pisos elegantes. "
                "Perfecto para la zona VIP o el área de artistas.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Reserva: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1200, 1600),
        "tags": ["festival", "concert", "spanish"],
    },

    # ── CHURCH & COMMUNITY ─────────────────────────────────────────────
    {
        "id": "church_en",
        "niche": "church",
        "lang": "en",
        "titles": [
            "Restroom Trailer for Church Events — Luxury Portable Rental",
            "Church Outdoor Event? Luxury Restroom Trailer — SoCal",
            "Premium Portable Restroom for Church Gatherings & Festivals",
        ],
        "descriptions": [
            (
                "Hosting an outdoor church event, fundraiser, or festival?\n\n"
                "Our luxury restroom trailer keeps your congregation comfortable. "
                "4 stalls, AC, running water, and clean hardwood floors.\n\n"
                "Affordable rental for community and religious events.\n"
                f"Serving {SERVICE_AREA}.\n\n"
                f"Call for pricing: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["church", "community", "nonprofit"],
    },
    {
        "id": "church_es",
        "niche": "church",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Eventos de Iglesia — Renta SoCal",
            "Tráiler de Baño para Eventos Comunitarios y de Iglesia",
        ],
        "descriptions": [
            (
                "¿Evento al aire libre para tu iglesia o comunidad?\n\n"
                "Nuestro tráiler de baño de lujo mantiene a todos cómodos. "
                "4 cabinas con AC, agua y pisos limpios.\n\n"
                "Precios accesibles para eventos religiosos y comunitarios.\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Llámanos: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["church", "community", "spanish"],
    },

    # ── SEASONAL / URGENCY ─────────────────────────────────────────────
    {
        "id": "spring_season_en",
        "niche": "seasonal",
        "lang": "en",
        "titles": [
            "Spring Event Season — Book Your Luxury Restroom Trailer Now",
            "Spring & Summer Events — Premium Restroom Trailer Rental",
            "Outdoor Event Season Is Here — Luxury Restroom Trailer SoCal",
        ],
        "descriptions": [
            (
                "Spring event season is here and dates are filling fast.\n\n"
                "Lock in your luxury restroom trailer now for weddings, graduations, "
                "quinceañeras, and outdoor celebrations.\n\n"
                "4 stalls • AC • Running water • Hardwood floors • Mirrors\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Book your date: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["seasonal", "spring", "urgency"],
    },
    {
        "id": "spring_season_es",
        "niche": "seasonal",
        "lang": "es",
        "titles": [
            "Temporada de Eventos — Reserva Tu Baño de Lujo Ahora",
            "Primavera y Verano — Tráiler de Baño Premium Disponible",
        ],
        "descriptions": [
            (
                "¡La temporada de eventos llegó y las fechas se están llenando!\n\n"
                "Reserva ahora tu tráiler de baño de lujo para bodas, graduaciones, "
                "quinceañeras y celebraciones.\n\n"
                "4 cabinas • AC • Agua • Pisos de madera • Espejos\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Reserva: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["seasonal", "spring", "spanish"],
    },
    {
        "id": "summer_heat_en",
        "niche": "seasonal",
        "lang": "en",
        "titles": [
            "Outdoor Event in the Heat? AC Restroom Trailer — SoCal",
            "Summer Event Restroom — Luxury Trailer with Air Conditioning",
            "Beat the Heat — AC Restroom Trailer Rental for Events",
        ],
        "descriptions": [
            (
                "Summer events in SoCal mean one thing: HEAT.\n\n"
                "Our luxury restroom trailer has full AC, so your guests can step inside "
                "and cool off. Plus running water, hardwood floors, and mirrors.\n\n"
                "Don't let your guests suffer in a hot plastic box.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1100, 1400),
        "tags": ["seasonal", "summer", "ac"],
    },

    # ── BLOCK PARTY ────────────────────────────────────────────────────
    {
        "id": "block_party_en",
        "niche": "community",
        "lang": "en",
        "titles": [
            "Block Party Restroom Trailer — Luxury Portable Rental",
            "Neighborhood Block Party? Rent a Luxury Restroom Trailer",
            "Community Event Restroom — Premium 4-Stall Trailer SoCal",
        ],
        "descriptions": [
            (
                "Throwing a block party or community event? Handle the restroom situation "
                "like a pro.\n\n"
                "Our luxury trailer has 4 stalls with AC, running water, and clean floors. "
                "Way better than sending everyone to the nearest gas station.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1100),
        "tags": ["community", "block_party"],
    },

    # ── FAMILY REUNION ─────────────────────────────────────────────────
    {
        "id": "reunion_en",
        "niche": "party",
        "lang": "en",
        "titles": [
            "Family Reunion Restroom Trailer — Luxury Portable Rental",
            "Big Family Gathering? Luxury Restroom Trailer — SoCal",
        ],
        "descriptions": [
            (
                "50+ family members and 2 bathrooms? Recipe for a bad time.\n\n"
                "Our luxury restroom trailer adds 4 private stalls with AC, running water, "
                "and mirrors. Drop it in the driveway and everyone stays happy.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Book now: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["party", "reunion", "family"],
    },
    {
        "id": "reunion_es",
        "niche": "party",
        "lang": "es",
        "titles": [
            "Baño de Lujo para Reunión Familiar — Alquiler SoCal",
            "¿Reunión Familiar Grande? Tráiler de Baño Premium",
        ],
        "descriptions": [
            (
                "¿50 familiares y solo 2 baños? Mejor no.\n\n"
                "Nuestro tráiler agrega 4 cabinas privadas con AC, agua y espejos. "
                "Se estaciona fácil y todos contentos.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Reserva: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (900, 1200),
        "tags": ["party", "reunion", "spanish"],
    },

    # ── GENERAL / PROBLEM-AWARE ────────────────────────────────────────
    {
        "id": "problem_porta_en",
        "niche": "general",
        "lang": "en",
        "titles": [
            "Tired of Gross Porta Potties? Try Our Luxury Restroom Trailer",
            "Don't Ruin Your Event with Porta Potties — Luxury Alternative",
            "The Luxury Alternative to Porta Potties — 4-Stall Trailer SoCal",
        ],
        "descriptions": [
            (
                "Let's be honest — nobody wants to use a porta potty.\n\n"
                "Our luxury restroom trailer is the alternative your event needs. "
                "4 private stalls, AC, running water, hardwood floors, full mirrors.\n\n"
                "Your guests will actually enjoy using the restroom. Yes, really.\n\n"
                f"Serving {SERVICE_AREA}.\n"
                f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1300),
        "tags": ["general", "problem_aware"],
    },
    {
        "id": "problem_porta_es",
        "niche": "general",
        "lang": "es",
        "titles": [
            "¿Cansado de los Baños Portátiles? Prueba Nuestro Tráiler de Lujo",
            "La Alternativa de Lujo a los Baños Portátiles — SoCal",
        ],
        "descriptions": [
            (
                "Seamos honestos — a nadie le gustan los baños portátiles.\n\n"
                "Nuestro tráiler es la alternativa. 4 cabinas privadas, AC, "
                "agua corriente, pisos de madera y espejos.\n\n"
                "Tus invitados realmente disfrutarán ir al baño.\n\n"
                f"Servimos {SERVICE_AREA_ES}.\n"
                f"Cotización: {BIZ_PHONE}\n{BIZ_WEBSITE}"
            ),
        ],
        "price_range": (1000, 1300),
        "tags": ["general", "problem_aware", "spanish"],
    },
]

# Total: 32 angles (20 EN + 12 ES)

# ═══════════════════════════════════════════════════════════════════════════
# FB PAGE ORGANIC POSTS — for auto-posting via fb_page.py
# ═══════════════════════════════════════════════════════════════════════════

FB_ORGANIC_POSTS = [
    {
        "id": "tip_outdoor_wedding",
        "message": (
            "Planning an outdoor wedding? Here are 3 things most couples forget:\n\n"
            "1️⃣ Guest restroom comfort — plastic porta potties can ruin the vibe\n"
            "2️⃣ Handwash stations — important for food-forward receptions\n"
            "3️⃣ Mirror access — guests will want to freshen up\n\n"
            "Our luxury trailer handles all three with AC, running water, "
            f"hardwood floors, and mirrors.\n\n{BIZ_WEBSITE}"
        ),
        "tags": ["wedding", "tips"],
    },
    {
        "id": "tip_construction",
        "message": (
            "Running a construction project? Your crew deserves better than a standard porta potty.\n\n"
            "Our luxury trailer has flushing toilets, running water, and AC. "
            "Happy crew = productive crew.\n\n"
            f"Weekly and monthly rates. Text us: {BIZ_PHONE}"
        ),
        "tags": ["construction", "tips"],
    },
    {
        "id": "bts_delivery",
        "message": (
            "Delivery day! Setting up our luxury restroom trailer for a gorgeous "
            "outdoor wedding in Ventura County. 🍇✨\n\n"
            "Most of our wedding clients say their guests were genuinely impressed "
            "by the restroom situation. That's what we aim for.\n\n"
            f"Book your date: {BIZ_PHONE}"
        ),
        "tags": ["bts", "wedding"],
    },
    {
        "id": "bts_setup_valley",
        "message": (
            "Another beautiful setup in the Valley! Our trailer tucked perfectly between "
            "the ceremony space and the reception tent.\n\n"
            "Pro tip: let us know your venue layout in advance and we'll find the perfect "
            "placement for maximum convenience.\n\n"
            f"{BIZ_WEBSITE}"
        ),
        "tags": ["bts", "setup"],
    },
    {
        "id": "seasonal_spring",
        "message": (
            "Spring wedding season is HERE 🌸\n\n"
            "Our calendar is filling up — if you're planning an outdoor event "
            "between March and June, now is the time to lock in your date.\n\n"
            "Weddings, quinceañeras, graduations, corporate events — "
            f"all across SoCal.\n\n"
            f"Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
        ),
        "tags": ["seasonal", "spring"],
    },
    {
        "id": "social_proof_wedding",
        "message": (
            "Every weekend, we get the same text from our wedding clients:\n\n"
            "\"OMG our guests kept talking about the bathroom trailer!\"\n\n"
            "AC, running water, hardwood floors, mirrors — your guests will think "
            "it's part of the venue. That's the goal.\n\n"
            f"Book your date: {BIZ_PHONE}\n{BIZ_WEBSITE}"
        ),
        "tags": ["social_proof", "wedding"],
    },
    {
        "id": "comparison_porta",
        "message": (
            "🚫 Porta Potty: No AC, no running water, no mirrors, no comfort\n"
            "✅ Our Trailer: AC, running water, hardwood floors, mirrors, Bluetooth speaker\n\n"
            "Same event. Totally different experience for your guests.\n\n"
            f"Serving all of SoCal: {SERVICE_AREA}.\n"
            f"Free quote: {BIZ_PHONE}"
        ),
        "tags": ["comparison", "general"],
    },
    {
        "id": "quince_post",
        "message": (
            "Quinceañera season is upon us! 🎀\n\n"
            "Make her special day even more memorable with a luxury restroom trailer "
            "your guests will love. 4 stalls, AC, mirrors, Bluetooth speaker.\n\n"
            f"Serving {SERVICE_AREA}.\n"
            f"Cotización gratis / Free quote: {BIZ_PHONE}\n{BIZ_WEBSITE}"
        ),
        "tags": ["quinceanera", "bilingual"],
    },
    {
        "id": "faq_capacity",
        "message": (
            "FAQ: How many guests can your restroom trailer handle?\n\n"
            "Our 4-stall trailer comfortably serves events of 50-300+ guests. "
            "At a typical wedding, the trailer handles the entire evening with ease.\n\n"
            "AC keeps everyone cool • Running water • Flushing toilets\n\n"
            f"Questions? Text us: {BIZ_PHONE}\n{BIZ_WEBSITE}"
        ),
        "tags": ["faq", "educational"],
    },
    {
        "id": "faq_delivery",
        "message": (
            "FAQ: What do you need for delivery?\n\n"
            "Just 3 things:\n"
            "1. Flat surface (driveway, parking lot, grass is fine)\n"
            "2. Water hookup within 100ft OR we bring our own tank\n"
            "3. Access for our truck + trailer\n\n"
            "We handle everything else — setup, leveling, and pickup.\n\n"
            f"Free quote: {BIZ_PHONE}"
        ),
        "tags": ["faq", "educational"],
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# DAILY BRIEFING ENGINE
# ═══════════════════════════════════════════════════════════════════════════

_running = False
_tg_notify = None


def init_daily_posting(notify_fn):
    """Pass in the Telegram notify function."""
    global _tg_notify
    _tg_notify = notify_fn
    print(f"[DailyPosting] Initialized with {len(LISTING_ANGLES)} listing angles "
          f"({sum(1 for a in LISTING_ANGLES if a['lang']=='en')} EN, "
          f"{sum(1 for a in LISTING_ANGLES if a['lang']=='es')} ES)")


async def run_daily_posting():
    """Background loop — sends 8 AM PT daily briefing."""
    global _running
    _running = True
    print("[DailyPosting] Background loop started")

    while _running:
        try:
            now_la = datetime.now(LA_TZ)

            # Send briefing at 8:00 AM PT (check within a 5-min window)
            if now_la.hour == 8 and now_la.minute < 5:
                await generate_and_send_briefing()
                # Sleep until next day to avoid duplicate
                await asyncio.sleep(3600)
            else:
                # Check every 60 seconds
                await asyncio.sleep(60)
        except Exception as e:
            print(f"[DailyPosting] Loop error: {e}")
            await asyncio.sleep(300)


def stop_daily_posting():
    global _running
    _running = False
    print("[DailyPosting] Stopped")


async def _send_posting_email(to_email: str, subject: str, body: str):
    """Send a single posting email via SMTP (internal operational message).

    These are self-to-self emails (zoarbathrooms@gmail.com -> zoarbathrooms@gmail.com)
    for copy-paste convenience. Since these are INTERNAL operational messages (not
    outbound lead communications), they bypass the SecurityGate messaging_mode check.

    Safety: Still runs DLP scan to prevent accidental secret leakage in post content.
    """
    try:
        import json as _json
        import smtplib
        from email.mime.text import MIMEText
        from core.security import SecurityGate

        # DLP scan only — catch any secrets that might have leaked into post content
        gate = SecurityGate()
        violations = gate.scan_for_secrets(body)
        if violations:
            print(f"[DailyPosting] Email blocked — DLP violations in post content: {violations}")
            return False

        # Load SMTP config
        config_path = Path.home() / ".nexus" / "config.json"
        cfg = _json.loads(config_path.read_text()) if config_path.exists() else {}
        gmail = cfg.get("gmail_address", "zoarbathrooms@gmail.com")
        app_pw = cfg.get("gmail_app_password", "")

        if not app_pw:
            print("[DailyPosting] Email skipped — no gmail_app_password configured")
            return False

        # Verify recipient is the business email (safety check)
        if to_email.lower() != "zoarbathrooms@gmail.com":
            print(f"[DailyPosting] Email blocked — posting emails only go to zoarbathrooms@gmail.com, not {to_email}")
            return False

        # Build plain-text-only MIME message
        msg = MIMEText(body, "plain", "utf-8")
        msg["From"] = f"Nexus Posting <{gmail}>"
        msg["To"] = to_email
        msg["Subject"] = subject

        # Send in executor to avoid blocking async loop
        def _send():
            server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
            try:
                server.login(gmail, app_pw)
                server.sendmail(gmail, to_email, msg.as_string())
            finally:
                server.quit()

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send)
        print(f"[DailyPosting] Email sent: {subject}")
        return True

    except Exception as e:
        print(f"[DailyPosting] Email error: {e}")
        return False


def _format_email_for_post(post: dict, date_str: str) -> tuple[str, str]:
    """Format a single post into (subject, body) for email delivery.

    Returns plain-text email optimized for mobile copy-paste:
    - Each section on its own line with blank lines between
    - No emoji in copyable content
    - Clean text ready to paste directly into each platform
    """
    platform = post["platform"]
    language = post["language"]
    angle_name = post.get("angle_name", "")
    title = post.get("title", "")
    body_text = post.get("body", "")
    photo_set = post.get("photo_set", "?")

    lang_label = "English" if language == "en" else "Espanol"

    # Subject line (emoji OK here for inbox scanning)
    platform_subjects = {
        "fb_marketplace": f"\U0001f4f1 FB Marketplace — {lang_label} | {date_str} | {angle_name}",
        "craigslist": f"\U0001f4cb Craigslist — {lang_label} | {date_str} | {angle_name}",
        "instagram": f"\U0001f4f8 Instagram Post | {date_str} | {angle_name}",
        "fb_page": f"\U0001f4d8 Facebook Page Post | {date_str} | {angle_name}",
    }
    subject = platform_subjects.get(platform, f"Posting — {platform} | {date_str}")

    # Body: clean plain text, section per line, easy to select on mobile
    if platform == "fb_marketplace":
        email_body = f"""Title:
{title}

Price:
$999

Category:
Home Services

Description:
{body_text}

Location:
San Fernando Valley, CA

Photos:
Use photo set {photo_set}
"""

    elif platform == "craigslist":
        # Strip HTML from CL body for email (the raw description is cleaner)
        import re
        clean_body = re.sub(r"<[^>]+>", "", body_text)
        clean_body = clean_body.replace("&gt;", ">").replace("&amp;", "&")
        email_body = f"""Title:
{title}

Category:
Services > Event Services

Description:
{clean_body}

Location:
San Fernando Valley, CA

Photos:
Use photo set {photo_set}
"""

    elif platform == "instagram":
        email_body = f"""Caption:
{body_text}

Photo:
Use lead photo from set {photo_set}

Suggested posting time: 11:00 AM or 6:00 PM PT
"""

    elif platform == "fb_page":
        email_body = f"""Post text:
{body_text}

Photo:
Use lead photo from set {photo_set}

Link: zoarbathroomrental.com
"""

    else:
        email_body = f"Title: {title}\n\n{body_text}\n\nPhotos: set {photo_set}\n"

    return subject, email_body.strip()


async def send_posting_emails(posts: list[dict] = None) -> dict:
    """Generate and send 6 daily posting emails (one per platform).

    Sends to zoarbathrooms@gmail.com with 5-second delays between emails.
    Returns summary dict with sent count and any errors.

    If `posts` is None, generates fresh posts via PostingEngine.
    """
    to_email = "zoarbathrooms@gmail.com"
    now_la = datetime.now(LA_TZ)
    date_str = now_la.strftime("%B %d, %Y")

    if posts is None:
        from core.posting_engine import PostingEngine
        engine = PostingEngine()
        posts = engine.generate_all_daily_posts()

    if not posts:
        return {"ok": False, "error": "No posts generated", "sent": 0}

    sent = 0
    errors = []

    for i, post in enumerate(posts):
        subject, body = _format_email_for_post(post, date_str)
        success = await _send_posting_email(to_email, subject, body)
        if success:
            sent += 1
        else:
            errors.append(f"{post['platform']}_{post['language']}")

        # Small delay between emails to avoid rate limiting
        if i < len(posts) - 1:
            await asyncio.sleep(5)

    # Send Slack summary notification
    try:
        from integrations.slack_notify import send_slack
        platform_labels = {
            "fb_marketplace": "FB Marketplace",
            "craigslist": "Craigslist",
            "instagram": "Instagram Post",
            "fb_page": "Facebook Page Post",
        }
        summary_lines = [f"Daily posting emails sent at {now_la.strftime('%I:%M %p PT')}"]
        for post in posts:
            label = platform_labels.get(post["platform"], post["platform"])
            lang = f" {post['language'].upper()}" if post["platform"] in ("fb_marketplace", "craigslist") else ""
            summary_lines.append(f"- {label}{lang} sent")
        summary_lines.append(f"\nCheck {to_email}")
        await send_slack("\n".join(summary_lines), channel="posting")
    except Exception as e:
        print(f"[DailyPosting] Slack summary error: {e}")

    return {"ok": sent > 0, "sent": sent, "total": len(posts), "errors": errors}


async def generate_and_send_briefing(date: datetime = None):
    """Generate today's posting briefing and deliver via EMAIL.

    Sends 6 separate emails to zoarbathrooms@gmail.com (one per platform)
    for easy mobile copy-paste. Telegram only gets a confirmation summary.
    """
    if date is None:
        date = datetime.now(LA_TZ)

    date_str = date.strftime("%B %d, %Y")

    # Generate all 6 posts via PostingEngine
    from core.posting_engine import PostingEngine
    engine = PostingEngine()
    posts = engine.generate_all_daily_posts()

    # Send via email (one per platform, staggered)
    email_result = await send_posting_emails(posts)

    # Auto-post to FB Page (if enabled in config and not Sunday)
    fb_post_result = None
    try:
        import json as _json
        _cfg = _json.loads((Path.home() / ".nexus" / "config.json").read_text())
        auto_post_enabled = _cfg.get("auto_post_fb_page", False)
        is_sunday = date.weekday() == 6

        if auto_post_enabled and not is_sunday:
            # Find the fb_page post from today's generated posts
            fb_post = next((p for p in posts if p["platform"] == "fb_page"), None)
            if fb_post:
                fb_post_dict = {"message": fb_post["body"], "id": fb_post.get("angle_id", "fb_organic")}
                fb_post_result = await auto_post_to_fb_page(fb_post_dict)
                if fb_post_result and fb_post_result.get("ok"):
                    print(f"[DailyPosting] Auto-posted to FB Page: {fb_post_result.get('post_id', '')}")
                else:
                    error = fb_post_result.get("error", "unknown") if fb_post_result else "no result"
                    print(f"[DailyPosting] FB auto-post failed: {error}")
    except Exception as e:
        print(f"[DailyPosting] FB auto-post error: {e}")

    # Telegram gets a SHORT confirmation only (not the full content)
    if _tg_notify:
        tg_summary = f"Daily posting emails sent — {email_result['sent']}/{email_result['total']} delivered\n"
        tg_summary += f"Check zoarbathrooms@gmail.com for copy-paste content"
        if fb_post_result and fb_post_result.get("ok"):
            tg_summary += f"\nFB Page auto-posted (ID: {fb_post_result.get('post_id', 'N/A')})"
        if email_result.get("errors"):
            tg_summary += f"\nFailed: {', '.join(email_result['errors'])}"
        await _tg_notify(tg_summary)

    # Record briefing
    _record_briefing(date_str, len(posts))

    return {
        "date": date_str,
        "posts_generated": len(posts),
        "emails_sent": email_result["sent"],
        "fb_auto_posted": bool(fb_post_result and fb_post_result.get("ok")),
        "delivery": "email",
    }


def _format_separate_messages(date_str: str, day_name: str, listings: list, fb_post: Optional[dict]) -> list[str]:
    """Format posting package as separate messages, one per platform.
    Returns list of message strings to send sequentially."""
    messages = []

    # ── Message 1: Pipeline Status ──
    pipeline_msg = f"📊 MORNING STATUS — {day_name}, {date_str}\n━━━━━━━━━━━━━━━━━━\n"
    try:
        conn = sqlite3.connect(str(DB_PATH))
        active = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status NOT IN ('closed','opted_out')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
        ).fetchone()[0]
        followups = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE next_action_at <= datetime('now') AND status NOT IN ('closed','opted_out')"
        ).fetchone()[0]
        today_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= date('now')"
        ).fetchone()[0]
        conn.close()
        pipeline_msg += (
            f"• Active leads: {active} | Pending approvals: {pending}\n"
            f"• Follow-ups due today: {followups}\n"
            f"• New leads today: {today_leads}\n"
        )
    except Exception:
        pipeline_msg += "• Pipeline data unavailable\n"
    messages.append(pipeline_msg)

    # ── Messages 2-5: One per listing platform ──
    CL_REGIONS = ["Los Angeles", "San Fernando Valley", "Ventura County", "Santa Barbara", "Orange County"]
    day_of_year = datetime.now().timetuple().tm_yday
    todays_region = CL_REGIONS[day_of_year % len(CL_REGIONS)]

    listing_num = 0
    for listing in listings:
        listing_num += 1
        lang_flag = "🇺🇸" if listing["lang"] == "en" else "🇲🇽"
        lang_label = "ENGLISH" if listing["lang"] == "en" else "ESPAÑOL"

        if listing["platform"] == "fb_marketplace":
            header = f"FB MARKETPLACE — {lang_label}"
        else:
            header = f"CRAIGSLIST — {lang_label}"

        msg = f"{listing_num}️⃣ {header} {lang_flag}\n"
        msg += f"📌 Title: {listing['title']}\n"
        msg += f"💰 Price: $999\n"

        if listing["platform"] == "craigslist":
            msg += f"📂 Category: Services > Event Services\n"
            msg += f"📍 Region: {todays_region}\n"

        msg += f"\n📝 COPY/PASTE:\n{listing['description']}"
        messages.append(msg)

    # ── Message 6: Auto-post confirmations ──
    auto_msg = "✅ AUTO-POSTING TODAY:\n━━━━━━━━━━━━━━━━━━\n"
    if fb_post:
        auto_msg += f"• Facebook Page — \"{fb_post['message'][:100]}...\"\n"
        auto_msg += f"  Tags: {', '.join(fb_post.get('tags', []))}\n"
    try:
        from integrations.instagram_api import generate_ig_post_preview
        ig_preview = generate_ig_post_preview()
        if ig_preview:
            auto_msg += f"• Instagram Feed — \"{ig_preview.get('caption', '')[:100]}...\"\n"
            auto_msg += f"  Hashtag Set: {ig_preview.get('hashtag_set', '?')}\n"
    except Exception:
        pass
    auto_msg += "\nReply \"auto\" to post to FB Page\nReply \"hold\" to pause either one"
    messages.append(auto_msg)

    return messages


def _select_marketplace_listings(day_of_week: int) -> list:
    """Select 4 marketplace listings: FB Marketplace EN + ES, Craigslist EN + ES.
    On weekends, reduced to 2 (1 EN, 1 ES on FB Marketplace only)."""

    # Get recently used angle IDs (last 14 days) to avoid repetition
    used_ids = _get_recently_used_angles(14)

    # Build candidate pools by language
    all_candidates = [a for a in LISTING_ANGLES if a["id"] not in used_ids]
    if len(all_candidates) < 4:
        all_candidates = list(LISTING_ANGLES)

    en_pool = [a for a in all_candidates if a["lang"] == "en"]
    es_pool = [a for a in all_candidates if a["lang"] == "es"]

    result = []

    def _pick_and_generate(pool, platform):
        """Pick a random angle from pool, generate listing for given platform."""
        if not pool:
            return None
        pick = random.choice(pool)
        listing = _generate_from_angle(pick)
        listing["platform"] = platform
        # Remove from pools to avoid duplication
        for p in [en_pool, es_pool, all_candidates]:
            try:
                p[:] = [c for c in p if c["id"] != pick["id"]]
            except Exception:
                pass
        return listing

    if day_of_week in (5, 6):  # Weekend: 2 listings (lighter posting)
        fb_en = _pick_and_generate(en_pool, "fb_marketplace")
        if fb_en:
            result.append(fb_en)
        fb_es = _pick_and_generate(es_pool, "fb_marketplace")
        if fb_es:
            result.append(fb_es)
    else:  # Weekday: 4 listings (full posting day)
        # 1. FB Marketplace — English
        fb_en = _pick_and_generate(en_pool, "fb_marketplace")
        if fb_en:
            result.append(fb_en)
        # 2. FB Marketplace — Spanish
        fb_es = _pick_and_generate(es_pool, "fb_marketplace")
        if fb_es:
            result.append(fb_es)
        # 3. Craigslist — English
        cl_en = _pick_and_generate(en_pool, "craigslist")
        if cl_en:
            result.append(cl_en)
        # 4. Craigslist — Spanish
        cl_es = _pick_and_generate(es_pool, "craigslist")
        if cl_es:
            result.append(cl_es)

    return result


def _generate_from_angle(angle: dict) -> dict:
    """Generate a complete listing from an angle template."""
    title = random.choice(angle["titles"])
    description = random.choice(angle["descriptions"])
    price = 999  # Always $999 — never show ranges, no negotiation language
    images = random.choice(IMAGE_SETS).copy()
    random.shuffle(images)

    # Assign platform: alternate between FB Marketplace and Craigslist
    platform = random.choice(["fb_marketplace", "craigslist"])

    return {
        "angle_id": angle["id"],
        "niche": angle["niche"],
        "lang": angle["lang"],
        "platform": platform,
        "title": title,
        "description": description,
        "price": price,
        "images": images,
        "tags": angle["tags"],
    }


def _select_fb_page_post() -> Optional[dict]:
    """Select an organic FB Page post for today."""
    # Get recently posted IDs to avoid repetition
    used_ids = _get_recently_used_fb_posts(30)
    candidates = [p for p in FB_ORGANIC_POSTS if p["id"] not in used_ids]

    if not candidates:
        candidates = list(FB_ORGANIC_POSTS)

    return random.choice(candidates) if candidates else None


def _format_briefing(date_str: str, day_name: str, listings: list, fb_post: Optional[dict]) -> str:
    """Format the consolidated daily posting package for Telegram.
    Matches the Master Prompt v2 briefing format."""

    # Pipeline status header
    pipeline_status = ""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        active = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status NOT IN ('closed','opted_out')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
        ).fetchone()[0]
        today_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= date('now')"
        ).fetchone()[0]
        conn.close()
        pipeline_status = (
            f"📊 PIPELINE STATUS:\n"
            f"• Active leads: {active}\n"
            f"• Pending approvals: {pending}\n"
            f"• New today: {today_leads}\n"
        )
    except Exception:
        pipeline_status = "📊 PIPELINE STATUS: unavailable\n"

    # Craigslist region rotation (5 regions, rotate by day of year)
    CL_REGIONS = ["Los Angeles", "San Fernando Valley", "Ventura County", "Santa Barbara", "Orange County"]
    day_of_year = datetime.now().timetuple().tm_yday
    todays_region = CL_REGIONS[day_of_year % len(CL_REGIONS)]

    lines = [
        f"☀️ GOOD MORNING KAI — DAILY POSTING PACKAGE",
        "━" * 34,
        "",
        pipeline_status,
        "━" * 34,
        "",
    ]

    # Number listings per Master Prompt format
    listing_num = 0
    for listing in listings:
        listing_num += 1
        lang_flag = "🇺🇸" if listing["lang"] == "en" else "🇲🇽"
        lang_label = "ENGLISH" if listing["lang"] == "en" else "ESPAÑOL"

        if listing["platform"] == "fb_marketplace":
            header = f"FB MARKETPLACE — {lang_label}"
        else:
            header = f"CRAIGSLIST — {lang_label}"

        lines.append(f"{listing_num}️⃣ {header} {lang_flag}")
        lines.append(f"📌 Title: {listing['title']}")
        lines.append(f"💰 Price: $999")

        if listing["platform"] == "craigslist":
            lines.append(f"📂 Category: Services > Event Services")
            lines.append(f"📍 Region: {todays_region}")

        lines.append(f"📝 COPY/PASTE:")
        lines.append(f"{listing['description']}")
        lines.append("")

    # FB Page organic post
    if fb_post:
        listing_num += 1
        lines.append("━" * 34)
        lines.append(f"{listing_num}️⃣ FACEBOOK PAGE (reply \"auto\" to post)")
        lines.append(f"📸 📝 {fb_post['message']}")
        lines.append(f"Tags: {', '.join(fb_post.get('tags', []))}")
        lines.append("")

    # Footer
    lines.append("━" * 34)
    lines.append("• Reply \"auto\" → auto-post FB Page")
    lines.append("• Copy/paste listings to Marketplace & Craigslist")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# DB TRACKING
# ═══════════════════════════════════════════════════════════════════════════

def _init_posting_tables():
    """Ensure posting tracking tables exist."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_posting_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            angle_id TEXT,
            platform TEXT,
            lang TEXT,
            niche TEXT,
            action TEXT DEFAULT 'generated',
            posted_at TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_briefing_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            listing_count INTEGER,
            sent_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def _get_recently_used_angles(days: int) -> set:
    """Get angle IDs used in the last N days."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT DISTINCT angle_id FROM daily_posting_log WHERE created_at > ?",
            (cutoff,)
        ).fetchall()
        conn.close()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return set()


def _get_recently_used_fb_posts(days: int) -> set:
    """Get FB page post IDs used in the last N days."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT DISTINCT angle_id FROM daily_posting_log "
            "WHERE platform = 'fb_page' AND created_at > ?",
            (cutoff,)
        ).fetchall()
        conn.close()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return set()


def _record_briefing(date_str: str, listing_count: int):
    """Record that today's briefing was sent."""
    try:
        _init_posting_tables()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO daily_briefing_log (date, listing_count) VALUES (?, ?)",
            (date_str, listing_count)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DailyPosting] DB error recording briefing: {e}")


def record_angle_used(angle_id: str, platform: str, lang: str = "", niche: str = ""):
    """Record that a listing angle was used in a briefing."""
    try:
        _init_posting_tables()
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT INTO daily_posting_log (date, angle_id, platform, lang, niche) "
            "VALUES (date('now'), ?, ?, ?, ?)",
            (angle_id, platform, lang, niche)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DailyPosting] DB error recording angle: {e}")


def record_posted(angle_id: str, platform: str):
    """Record that a listing was actually posted (Kai confirmed)."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "UPDATE daily_posting_log SET action = 'posted', posted_at = datetime('now') "
            "WHERE angle_id = ? AND platform = ? AND action = 'generated' "
            "ORDER BY created_at DESC LIMIT 1",
            (angle_id, platform)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DailyPosting] DB error recording post: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# ON-DEMAND GENERATION
# ═══════════════════════════════════════════════════════════════════════════

async def generate_listing_now(
    niche: str = "",
    lang: str = "en",
    platform: str = "fb_marketplace",
) -> dict:
    """Generate a single listing on demand. For manual requests."""
    candidates = LISTING_ANGLES
    if niche:
        candidates = [a for a in candidates if a["niche"] == niche]
    if lang:
        candidates = [a for a in candidates if a["lang"] == lang]

    if not candidates:
        return {"error": f"No angles found for niche={niche}, lang={lang}"}

    angle = random.choice(candidates)
    listing = _generate_from_angle(angle)
    listing["platform"] = platform

    # Record usage
    record_angle_used(angle["id"], platform, angle["lang"], angle["niche"])

    return listing


def get_all_niches() -> list:
    """Get all unique niches."""
    return sorted(set(a["niche"] for a in LISTING_ANGLES))


def get_angle_count() -> dict:
    """Get count of angles by niche and language."""
    stats = {}
    for a in LISTING_ANGLES:
        key = f"{a['niche']}_{a['lang']}"
        stats[key] = stats.get(key, 0) + 1
    return {
        "total": len(LISTING_ANGLES),
        "en": sum(1 for a in LISTING_ANGLES if a["lang"] == "en"),
        "es": sum(1 for a in LISTING_ANGLES if a["lang"] == "es"),
        "by_niche_lang": stats,
        "niches": get_all_niches(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# FB PAGE AUTO-POSTING (P4)
# ═══════════════════════════════════════════════════════════════════════════

# Stores today's selected FB page post for "auto" command
_todays_fb_post: Optional[dict] = None


def set_todays_fb_post(post: dict):
    """Store today's FB page post so it can be auto-posted on command."""
    global _todays_fb_post
    _todays_fb_post = post


def get_todays_fb_post() -> Optional[dict]:
    """Get today's pending FB page post."""
    return _todays_fb_post


async def auto_post_to_fb_page(post: dict = None) -> dict:
    """Auto-post the selected content to the Facebook Page via Graph API.

    Returns {"ok": True, "post_id": "..."} on success.
    """
    global _todays_fb_post

    if post is None:
        post = _todays_fb_post

    if not post:
        return {"ok": False, "error": "No FB page post queued for today"}

    try:
        from integrations.fb_page import create_post
        result = await create_post(post["message"])

        post_id = result.get("id", "")
        if post_id:
            # Record in posting log
            record_angle_used(post.get("id", "fb_organic"), "fb_page")
            record_posted(post.get("id", "fb_organic"), "fb_page")

            # Notify via Telegram
            if _tg_notify:
                await _tg_notify(
                    f"✅ FB Page post published!\n"
                    f"Post ID: {post_id}\n"
                    f"Preview: {post['message'][:100]}..."
                )

            # Clear today's pending post
            _todays_fb_post = None

            return {"ok": True, "post_id": post_id}
        else:
            error = result.get("error", {}).get("message", str(result))
            return {"ok": False, "error": f"FB API error: {error}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# Initialize tables on import
try:
    _init_posting_tables()
except Exception:
    pass
