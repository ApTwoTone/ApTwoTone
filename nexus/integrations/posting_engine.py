"""
Posting Engine — Comprehensive daily posting with angle rotation, photo
management, and multi-platform listing generation.

Generates marketplace listings (FB Marketplace, Craigslist) and social media
posts (Instagram, FB Page) across 32 unique niche angles in English & Spanish.
Manages photo rotation, hashtag sets, and a strict daily schedule.

Database: ~/.nexus/memory.db (WAL mode)
Config:   ~/.nexus/config.json
Schedule: 7:55 AM - 8:20 AM PT daily
"""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

# ── Constants ────────────────────────────────────────────────────────────────

DB_PATH = Path.home() / ".nexus" / "memory.db"
PT = ZoneInfo("America/Los_Angeles")

BIZ_PHONE = "(424) 235-8979"
BIZ_EMAIL = "zoarbathrooms@gmail.com"
BIZ_WEBSITE = "zoarbathroomrental.com"
BIZ_NAME = "Zoar Bathroom Rental"
PRICE = "$1,100"
PRICE_NUM = 1100

SERVICE_AREA_EN = (
    "Los Angeles, San Fernando Valley, Ventura, Santa Clarita, Malibu, "
    "and all of Southern California"
)
SERVICE_AREA_ES = (
    "Los Angeles, San Fernando Valley, Ventura, Santa Clarita, Malibu "
    "y todo el sur de California"
)

FEATURES_EN = (
    "4 private stalls, air conditioning, LED lighting, chrome fixtures, "
    "running water, flushing toilets, Bluetooth speakers, vanity mirrors, "
    "and premium hand soap"
)
FEATURES_ES = (
    "4 cabinas privadas, aire acondicionado, iluminacion LED, griferia cromada, "
    "agua corriente, sanitarios con descarga, bocinas Bluetooth, espejos de tocador "
    "y jabon premium"
)

CL_REGIONS = ["Los Angeles", "SFV", "Ventura", "Santa Barbara", "Orange County"]

PHOTO_DIR = Path.home() / ".nexus" / "photos"
DEFAULT_PHOTOS = [
    "web-000.jpg", "web-002.jpg", "web-004.jpg", "web-006.jpg",
    "web-010.jpg", "web-014.jpg", "web-024.jpg", "web-030.jpg",
    "web-032.jpg", "web-036.jpg",
]

# ── Instagram hashtag sets (4 rotations) ─────────────────────────────────────

HASHTAG_SETS = {
    "A": [  # Wedding focused
        "#WeddingRestroom", "#LuxuryWedding", "#WeddingPlanning",
        "#OutdoorWedding", "#WeddingVenue", "#SoCalWedding",
        "#LAWedding", "#RestroomTrailer", "#WeddingDay",
        "#WeddingDetails", "#BrideTooBe", "#WeddingInspo",
        "#LuxuryRental", "#WeddingRentals", "#EventRentals",
        "#VenueDecor", "#WeddingComfort", "#GuestExperience",
        "#WeddingSeason", "#CaliforniaWedding",
    ],
    "B": [  # Event planning focused
        "#EventPlanning", "#EventPlanner", "#PartyPlanning",
        "#EventRentals", "#OutdoorEvent", "#EventDesign",
        "#SoCalEvents", "#LAEvents", "#EventProfs",
        "#CorporateEvents", "#SpecialEvents", "#LuxuryEvents",
        "#RestroomTrailer", "#PortableRestroom", "#LuxuryRestroom",
        "#EventLife", "#PartyRentals", "#VenueSetup",
        "#EventDay", "#EventManager",
    ],
    "C": [  # Quinceanera / Latino market focused
        "#Quinceanera", "#Quince", "#MisQuince", "#QuinceaneraPlanning",
        "#FiestaLatina", "#EventosLatinos", "#BodaLatina",
        "#CelebrandoEnGrande", "#FiestaEnCasa", "#EventosMexicanos",
        "#LatinoEvents", "#SoCalLatino", "#LALatino",
        "#BanosDeLujo", "#RentaDeBanos", "#FiestasFamiliares",
        "#TrailerDeBano", "#EventoEspecial", "#CelebracionLatina",
        "#FiestaMexicana",
    ],
    "D": [  # Local area focused
        "#LosAngeles", "#SanFernandoValley", "#SFV", "#Ventura",
        "#SantaClarita", "#Malibu", "#Calabasas", "#WoodlandHills",
        "#ShermanOaks", "#Encino", "#Tarzana", "#Burbank",
        "#Pasadena", "#ThousandOaks", "#SimiValley",
        "#SoCalRentals", "#LACounty", "#VenturaCounty",
        "#SoCalBusiness", "#LocalBusiness",
    ],
}

# ── 32 Seed Angles ───────────────────────────────────────────────────────────
# Each tuple: (angle_number, niche, title_en, title_es, description_en, description_es)

SEED_ANGLES: list[tuple[int, str, str, str, str, str]] = [
    # 1 - Wedding
    (
        1, "wedding",
        "Luxury Restroom Trailer for Your Dream Wedding",
        "Trailer de Bano de Lujo para la Boda de tus Suenos",
        (
            "Your wedding guests deserve better than a plastic porta potty.\n\n"
            f"Our luxury restroom trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Tus invitados merecen algo mejor que un bano portatil de plastico.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 2 - Quinceanera
    (
        2, "quinceanera",
        "Make Her Quinceanera Unforgettable -- Luxury Restroom Trailer",
        "Haz su Quinceanera Inolvidable -- Trailer de Bano de Lujo",
        (
            "Her special day deserves every detail to be perfect -- including the restrooms.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Su dia especial merece que cada detalle sea perfecto -- incluyendo los banos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 3 - Corporate Event
    (
        3, "corporate",
        "Executive Restroom Trailer for Corporate Events",
        "Trailer de Bano Ejecutivo para Eventos Corporativos",
        (
            "Impress clients and keep your team comfortable at your next corporate event.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Impresione a sus clientes y mantenga comodo a su equipo en su proximo evento corporativo.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 4 - Birthday Party
    (
        4, "birthday",
        "Big Birthday Party? Luxury Restroom Trailer Rental",
        "Fiesta de Cumpleanos Grande? Renta de Bano de Lujo",
        (
            "Throwing a big birthday bash in the backyard? Stop worrying about your one bathroom.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Fiesta grande en el patio y solo un bano en la casa? Nosotros te resolvemos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 5 - Graduation
    (
        5, "graduation",
        "Graduation Party Restroom Trailer -- Premium Portable Rental",
        "Fiesta de Graduacion -- Trailer de Bano Premium",
        (
            "Celebrate their achievement with a party your guests will remember.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Celebra su logro con una fiesta que tus invitados recordaran.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 6 - Church Event
    (
        6, "church",
        "Restroom Trailer for Church Events and Gatherings",
        "Trailer de Bano para Eventos de Iglesia",
        (
            "Hosting an outdoor church event, retreat, or community gathering?\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Evento al aire libre de su iglesia, retiro o reunion comunitaria?\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 7 - School Function
    (
        7, "school",
        "Luxury Restroom Trailer for School Events and Functions",
        "Trailer de Bano de Lujo para Eventos Escolares",
        (
            "School carnival, field day, or outdoor ceremony? Give everyone clean, comfortable restrooms.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Feria escolar, dia de campo o ceremonia al aire libre? Ofrezca banos limpios y comodos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 8 - Sports Tournament
    (
        8, "sports",
        "Premium Restroom Trailer for Sports Tournaments",
        "Trailer de Bano Premium para Torneos Deportivos",
        (
            "Running a tournament, league game day, or sports fundraiser? Keep families comfortable.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Torneo, dia de liga o evento deportivo de recaudacion? Mantenga a las familias comodas.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 9 - Construction Site
    (
        9, "construction",
        "Luxury Restroom Trailer for Construction Sites",
        "Trailer de Bano de Lujo para Sitios de Construccion",
        (
            "Your crew deserves better than a standard porta potty. Boost morale and productivity.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n"
            f"Weekly and monthly rates available.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Tu equipo merece algo mejor que un bano portatil basico. Mejora la moral y productividad.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n"
            f"Tarifas semanales y mensuales disponibles.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 10 - Film Production
    (
        10, "film",
        "Restroom Trailer for Film and TV Productions",
        "Trailer de Bano para Producciones de Cine y Television",
        (
            "Keep your cast and crew comfortable on location. Industry-standard luxury facilities.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Mantenga comodos a su elenco y equipo en locacion. Instalaciones de lujo de nivel profesional.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 11 - Outdoor Festival
    (
        11, "festival",
        "VIP Restroom Trailer for Outdoor Festivals",
        "Trailer de Bano VIP para Festivales al Aire Libre",
        (
            "Elevate your festival with VIP-level restrooms. No more port-a-potty complaints.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Eleve su festival con banos nivel VIP. No mas quejas de banos portatiles.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 12 - Vineyard Wedding
    (
        12, "vineyard_wedding",
        "Luxury Restroom Trailer for Vineyard Weddings",
        "Trailer de Bano de Lujo para Bodas en Vinedos",
        (
            "Vineyard venue, five-star restrooms. Your guests will think it is part of the property.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Vinedo como escenario, banos de cinco estrellas. Sus invitados pensaran que es parte del lugar.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 13 - Ranch Event
    (
        13, "ranch",
        "Restroom Trailer for Ranch Events and Country Venues",
        "Trailer de Bano para Eventos en Ranchos",
        (
            "Rustic venue, luxury restrooms. The combo your guests will love.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Lugar rustico, banos de lujo. La combinacion que a tus invitados les va a encantar.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 14 - Beach Event
    (
        14, "beach",
        "Luxury Restroom Trailer for Beach Events and Weddings",
        "Trailer de Bano de Lujo para Eventos en la Playa",
        (
            "Sand and porta potties do not mix. Give your beach event the restrooms it deserves.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"We deliver from Malibu to Santa Barbara and everywhere in between.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Arena y banos portatiles no combinan. Dale a tu evento en la playa los banos que merece.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Entregamos desde Malibu hasta Santa Barbara y todo en medio.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 15 - Backyard Party
    (
        15, "backyard",
        "Backyard Party? Luxury Restroom Trailer Rental",
        "Fiesta en el Patio? Renta de Trailer de Bano de Lujo",
        (
            "Big party, small house? Our luxury restroom trailer handles the crowd.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fits in your driveway or side yard.\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Fiesta grande, casa pequena? Nuestro trailer de bano resuelve eso.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Cabe en tu entrada o al lado de tu casa.\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 16 - Holiday Party
    (
        16, "holiday",
        "Holiday Party Restroom Trailer -- Luxury Portable Rental",
        "Fiesta Navidena -- Trailer de Bano de Lujo Portatil",
        (
            "Holiday party season is here. Make sure your guests stay comfortable all night.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Temporada de fiestas decembrinas. Que tus invitados esten comodos toda la noche.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 17 - Fundraiser Gala
    (
        17, "fundraiser",
        "Luxury Restroom Trailer for Fundraiser Galas",
        "Trailer de Bano de Lujo para Galas Beneficas",
        (
            "Your fundraiser gala deserves restrooms that match the elegance of the event.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Su gala benefica merece banos a la altura de la elegancia del evento.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 18 - Family Reunion
    (
        18, "reunion",
        "Family Reunion? Luxury Restroom Trailer Rental",
        "Reunion Familiar? Renta de Trailer de Bano de Lujo",
        (
            "50 plus family members and only 2 bathrooms? We have you covered.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Mas de 50 familiares y solo 2 banos? Nosotros te resolvemos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 19 - Baby Shower
    (
        19, "baby_shower",
        "Luxury Restroom Trailer for Baby Showers",
        "Trailer de Bano de Lujo para Baby Showers",
        (
            "Hosting a baby shower at home? Keep your guests comfortable with luxury restrooms.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Baby shower en casa? Manten comodos a tus invitados con banos de lujo.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 20 - Bridal Shower
    (
        20, "bridal_shower",
        "Bridal Shower Restroom Trailer -- Elegant Portable Rental",
        "Despedida de Soltera -- Trailer de Bano Elegante",
        (
            "Give the bride-to-be the elegant experience she deserves, down to the last detail.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Dale a la futura novia la experiencia elegante que se merece, hasta el ultimo detalle.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 21 - Retirement Party
    (
        21, "retirement",
        "Retirement Party Restroom Trailer -- Celebrate in Style",
        "Fiesta de Jubilacion -- Celebra con Estilo",
        (
            "They have worked hard. Celebrate their retirement with an event that shines.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Trabajo duro toda su vida. Celebre su jubilacion con un evento que brille.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 22 - Anniversary
    (
        22, "anniversary",
        "Anniversary Celebration? Luxury Restroom Trailer Rental",
        "Celebracion de Aniversario? Renta de Bano de Lujo",
        (
            "Make your anniversary celebration unforgettable for every guest.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Haz que la celebracion de aniversario sea inolvidable para cada invitado.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 23 - Memorial Service
    (
        23, "memorial",
        "Restroom Trailer for Memorial Services and Celebrations of Life",
        "Trailer de Bano para Servicios Memoriales",
        (
            "Honor their memory with a dignified gathering. We handle the restroom details.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Honre su memoria con una reunion digna. Nosotros nos encargamos de los banos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 24 - Camp Restrooms
    (
        24, "camp",
        "Restroom Trailer for Camps and Outdoor Programs",
        "Trailer de Bano para Campamentos y Programas al Aire Libre",
        (
            "Running a summer camp, scout retreat, or outdoor program? Real restrooms make all the difference.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n"
            f"Multi-day and weekly rates available.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Campamento de verano, retiro scout o programa al aire libre? Banos de verdad hacen la diferencia.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n"
            f"Tarifas de varios dias y semanales disponibles.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 25 - HOA Event
    (
        25, "hoa",
        "Restroom Trailer for HOA Events and Community Gatherings",
        "Trailer de Bano para Eventos de HOA y Comunidad",
        (
            "HOA pool party, block party, or community celebration? Luxury restrooms for your residents.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Fiesta de alberca del HOA, fiesta de cuadra o celebracion comunitaria? Banos de lujo para sus residentes.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 26 - Food Truck Festival
    (
        26, "food_truck",
        "Restroom Trailer for Food Truck Festivals and Markets",
        "Trailer de Bano para Festivales de Food Trucks",
        (
            "Food truck festival or outdoor market? Your attendees need clean restrooms nearby.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Festival de food trucks o mercado al aire libre? Tus asistentes necesitan banos limpios cerca.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 27 - Marathon / Race
    (
        27, "marathon",
        "Luxury Restroom Trailer for Marathons and Race Events",
        "Trailer de Bano de Lujo para Maratones y Carreras",
        (
            "Runners and spectators need clean restrooms. Upgrade from standard porta potties.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Corredores y espectadores necesitan banos limpios. Mejore sus banos portatiles estandar.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 28 - Carnival
    (
        28, "carnival",
        "Restroom Trailer for Carnivals and Fairs",
        "Trailer de Bano para Carnavales y Ferias",
        (
            "Carnival or fair coming up? Families deserve clean, comfortable restrooms.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Carnaval o feria en camino? Las familias merecen banos limpios y comodos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 29 - Art Show
    (
        29, "art_show",
        "Luxury Restroom Trailer for Art Shows and Gallery Events",
        "Trailer de Bano de Lujo para Exposiciones de Arte",
        (
            "Art shows and gallery events demand an elevated guest experience. Including the restrooms.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Las exposiciones de arte y eventos de galeria exigen una experiencia elevada. Incluyendo los banos.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 30 - Music Festival
    (
        30, "music_festival",
        "VIP Restroom Trailer for Music Festivals",
        "Trailer de Bano VIP para Festivales de Musica",
        (
            "Give your festival the VIP treatment. Artists, crew, and guests all deserve real restrooms.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Dale a tu festival el trato VIP. Artistas, equipo e invitados merecen banos de verdad.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 31 - County Fair
    (
        31, "county_fair",
        "Restroom Trailer for County Fairs and Expos",
        "Trailer de Bano para Ferias del Condado y Exposiciones",
        (
            "County fair or expo? Upgrade the restroom experience for thousands of visitors.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Feria del condado o exposicion? Mejore la experiencia de banos para miles de visitantes.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
    # 32 - Emergency Response
    (
        32, "emergency",
        "Emergency Restroom Trailer -- Same Day Available",
        "Trailer de Bano de Emergencia -- Disponible el Mismo Dia",
        (
            "Plumbing emergency, disaster response, or last-minute event? We can deliver fast.\n\n"
            f"Our luxury trailer features {FEATURES_EN}.\n\n"
            f"Fully self-contained -- just needs flat ground.\n"
            f"Delivery, setup, and pickup INCLUDED.\n"
            f"Same-day and next-day delivery available.\n\n"
            f"Price: {PRICE}\n"
            f"Serving {SERVICE_AREA_EN}.\n\n"
            f"Call or text NOW: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        (
            "Emergencia de plomeria, respuesta a desastre o evento de ultimo momento? Entregamos rapido.\n\n"
            f"Nuestro trailer de lujo incluye {FEATURES_ES}.\n\n"
            f"Completamente autosuficiente -- solo necesita terreno plano.\n"
            f"Entrega, instalacion y recogida INCLUIDA.\n"
            f"Entrega el mismo dia o al dia siguiente disponible.\n\n"
            f"Precio: {PRICE}\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto AHORA: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
    ),
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_pt() -> datetime:
    return datetime.now(PT)


def _today_str() -> str:
    return _now_pt().strftime("%Y-%m-%d")


def _log(msg: str) -> None:
    print(f"[PostEngine] {msg}")


def _load_config() -> dict:
    p = Path.home() / ".nexus" / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE INIT
# ═════════════════════════════════════════════════════════════════════════════

def init_posting_engine() -> None:
    """Create posting engine tables, seed 32 angles if empty."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS posting_angles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        angle_number INTEGER,
        niche TEXT,
        title_en TEXT,
        title_es TEXT,
        description_en TEXT,
        description_es TEXT,
        last_used_date TEXT,
        times_used INTEGER DEFAULT 0,
        inquiries_generated INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS posting_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT,
        language TEXT,
        angle_id INTEGER,
        title TEXT,
        body TEXT,
        photos_used TEXT,
        hashtags TEXT,
        region TEXT,
        posted_at TIMESTAMP DEFAULT (datetime('now')),
        auto_posted BOOLEAN DEFAULT FALSE,
        engagement_data TEXT
    );

    CREATE TABLE IF NOT EXISTS photo_rotation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        last_used_date TEXT,
        times_used INTEGER DEFAULT 0,
        platforms_used TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_posting_angles_niche ON posting_angles(niche);
    CREATE INDEX IF NOT EXISTS idx_posting_angles_last_used ON posting_angles(last_used_date);
    CREATE INDEX IF NOT EXISTS idx_posting_log_platform ON posting_log(platform);
    CREATE INDEX IF NOT EXISTS idx_posting_log_posted_at ON posting_log(posted_at);
    CREATE INDEX IF NOT EXISTS idx_photo_rotation_last_used ON photo_rotation(last_used_date);
    """)
    conn.commit()

    # Seed angles if table is empty
    count = conn.execute("SELECT COUNT(*) FROM posting_angles").fetchone()[0]
    if count == 0:
        _log("Seeding 32 posting angles...")
        conn.executemany(
            "INSERT INTO posting_angles "
            "(angle_number, niche, title_en, title_es, description_en, description_es) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            SEED_ANGLES,
        )
        conn.commit()
        _log(f"Seeded {len(SEED_ANGLES)} posting angles")

    # Seed photo rotation if empty
    photo_count = conn.execute("SELECT COUNT(*) FROM photo_rotation").fetchone()[0]
    if photo_count == 0:
        _log("Seeding photo rotation...")
        conn.executemany(
            "INSERT INTO photo_rotation (filename, last_used_date, times_used, platforms_used) "
            "VALUES (?, NULL, 0, '[]')",
            [(f,) for f in DEFAULT_PHOTOS],
        )
        conn.commit()
        _log(f"Seeded {len(DEFAULT_PHOTOS)} photos into rotation")

    conn.close()
    _log("Database tables ready")


# ═════════════════════════════════════════════════════════════════════════════
# ANGLE SELECTION
# ═════════════════════════════════════════════════════════════════════════════

def pick_next_angle() -> dict:
    """Pick the next angle to use. Never repeats within 30 days.

    Returns dict with: id, angle_number, niche, title_en, title_es,
                       description_en, description_es, times_used
    """
    conn = _get_conn()
    cutoff = (_now_pt() - timedelta(days=30)).strftime("%Y-%m-%d")

    # Prefer angles not used in last 30 days, then least-used overall
    row = conn.execute(
        "SELECT * FROM posting_angles "
        "WHERE last_used_date IS NULL OR last_used_date < ? "
        "ORDER BY times_used ASC, RANDOM() LIMIT 1",
        (cutoff,),
    ).fetchone()

    # Fallback: if ALL used within 30 days, pick oldest
    if row is None:
        row = conn.execute(
            "SELECT * FROM posting_angles "
            "ORDER BY last_used_date ASC, times_used ASC LIMIT 1"
        ).fetchone()

    conn.close()

    if row is None:
        _log("WARNING: No angles found in database")
        return {}

    return {
        "id": row["id"],
        "angle_number": row["angle_number"],
        "niche": row["niche"],
        "title_en": row["title_en"],
        "title_es": row["title_es"],
        "description_en": row["description_en"],
        "description_es": row["description_es"],
        "times_used": row["times_used"],
        "inquiries_generated": row["inquiries_generated"],
    }


def _mark_angle_used(angle_id: int) -> None:
    """Mark an angle as used today and increment usage count."""
    conn = _get_conn()
    conn.execute(
        "UPDATE posting_angles SET last_used_date = ?, times_used = times_used + 1 "
        "WHERE id = ?",
        (_today_str(), angle_id),
    )
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# PHOTO ROTATION
# ═════════════════════════════════════════════════════════════════════════════

def select_photos(count: int = 5) -> list[str]:
    """Select photos using least-recently-used rotation.

    Returns list of photo filenames sorted by least recently used first.
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT id, filename FROM photo_rotation "
        "ORDER BY last_used_date ASC NULLS FIRST, times_used ASC, RANDOM() "
        "LIMIT ?",
        (count,),
    ).fetchall()

    today = _today_str()
    photo_ids = [r["id"] for r in rows]
    filenames = [r["filename"] for r in rows]

    if photo_ids:
        placeholders = ",".join("?" * len(photo_ids))
        conn.execute(
            f"UPDATE photo_rotation SET last_used_date = ?, times_used = times_used + 1 "
            f"WHERE id IN ({placeholders})",
            [today, *photo_ids],
        )
        conn.commit()

    conn.close()
    return filenames


# ═════════════════════════════════════════════════════════════════════════════
# LISTING GENERATION
# ═════════════════════════════════════════════════════════════════════════════

def generate_marketplace_listing(
    angle: dict,
    language: str,
    platform: str,
) -> dict:
    """Generate a marketplace listing for a given angle, language, and platform.

    Args:
        angle: dict from pick_next_angle()
        language: "en" or "es"
        platform: "fb_marketplace" or "craigslist"

    Returns:
        dict with keys: title, body, platform, language, angle_id, region, photos
    """
    if language == "es":
        title = angle["title_es"]
        body = angle["description_es"]
    else:
        title = angle["title_en"]
        body = angle["description_en"]

    # Craigslist: plain text only, strip any formatting
    if platform == "craigslist":
        body = body.replace("--", "-")

    # Select CL region based on day of year rotation
    region = ""
    if platform == "craigslist":
        day_of_year = _now_pt().timetuple().tm_yday
        region = CL_REGIONS[day_of_year % len(CL_REGIONS)]

    photos = select_photos(5)

    # Log to database
    conn = _get_conn()
    conn.execute(
        "INSERT INTO posting_log "
        "(platform, language, angle_id, title, body, photos_used, region, auto_posted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (platform, language, angle["id"], title, body,
         json.dumps(photos), region, False),
    )
    conn.commit()
    conn.close()

    # Mark angle as used
    _mark_angle_used(angle["id"])

    return {
        "title": title,
        "body": body,
        "platform": platform,
        "language": language,
        "angle_id": angle["id"],
        "niche": angle["niche"],
        "region": region,
        "photos": photos,
        "price": PRICE_NUM,
    }


def generate_social_post(angle: dict, platform: str) -> dict:
    """Generate a social media post (Instagram or FB Page).

    Args:
        angle: dict from pick_next_angle()
        platform: "instagram" or "fb_page"

    Returns:
        dict with keys: caption, hashtags, hashtag_set, platform, angle_id, photos
    """
    # Use English for social media posts
    title = angle["title_en"]
    niche = angle["niche"]

    # Build caption
    caption = (
        f"{title}\n\n"
        f"Luxury 4-stall restroom trailer for your next event.\n"
        f"AC | Running water | LED lighting | Bluetooth speakers\n\n"
        f"Fully self-contained -- just needs flat ground.\n"
        f"Delivery, setup, and pickup INCLUDED.\n\n"
        f"{PRICE} | Serving all of SoCal\n\n"
        f"Book now:\n"
        f"{BIZ_PHONE}\n"
        f"{BIZ_WEBSITE}"
    )

    # Rotate hashtag set based on day of year
    set_keys = list(HASHTAG_SETS.keys())
    day_of_year = _now_pt().timetuple().tm_yday
    set_key = set_keys[day_of_year % len(set_keys)]
    hashtags = HASHTAG_SETS[set_key]

    # Pick 20 hashtags from the selected set
    selected_hashtags = hashtags[:20]
    hashtag_str = " ".join(selected_hashtags)

    photos = select_photos(5)

    # Log
    conn = _get_conn()
    conn.execute(
        "INSERT INTO posting_log "
        "(platform, language, angle_id, title, body, photos_used, hashtags, auto_posted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (platform, "en", angle["id"], title, caption,
         json.dumps(photos), hashtag_str, False),
    )
    conn.commit()
    conn.close()

    return {
        "caption": caption,
        "hashtags": hashtag_str,
        "hashtag_set": set_key,
        "platform": platform,
        "angle_id": angle["id"],
        "niche": niche,
        "photos": photos,
    }


# ═════════════════════════════════════════════════════════════════════════════
# STATUS & ANALYTICS
# ═════════════════════════════════════════════════════════════════════════════

def get_posting_status() -> dict:
    """Get today's posting summary.

    Returns dict with: date, total_posts, by_platform, by_language,
                       angles_used, photos_used
    """
    today = _today_str()
    conn = _get_conn()

    total = conn.execute(
        "SELECT COUNT(*) FROM posting_log WHERE DATE(posted_at) = ?", (today,)
    ).fetchone()[0]

    by_platform = {}
    for row in conn.execute(
        "SELECT platform, COUNT(*) as cnt FROM posting_log "
        "WHERE DATE(posted_at) = ? GROUP BY platform", (today,)
    ).fetchall():
        by_platform[row["platform"]] = row["cnt"]

    by_language = {}
    for row in conn.execute(
        "SELECT language, COUNT(*) as cnt FROM posting_log "
        "WHERE DATE(posted_at) = ? GROUP BY language", (today,)
    ).fetchall():
        by_language[row["language"]] = row["cnt"]

    angles = conn.execute(
        "SELECT DISTINCT angle_id FROM posting_log WHERE DATE(posted_at) = ?",
        (today,),
    ).fetchall()

    photos_rows = conn.execute(
        "SELECT photos_used FROM posting_log WHERE DATE(posted_at) = ?",
        (today,),
    ).fetchall()

    all_photos: set[str] = set()
    for r in photos_rows:
        if r["photos_used"]:
            try:
                all_photos.update(json.loads(r["photos_used"]))
            except (json.JSONDecodeError, TypeError):
                pass

    conn.close()

    return {
        "date": today,
        "total_posts": total,
        "by_platform": by_platform,
        "by_language": by_language,
        "angles_used": len(angles),
        "photos_used": len(all_photos),
    }


def get_angle_performance() -> list[dict]:
    """Get angles ranked by inquiries generated.

    Returns list of dicts: angle_number, niche, title_en, times_used,
                           inquiries_generated, last_used_date
    """
    conn = _get_conn()
    rows = conn.execute(
        "SELECT angle_number, niche, title_en, times_used, "
        "inquiries_generated, last_used_date "
        "FROM posting_angles ORDER BY inquiries_generated DESC, times_used DESC"
    ).fetchall()
    conn.close()

    return [
        {
            "angle_number": r["angle_number"],
            "niche": r["niche"],
            "title_en": r["title_en"],
            "times_used": r["times_used"],
            "inquiries_generated": r["inquiries_generated"],
            "last_used_date": r["last_used_date"],
        }
        for r in rows
    ]


def record_inquiry(angle_id: int) -> None:
    """Increment inquiry count for an angle (called when a lead comes in)."""
    conn = _get_conn()
    conn.execute(
        "UPDATE posting_angles SET inquiries_generated = inquiries_generated + 1 "
        "WHERE id = ?",
        (angle_id,),
    )
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# HOLD / PAUSE LOGIC
# ═════════════════════════════════════════════════════════════════════════════

_holds: dict[str, str] = {}  # platform -> hold reason


def set_hold(platform: str, reason: str = "manual") -> None:
    """Pause posting for a platform."""
    _holds[platform] = reason
    _log(f"Hold set for {platform}: {reason}")


def clear_hold(platform: str) -> None:
    """Resume posting for a platform."""
    _holds.pop(platform, None)
    _log(f"Hold cleared for {platform}")


def get_holds() -> dict[str, str]:
    """Return current holds."""
    return dict(_holds)


def is_on_hold(platform: str) -> bool:
    """Check if a platform is on hold."""
    return platform in _holds


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLERS (sync, return str)
# ═════════════════════════════════════════════════════════════════════════════

def handle_posting_command(text: str) -> str:
    """/posting -- show today's posting status."""
    status = get_posting_status()
    holds = get_holds()

    msg = (
        f"\U0001f4cb POSTING STATUS -- {status['date']}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4e8 Total posts today: {status['total_posts']}\n\n"
    )

    if status["by_platform"]:
        msg += "\U0001f4f1 By Platform:\n"
        for plat, cnt in status["by_platform"].items():
            label = plat.replace("_", " ").title()
            msg += f"  \u2022 {label}: {cnt}\n"
        msg += "\n"

    if status["by_language"]:
        msg += "\U0001f30e By Language:\n"
        for lang, cnt in status["by_language"].items():
            flag = "\U0001f1fa\U0001f1f8" if lang == "en" else "\U0001f1f2\U0001f1fd"
            msg += f"  {flag} {lang.upper()}: {cnt}\n"
        msg += "\n"

    msg += (
        f"\U0001f3af Angles used: {status['angles_used']}\n"
        f"\U0001f4f7 Photos used: {status['photos_used']}\n"
    )

    if holds:
        msg += "\n\u26a0\ufe0f Active Holds:\n"
        for plat, reason in holds.items():
            msg += f"  \u23f8 {plat}: {reason}\n"
    else:
        msg += "\n\u2705 No active holds\n"

    return msg


def handle_post_preview_command(text: str) -> str:
    """/post_preview -- preview tomorrow's posting angles."""
    # Pick 4 angles to preview (without marking used)
    conn = _get_conn()
    cutoff = (_now_pt() - timedelta(days=30)).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT angle_number, niche, title_en, title_es, times_used "
        "FROM posting_angles "
        "WHERE last_used_date IS NULL OR last_used_date < ? "
        "ORDER BY times_used ASC, RANDOM() LIMIT 4",
        (cutoff,),
    ).fetchall()

    if not rows:
        rows = conn.execute(
            "SELECT angle_number, niche, title_en, title_es, times_used "
            "FROM posting_angles "
            "ORDER BY last_used_date ASC, times_used ASC LIMIT 4"
        ).fetchall()

    conn.close()

    # Determine tomorrow's CL region
    tomorrow = _now_pt() + timedelta(days=1)
    region_idx = tomorrow.timetuple().tm_yday % len(CL_REGIONS)
    tomorrow_region = CL_REGIONS[region_idx]

    # Determine hashtag set for tomorrow
    set_keys = list(HASHTAG_SETS.keys())
    ht_idx = tomorrow.timetuple().tm_yday % len(set_keys)
    tomorrow_ht_set = set_keys[ht_idx]

    msg = (
        f"\U0001f52e TOMORROW'S POSTING PREVIEW\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001f4c5 {tomorrow.strftime('%A, %B %d')}\n"
        f"\U0001f4cd CL Region: {tomorrow_region}\n"
        f"# Hashtag Set: {tomorrow_ht_set}\n\n"
        f"\U0001f3af Candidate Angles:\n"
    )

    platforms = ["FB Marketplace EN", "FB Marketplace ES",
                 "Craigslist EN", "Craigslist ES"]
    for i, row in enumerate(rows):
        plat = platforms[i] if i < len(platforms) else "Extra"
        msg += (
            f"\n{i + 1}. {plat}\n"
            f"   \U0001f3af {row['niche'].replace('_', ' ').title()}\n"
            f"   \U0001f1fa\U0001f1f8 {row['title_en'][:60]}\n"
            f"   \U0001f1f2\U0001f1fd {row['title_es'][:60]}\n"
            f"   Used: {row['times_used']}x\n"
        )

    msg += "\n\U0001f4a1 These are candidates. Final selection happens at 8 AM PT."
    return msg


def handle_post_hold_command(text: str) -> str:
    """/hold [platform] -- pause or resume posting for a platform."""
    stripped = text.strip()
    for pfx in ["/hold ", "/post_hold "]:
        if stripped.lower().startswith(pfx):
            stripped = stripped[len(pfx):].strip()
            break
    else:
        # Just /hold with no args: show current holds
        holds = get_holds()
        if not holds:
            return (
                "\u2705 No active holds.\n\n"
                "Usage: /hold [platform]\n"
                "Platforms: fb_marketplace, craigslist, instagram, fb_page, all"
            )
        msg = "\u23f8 Active Holds:\n"
        for plat, reason in holds.items():
            msg += f"  \u2022 {plat}: {reason}\n"
        msg += "\nUse /hold clear [platform] to resume."
        return msg

    lower = stripped.lower()

    # Handle clear
    if lower.startswith("clear"):
        target = lower.replace("clear", "").strip()
        if target == "all":
            for p in list(_holds.keys()):
                clear_hold(p)
            return "\u2705 All holds cleared. Posting resumed."
        if target:
            clear_hold(target)
            return f"\u2705 Hold cleared for {target}. Posting resumed."
        return "Usage: /hold clear [platform|all]"

    # Set hold
    valid = ["fb_marketplace", "craigslist", "instagram", "fb_page", "all"]
    if lower == "all":
        for v in valid[:-1]:
            set_hold(v, "manual hold")
        return "\u23f8 All platforms on hold."

    if lower in valid:
        set_hold(lower, "manual hold")
        return f"\u23f8 {lower} is now on hold.\nUse /hold clear {lower} to resume."

    return (
        f"Unknown platform: {stripped}\n"
        f"Valid: {', '.join(valid)}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# DAILY SCHEDULE GENERATION
# ═════════════════════════════════════════════════════════════════════════════

def _build_pipeline_status() -> str:
    """Build the 7:55 AM pipeline status summary."""
    msg = (
        f"\U0001f4ca MORNING STATUS -- {_now_pt().strftime('%A, %B %d, %Y')}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    )
    try:
        conn = _get_conn()
        active = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status NOT IN ('closed','opted_out')"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM message_approvals WHERE status='pending'"
        ).fetchone()[0]
        followups = conn.execute(
            "SELECT COUNT(*) FROM leads "
            "WHERE next_action_at <= datetime('now') "
            "AND status NOT IN ('closed','opted_out')"
        ).fetchone()[0]
        today_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= date('now')"
        ).fetchone()[0]
        conn.close()
        msg += (
            f"\u2022 Active leads: {active}\n"
            f"\u2022 Pending approvals: {pending}\n"
            f"\u2022 Follow-ups due: {followups}\n"
            f"\u2022 New leads today: {today_leads}\n"
        )
    except Exception:
        msg += "\u2022 Pipeline data unavailable\n"
    return msg


def _build_marketplace_message(
    listing: dict, index: int, label: str, lang_flag: str
) -> str:
    """Build a single marketplace listing Telegram message."""
    msg = f"{index}\ufe0f\u20e3 {label} {lang_flag}\n"
    msg += f"\U0001f4cc Title: {listing['title']}\n"
    msg += f"\U0001f4b0 Price: {PRICE}\n"

    if listing["platform"] == "craigslist":
        msg += f"\U0001f4c2 Category: Services > Event Services\n"
        msg += f"\U0001f4cd Region: {listing['region']}\n"

    msg += f"\n\U0001f4dd COPY/PASTE:\n{listing['body']}\n"

    if listing["photos"]:
        msg += f"\n\U0001f5bc PHOTOS (in order):\n"
        for i, photo in enumerate(listing["photos"], 1):
            msg += f"  {i}. {photo}\n"

    return msg


def _build_auto_post_message(ig_preview: Optional[dict] = None) -> str:
    """Build the 8:20 AM auto-post confirmation message."""
    msg = (
        f"\u2705 AUTO-POSTING TODAY:\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
    )

    holds = get_holds()

    if is_on_hold("instagram"):
        msg += "\u23f8 Instagram: ON HOLD\n"
    elif ig_preview:
        msg += (
            f"\U0001f4f7 Instagram Feed:\n"
            f"  Caption: {ig_preview['caption'][:80]}...\n"
            f"  Hashtag Set: {ig_preview['hashtag_set']}\n"
        )
    else:
        msg += "\U0001f4f7 Instagram: Ready\n"

    if is_on_hold("fb_page"):
        msg += "\u23f8 FB Page: ON HOLD\n"
    else:
        msg += "\U0001f4d8 FB Page: Ready\n"

    msg += "\nReply \"auto\" to post | Reply \"hold\" to pause"
    return msg


async def _run_daily_schedule(send_fn) -> None:
    """Execute the full daily posting schedule.

    Schedule (PT):
      7:55 AM  -- Pipeline status summary
      8:00 AM  -- FB Marketplace English
      8:05 AM  -- FB Marketplace Spanish
      8:10 AM  -- Craigslist English
      8:15 AM  -- Craigslist Spanish
      8:20 AM  -- Auto-post confirmations (IG + FB Page)
    """
    _log("Daily schedule starting...")

    # Pick angles for today (up to 4 unique angles)
    angles = []
    used_ids: set[int] = set()
    for _ in range(4):
        angle = pick_next_angle()
        if angle and angle["id"] not in used_ids:
            angles.append(angle)
            used_ids.add(angle["id"])

    if len(angles) < 4:
        _log(f"WARNING: Only {len(angles)} unique angles available today")

    # 7:55 AM -- Pipeline Status
    _log("Sending pipeline status...")
    pipeline_msg = _build_pipeline_status()
    try:
        await send_fn(pipeline_msg)
    except Exception as e:
        _log(f"Error sending pipeline status: {e}")
    await asyncio.sleep(300)  # Wait 5 min -> 8:00 AM

    # 8:00 AM -- FB Marketplace English
    if not is_on_hold("fb_marketplace") and len(angles) >= 1:
        _log("Generating FB Marketplace EN...")
        listing = generate_marketplace_listing(angles[0], "en", "fb_marketplace")
        msg = _build_marketplace_message(
            listing, 1, "FB MARKETPLACE -- ENGLISH", "\U0001f1fa\U0001f1f8"
        )
        try:
            await send_fn(msg)
        except Exception as e:
            _log(f"Error sending FB MP EN: {e}")
    else:
        _log("FB Marketplace EN: skipped (on hold or no angles)")
    await asyncio.sleep(300)  # Wait 5 min -> 8:05 AM

    # 8:05 AM -- FB Marketplace Spanish
    if not is_on_hold("fb_marketplace") and len(angles) >= 2:
        _log("Generating FB Marketplace ES...")
        listing = generate_marketplace_listing(angles[1], "es", "fb_marketplace")
        msg = _build_marketplace_message(
            listing, 2, "FB MARKETPLACE -- ESPANOL", "\U0001f1f2\U0001f1fd"
        )
        try:
            await send_fn(msg)
        except Exception as e:
            _log(f"Error sending FB MP ES: {e}")
    else:
        _log("FB Marketplace ES: skipped (on hold or no angles)")
    await asyncio.sleep(300)  # Wait 5 min -> 8:10 AM

    # 8:10 AM -- Craigslist English
    if not is_on_hold("craigslist") and len(angles) >= 3:
        _log("Generating Craigslist EN...")
        listing = generate_marketplace_listing(angles[2], "en", "craigslist")
        msg = _build_marketplace_message(
            listing, 3, "CRAIGSLIST -- ENGLISH", "\U0001f1fa\U0001f1f8"
        )
        try:
            await send_fn(msg)
        except Exception as e:
            _log(f"Error sending CL EN: {e}")
    else:
        _log("Craigslist EN: skipped (on hold or no angles)")
    await asyncio.sleep(300)  # Wait 5 min -> 8:15 AM

    # 8:15 AM -- Craigslist Spanish
    if not is_on_hold("craigslist") and len(angles) >= 4:
        _log("Generating Craigslist ES...")
        listing = generate_marketplace_listing(angles[3], "es", "craigslist")
        msg = _build_marketplace_message(
            listing, 4, "CRAIGSLIST -- ESPANOL", "\U0001f1f2\U0001f1fd"
        )
        try:
            await send_fn(msg)
        except Exception as e:
            _log(f"Error sending CL ES: {e}")
    else:
        _log("Craigslist ES: skipped (on hold or no angles)")
    await asyncio.sleep(300)  # Wait 5 min -> 8:20 AM

    # 8:20 AM -- Auto-post confirmations
    ig_preview = None
    if not is_on_hold("instagram") and angles:
        ig_preview = generate_social_post(angles[0], "instagram")

    auto_msg = _build_auto_post_message(ig_preview)
    try:
        await send_fn(auto_msg)
    except Exception as e:
        _log(f"Error sending auto-post confirmation: {e}")

    _log("Daily schedule complete")


# ═════════════════════════════════════════════════════════════════════════════
# ASYNC SCHEDULER
# ═════════════════════════════════════════════════════════════════════════════

async def run_posting_scheduler(send_fn) -> None:
    """Async loop running the daily posting schedule.

    Checks every 60 seconds. When the 7:55 AM PT window opens, runs the
    full daily schedule. Heartbeats every 60s.
    """
    try:
        from core.watchdog import heartbeat
    except ImportError:
        def heartbeat(_: str) -> None:
            pass

    _log("Posting scheduler started")
    init_posting_engine()

    sent_today = ""

    while True:
        try:
            heartbeat("posting_engine")
            now = _now_pt()
            today = now.strftime("%Y-%m-%d")

            # Trigger at 7:55 AM PT, only once per day
            if (
                now.hour == 7
                and now.minute >= 55
                and today != sent_today
            ):
                sent_today = today
                _log(f"Daily schedule triggered for {today}")
                try:
                    await _run_daily_schedule(send_fn)
                except Exception as e:
                    _log(f"Daily schedule error: {e}")
                    traceback.print_exc()

            # Also allow 8:00 AM trigger if 7:55 was missed
            elif (
                now.hour == 8
                and now.minute < 5
                and today != sent_today
            ):
                sent_today = today
                _log(f"Daily schedule triggered (catch-up) for {today}")
                try:
                    await _run_daily_schedule(send_fn)
                except Exception as e:
                    _log(f"Daily schedule error: {e}")
                    traceback.print_exc()

        except Exception as e:
            _log(f"Scheduler loop error: {e}")
            traceback.print_exc()

        await asyncio.sleep(60)


# ═════════════════════════════════════════════════════════════════════════════
# MODULE INIT
# ═════════════════════════════════════════════════════════════════════════════

try:
    init_posting_engine()
except Exception as _init_err:
    _log(f"Init warning (non-fatal): {_init_err}")
