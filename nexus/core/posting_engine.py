"""
Posting Engine — DB-backed daily posting system for Zoar Bathroom Rentals.

32 unique posting angles stored in SQLite with full rotation tracking.
Generates copy-paste-ready listings for FB Marketplace & Craigslist in
English and Spanish, with Slack-formatted briefings.

Rotation rules:
  - No angle repeated within 30 days
  - No photo set repeated within 7 days
  - All usage tracked in posting_log table

Part of the Nexus Network — FastAPI + SQLite system.
Database: ~/.nexus/memory.db
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from textwrap import dedent
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("PostingEngine")
_handler = logging.StreamHandler()
_handler.setFormatter(logging.Formatter("[PostingEngine] %(message)s"))
if not log.handlers:
    log.addHandler(_handler)
log.setLevel(logging.INFO)

LA_TZ = ZoneInfo("America/Los_Angeles")

# ── Public business info (NEVER personal) ────────────────────────────────
BIZ_PHONE = "(424) 235-8979"
BIZ_EMAIL = "zoarbathrooms@gmail.com"
BIZ_WEBSITE = "zoarbathroomrental.com"
BIZ_NAME = "Zoar Bathroom Rental"
SERVICE_AREA = "Greater Los Angeles, San Fernando Valley, Ventura, Santa Clarita, and all of SoCal"
SERVICE_AREA_ES = "el area metropolitana de Los Angeles, San Fernando Valley, Ventura, Santa Clarita y todo el sur de California"
PRICE = "Starting at $999"

# Craigslist region rotation
CL_REGIONS = [
    "Los Angeles",
    "San Fernando Valley",
    "Ventura County",
    "Santa Clarita",
    "Orange County",
]

# ══════════════════════════════════════════════════════════════════════════
# 32 SEED ANGLES — full 800-1200 char descriptions
# First 10 have both English and Spanish versions.
# ══════════════════════════════════════════════════════════════════════════

SEED_ANGLES: list[dict] = [
    # ── 1. Wedding Luxury ────────────────────────────────────────────────
    {
        "angle_name": "wedding_luxury",
        "title_en": "Luxury Restroom Trailer for Your Dream Wedding",
        "description_en": (
            "Your dream wedding deserves dream restrooms. Forget plastic porta potties "
            "— our luxury 4-stall restroom trailer brings the elegance your guests expect.\n\n"
            "Each stall features real flushing toilets, running hot and cold water, hardwood-style "
            "floors, full-length vanity mirrors, interior LED lighting, and a Bluetooth speaker "
            "playing your playlist. Climate-controlled with AC in summer and heat in winter so "
            "everyone stays comfortable no matter the season.\n\n"
            "We handle everything: delivery, professional setup, leveling, and pickup after your "
            "event. All you do is enjoy your day.\n\n"
            f"Flat rate: {PRICE} — serving {SERVICE_AREA}.\n\n"
            "Your guests will think the restrooms are part of the venue. Book early — wedding "
            "season dates go fast.\n\n"
            f"Call or text for a free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Banos de Lujo para la Boda de Tus Suenos",
        "description_es": (
            "Tu boda de ensueno merece banos de ensueno. Olvida los banos portatiles de plastico "
            "— nuestro trailer de lujo con 4 cabinas privadas trae la elegancia que tus invitados esperan.\n\n"
            "Cada cabina tiene sanitarios reales con descarga de agua, agua corriente caliente y fria, "
            "pisos estilo madera, espejos de cuerpo entero, iluminacion LED y bocina Bluetooth "
            "para tu musica. Climatizado con aire acondicionado en verano y calefaccion en invierno.\n\n"
            "Nosotros nos encargamos de todo: entrega, instalacion profesional, nivelacion y "
            "recoleccion despues de tu evento.\n\n"
            f"Tarifa fija: {PRICE} — servimos {SERVICE_AREA_ES}.\n\n"
            "Tus invitados pensaran que los banos son parte del lugar. Reserva pronto — las "
            "fechas de temporada de bodas se llenan rapido.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "wedding",
        "photo_set": "A",
    },
    # ── 2. Quinceanera Special ───────────────────────────────────────────
    {
        "angle_name": "quinceanera_special",
        "title_en": "Luxury Restroom Trailer for Quinceaneras",
        "description_en": (
            "Her quinceanera is once in a lifetime — every detail matters, including the restrooms. "
            "Our luxury 4-stall restroom trailer makes sure your guests are impressed from start "
            "to finish.\n\n"
            "Real flushing toilets, running water, hardwood-style floors, full-length mirrors, "
            "LED mood lighting, and a Bluetooth speaker so the party vibe never stops. "
            "Climate-controlled with AC and heat for any season.\n\n"
            "Perfect for backyard celebrations, banquet halls without enough restrooms, park "
            "events, and any outdoor venue. We deliver, set up, and pick up — you just focus on "
            "the celebration.\n\n"
            f"All-inclusive rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Don't let porta potties be the one thing people remember. Give your guests a luxury "
            "experience they'll actually talk about.\n\n"
            f"Free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Banos de Lujo para Tu Quinceanera",
        "description_es": (
            "Su quinceanera es una vez en la vida — cada detalle importa, incluyendo los banos. "
            "Nuestro trailer de lujo con 4 cabinas asegura que tus invitados esten impresionados "
            "de principio a fin.\n\n"
            "Sanitarios reales con descarga, agua corriente, pisos estilo madera, espejos de cuerpo "
            "entero, iluminacion LED y bocina Bluetooth para que la fiesta no pare. Climatizado "
            "con AC y calefaccion.\n\n"
            "Perfecto para celebraciones en casa, salones sin suficientes banos, parques y "
            "cualquier lugar al aire libre. Nosotros entregamos, instalamos y recogemos.\n\n"
            f"Tarifa todo incluido: {PRICE}. Servimos {SERVICE_AREA_ES}.\n\n"
            "No dejes que los banos portatiles sean lo unico que recuerden. Dale a tus invitados "
            "una experiencia de lujo.\n\n"
            f"Cotizacion gratis: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "quinceanera",
        "photo_set": "B",
    },
    # ── 3. Outdoor Event Essential ───────────────────────────────────────
    {
        "angle_name": "outdoor_event_essential",
        "title_en": "Don't Let Porta-Potties Ruin Your Outdoor Event",
        "description_en": (
            "You spent months planning the perfect outdoor event. Don't let a row of smelly "
            "porta potties ruin the experience for your guests.\n\n"
            "Our luxury 4-stall restroom trailer is the upgrade your event deserves. Real "
            "flushing toilets with running hot and cold water. Hardwood-style floors, full-length "
            "mirrors, LED interior lighting, and a Bluetooth speaker. Full climate control — AC "
            "when it's hot, heat when it's cold.\n\n"
            "We bring it, set it up on any flat surface (grass, gravel, driveway), and pick it up "
            "when your event is done. Zero hassle for you.\n\n"
            f"One flat rate: {PRICE}. Delivery, setup, and pickup included.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Weddings, parties, corporate events, festivals — if it's outdoors and you need real "
            "restrooms, we've got you.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "No Dejes Que los Banos Portatiles Arruinen Tu Evento",
        "description_es": (
            "Pasaste meses planeando el evento perfecto al aire libre. No dejes que una fila de "
            "banos portatiles apestosos arruine la experiencia de tus invitados.\n\n"
            "Nuestro trailer de lujo con 4 cabinas es la mejora que tu evento necesita. Sanitarios "
            "reales con agua caliente y fria. Pisos estilo madera, espejos de cuerpo entero, "
            "iluminacion LED y bocina Bluetooth. Clima controlado — AC cuando hace calor, "
            "calefaccion cuando hace frio.\n\n"
            "Lo llevamos, lo instalamos en cualquier superficie plana y lo recogemos cuando termine "
            "tu evento. Cero complicaciones.\n\n"
            f"Tarifa unica: {PRICE}. Entrega, instalacion y recoleccion incluida.\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Llama o envia texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "outdoor",
        "photo_set": "C",
    },
    # ── 4. Corporate Event Class ─────────────────────────────────────────
    {
        "angle_name": "corporate_event_class",
        "title_en": "Impress Clients at Your Next Corporate Event",
        "description_en": (
            "Your corporate event is a reflection of your brand. Make sure the restroom "
            "experience matches the professionalism of everything else.\n\n"
            "Our luxury 4-stall restroom trailer features real flushing toilets, running water "
            "with soap dispensers, hardwood-style floors, full-length mirrors, LED lighting, and "
            "a Bluetooth speaker. Climate-controlled with AC and heating.\n\n"
            "Ideal for outdoor company picnics, client appreciation events, product launches, "
            "team retreats, holiday parties, and any venue that needs extra restroom capacity.\n\n"
            f"All-inclusive: {PRICE}. We handle delivery, setup, and removal.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Your clients and employees will notice the difference — and they'll remember your "
            "attention to detail.\n\n"
            f"Get a quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Impresiona a Tus Clientes en Tu Proximo Evento Corporativo",
        "description_es": (
            "Tu evento corporativo es un reflejo de tu marca. Asegurate de que la experiencia en "
            "los banos sea tan profesional como todo lo demas.\n\n"
            "Nuestro trailer de lujo con 4 cabinas tiene sanitarios reales con descarga de agua, "
            "agua corriente caliente y fria con dispensadores de jabon, pisos estilo madera, "
            "espejos de cuerpo entero, iluminacion LED ambiental y bocina Bluetooth. Completamente "
            "climatizado con aire acondicionado y calefaccion para cualquier temporada.\n\n"
            "Ideal para picnics corporativos, eventos para clientes, lanzamientos de productos, "
            "retiros de equipo y fiestas de fin de ano. Tus empleados y clientes notaran la "
            "diferencia y recordaran tu atencion al detalle.\n\n"
            f"Todo incluido: {PRICE}. Entrega, instalacion profesional y recoleccion.\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            f"Cotizacion: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "corporate",
        "photo_set": "D",
    },
    # ── 5. Festival & Concert ────────────────────────────────────────────
    {
        "angle_name": "festival_concert",
        "title_en": "Festival-Ready Luxury Restrooms",
        "description_en": (
            "Running a festival, concert, or large outdoor event? Your VIP section needs VIP "
            "restrooms.\n\n"
            "Our luxury 4-stall restroom trailer delivers the experience your attendees expect. "
            "Real flushing toilets, running water, hardwood-style floors, full-length mirrors, "
            "LED lighting, and a Bluetooth speaker. Full AC in summer heat, heating for evening "
            "events.\n\n"
            "Built to handle high traffic — 4 private stalls keep lines moving and guests happy. "
            "Perfect for VIP areas, artist lounges, and premium ticket holders.\n\n"
            "We deliver to any location, handle all setup and leveling, and pick up when the "
            "show is over.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Upgrade from plastic boxes to something your crowd will actually appreciate.\n\n"
            f"Book now: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Banos de Lujo Listos para Festivales",
        "description_es": (
            "Organizando un festival, concierto o evento grande al aire libre? Tu seccion VIP "
            "necesita banos VIP que esten a la altura de la experiencia.\n\n"
            "Nuestro trailer de lujo con 4 cabinas ofrece la experiencia premium que tus "
            "asistentes esperan. Sanitarios reales con descarga de agua, agua corriente caliente "
            "y fria, pisos estilo madera, espejos de cuerpo entero, iluminacion LED ambiental y "
            "bocina Bluetooth. Aire acondicionado para el calor del dia y calefaccion para "
            "eventos nocturnos.\n\n"
            "Construido para alto trafico — 4 cabinas privadas mantienen las filas cortas y a "
            "tus asistentes contentos. Perfecto para zonas VIP, areas de artistas y asistentes "
            "con boletos premium.\n\n"
            "Entregamos a cualquier ubicacion, nos encargamos de la instalacion y recogemos "
            "cuando termine el evento.\n\n"
            f"Tarifa: {PRICE}. Servimos {SERVICE_AREA_ES}.\n\n"
            f"Reserva: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "outdoor",
        "photo_set": "A",
    },
    # ── 6. Birthday Party Upgrade ────────────────────────────────────────
    {
        "angle_name": "birthday_party_upgrade",
        "title_en": "Make Your Birthday Party Unforgettable",
        "description_en": (
            "Big birthday bash but your house only has two bathrooms? Don't make 80 guests wait "
            "in line — upgrade with our luxury 4-stall restroom trailer.\n\n"
            "Real flushing toilets, running water, hardwood-style floors, full-length mirrors, "
            "LED lighting, and a Bluetooth speaker bumping the birthday playlist. "
            "Climate-controlled with AC and heat so the party goes all day and night.\n\n"
            "Drops right into your driveway, side yard, or any flat surface. We deliver, "
            "set up everything, and come back to pick it up after the celebration.\n\n"
            f"One price: {PRICE}. Everything included.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Your guests will be talking about the bathrooms — in the best way possible.\n\n"
            f"Text or call: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Haz Tu Fiesta de Cumpleanos Inolvidable",
        "description_es": (
            "Gran fiesta de cumpleanos pero tu casa solo tiene dos banos? No hagas que 80 "
            "invitados esperen en fila — mejora con nuestro trailer de lujo con 4 cabinas "
            "privadas que haran que todos hablen.\n\n"
            "Sanitarios reales con descarga de agua, agua corriente caliente y fria, pisos estilo "
            "madera, espejos de cuerpo entero, iluminacion LED ambiental y bocina Bluetooth "
            "reproduciendo tu playlist de cumpleanos. Completamente climatizado con aire "
            "acondicionado y calefaccion para que la fiesta dure todo el dia y la noche.\n\n"
            "Se estaciona facilmente en tu entrada, patio lateral o cualquier superficie plana. "
            "Nosotros entregamos, instalamos todo profesionalmente y regresamos a recogerlo "
            "despues de tu celebracion.\n\n"
            f"Un solo precio: {PRICE}. Todo incluido.\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            "Tus invitados hablaran de los banos — de la mejor manera posible.\n\n"
            f"Llama o texto: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "outdoor",
        "photo_set": "C",
    },
    # ── 8. Baby Shower Elegance ──────────────────────────────────────────
    {
        "angle_name": "baby_shower_elegance",
        "title_en": "Elegant Restrooms for Your Baby Shower",
        "description_en": (
            "Planning a beautiful baby shower at home or in a garden? Make sure the restroom "
            "experience is just as elegant as your decorations.\n\n"
            "Our luxury 4-stall restroom trailer features real flushing toilets, running hot and "
            "cold water, hardwood-style floors, full-length vanity mirrors, soft LED lighting, and "
            "a Bluetooth speaker for ambient music. Climate-controlled with AC and heat.\n\n"
            "Your expecting mama and all your guests stay comfortable without crowding your home "
            "bathrooms. The trailer is clean, spacious, and beautifully finished — perfect for "
            "photo-worthy events.\n\n"
            f"All-inclusive: {PRICE}. Delivery, setup, and pickup handled by us.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Give your guests the comfort they deserve at this special celebration.\n\n"
            f"Free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Banos Elegantes para Tu Baby Shower",
        "description_es": (
            "Planeando un hermoso baby shower en casa o en un jardin? Asegurate de que la "
            "experiencia en los banos sea tan elegante como la decoracion.\n\n"
            "Nuestro trailer de lujo con 4 cabinas privadas tiene sanitarios reales con descarga "
            "de agua, agua corriente caliente y fria, pisos estilo madera, espejos de cuerpo "
            "entero, iluminacion LED suave y ambiental, y bocina Bluetooth para musica de fondo. "
            "Completamente climatizado con aire acondicionado y calefaccion.\n\n"
            "Tu futura mama y todos tus invitados se mantienen comodos sin saturar los banos de "
            "tu casa. El trailer es limpio, espacioso y con un acabado elegante — perfecto para "
            "eventos donde las fotos importan.\n\n"
            "Nosotros nos encargamos de todo: entrega, instalacion profesional y recoleccion.\n\n"
            f"Todo incluido: {PRICE}. Servimos {SERVICE_AREA_ES}.\n\n"
            "Dale a tus invitados la comodidad que merecen en esta celebracion especial.\n\n"
            f"Cotizacion gratis: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "outdoor",
        "photo_set": "D",
    },
    # ── 9. Graduation Celebration ────────────────────────────────────────
    {
        "angle_name": "graduation_celebration",
        "title_en": "Graduation Party? We've Got the Restrooms Covered",
        "description_en": (
            "Throwing a big graduation party at home? Between family, friends, and neighbors, "
            "your house bathrooms won't cut it.\n\n"
            "Our luxury 4-stall restroom trailer handles the crowd. Real flushing toilets, "
            "running water, hardwood-style floors, full-length mirrors, LED lighting, and a "
            "Bluetooth speaker. Climate-controlled with AC and heat — because grad parties in "
            "SoCal can get HOT.\n\n"
            "We pull up, set everything up on your driveway or yard, and pick it up the next day. "
            "You focus on celebrating the graduate.\n\n"
            f"Flat rate: {PRICE}. Delivery, setup, pickup — all included.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Your guests get luxury restrooms. You get zero bathroom stress. Win-win.\n\n"
            f"Book your date: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Fiesta de Graduacion? Los Banos Estan Cubiertos",
        "description_es": (
            "Gran fiesta de graduacion en casa? Entre familia, amigos y vecinos, los banos de tu "
            "casa no van a alcanzar. No dejes que tus invitados hagan fila toda la noche.\n\n"
            "Nuestro trailer de lujo con 4 cabinas privadas maneja la multitud sin problema. "
            "Sanitarios reales con descarga de agua, agua corriente caliente y fria, pisos estilo "
            "madera, espejos de cuerpo entero, iluminacion LED y bocina Bluetooth. Completamente "
            "climatizado con aire acondicionado y calefaccion — porque las fiestas de graduacion "
            "en SoCal pueden ponerse MUY calientes.\n\n"
            "Llegamos, instalamos todo profesionalmente en tu entrada o patio, y recogemos al "
            "dia siguiente. Tu solo enfocate en celebrar al graduado.\n\n"
            f"Tarifa fija: {PRICE}. Entrega, instalacion y recoleccion incluida.\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            "Tus invitados tendran banos de lujo. Tu tendras cero estres. Todos ganan.\n\n"
            f"Reserva tu fecha: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "outdoor",
        "photo_set": "A",
    },
    # ── 10. Holiday Party ────────────────────────────────────────────────
    {
        "angle_name": "holiday_party",
        "title_en": "Holiday Party Luxury — Skip the Porta-Potty",
        "description_en": (
            "Holiday party at home with the whole extended family? Your two bathrooms won't "
            "survive the night. Skip the porta potty and go luxury instead.\n\n"
            "Our 4-stall restroom trailer brings the comfort: real flushing toilets, running hot "
            "and cold water, hardwood-style floors, full-length mirrors, LED lighting, and a "
            "Bluetooth speaker for holiday tunes. Heated for those cool SoCal winter evenings.\n\n"
            "Perfect for Thanksgiving, Christmas, New Year's Eve, Hanukkah, and any holiday "
            "gathering that needs extra restroom capacity. We deliver, set up, and pick up "
            "so you can enjoy the party.\n\n"
            f"One rate: {PRICE}. All-inclusive.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Make this holiday gathering the one everyone remembers — for the right reasons.\n\n"
            f"Reserve now: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": "Fiesta de Temporada con Lujo — Olvida el Bano Portatil",
        "description_es": (
            "Fiesta de temporada en casa con toda la familia extendida? Tus dos banos no "
            "sobreviviran la noche. Olvida el bano portatil de plastico y elige lujo en su lugar.\n\n"
            "Nuestro trailer con 4 cabinas privadas ofrece sanitarios reales con descarga de agua, "
            "agua corriente caliente y fria, pisos estilo madera, espejos de cuerpo entero, "
            "iluminacion LED ambiental y bocina Bluetooth para musica navidena. Calefaccion para "
            "las noches frescas de invierno en SoCal para que tus invitados esten comodos.\n\n"
            "Perfecto para Accion de Gracias, Navidad, Ano Nuevo, Hanukkah y cualquier reunion "
            "familiar que necesite capacidad extra de banos. Nosotros entregamos, instalamos "
            "profesionalmente y recogemos despues de las fiestas.\n\n"
            f"Una tarifa: {PRICE}. Todo incluido.\n"
            f"Servimos {SERVICE_AREA_ES}.\n\n"
            "Haz que esta reunion familiar sea la que todos recuerden — por las razones correctas.\n\n"
            f"Reserva: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "category": "seasonal",
        "photo_set": "B",
    },
    # ── 11. Porta-Potty Comparison (EN only from here) ───────────────────
    {
        "angle_name": "porta_potty_comparison",
        "title_en": "Why Settle for a Porta-Potty?",
        "description_en": (
            "Porta potty: no AC, no running water, no mirrors, a chemical smell, and a plastic "
            "seat. Is that really what you want your guests to experience?\n\n"
            "Our luxury 4-stall restroom trailer is the alternative: real flushing toilets, running "
            "hot and cold water, hardwood-style floors, full-length vanity mirrors, LED lighting, "
            "Bluetooth speaker, and full climate control with AC and heat.\n\n"
            "Same event. Completely different restroom experience. Your guests will actually "
            "compliment the bathroom — something that has never happened at a porta potty.\n\n"
            "We handle delivery, professional setup, and pickup. You just show up and enjoy "
            "your event.\n\n"
            f"All of this for {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Stop settling. Your event deserves better.\n\n"
            f"Get your free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "comparison",
        "photo_set": "C",
    },
    # ── 12. Guest Comfort Focus ──────────────────────────────────────────
    {
        "angle_name": "guest_comfort_focus",
        "title_en": "Your Guests Will Actually COMPLIMENT the Bathroom",
        "description_en": (
            "Sounds crazy, right? But it happens at every event we serve. Guests walk out of our "
            "luxury restroom trailer and say: 'Wait, THAT was a portable bathroom?'\n\n"
            "Our 4-stall trailer features real flushing toilets, running hot and cold water, "
            "hardwood-style floors, full-length mirrors, interior LED lighting, and a Bluetooth "
            "speaker. Climate-controlled with AC in summer and heat in winter.\n\n"
            "It doesn't look like a trailer from the inside. It looks like a high-end hotel "
            "restroom — because that's the standard we built it to.\n\n"
            "We deliver, set up, and pick up. You just enjoy the compliments.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Book your date before someone else does.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "comparison",
        "photo_set": "D",
    },
    # ── 13. Host Reputation ──────────────────────────────────────────────
    {
        "angle_name": "host_reputation",
        "title_en": "Be the Host Everyone Talks About",
        "description_en": (
            "Great hosts think of everything — including the restrooms. When you bring in our "
            "luxury 4-stall restroom trailer, you become the host who went above and beyond.\n\n"
            "Real flushing toilets, running water, hardwood-style floors, full-length mirrors, "
            "LED lighting, and a Bluetooth speaker. Your guests step inside and feel like they're "
            "in a five-star hotel bathroom. Climate-controlled with AC and heat.\n\n"
            "Whether it's a wedding, birthday, graduation, quinceanera, or holiday gathering — "
            "this is the detail that sets your event apart from every other backyard party.\n\n"
            "We handle delivery, setup, and pickup. You handle the applause.\n\n"
            f"All-inclusive: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Be the host everyone remembers.\n\n"
            f"Book now: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "outdoor",
        "photo_set": "A",
    },
    # ── 14. Venue Requirement ────────────────────────────────────────────
    {
        "angle_name": "venue_requirement",
        "title_en": "No Restrooms at Your Venue? No Problem",
        "description_en": (
            "Found the perfect outdoor venue but it doesn't have restrooms? That's exactly "
            "why we exist.\n\n"
            "Our luxury 4-stall restroom trailer brings five-star facilities to any location. "
            "Real flushing toilets, running hot and cold water, hardwood-style floors, full-length "
            "mirrors, LED lighting, and a Bluetooth speaker. Full climate control — AC and heat.\n\n"
            "Vineyards, ranches, beaches, parks, private estates, rooftops, fields — we've "
            "delivered to all of them across SoCal. If there's a flat spot for our trailer, "
            "we can make it work.\n\n"
            "Delivery, setup, leveling, and pickup are all included in the price.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Don't let a lack of plumbing stop you from booking your dream venue.\n\n"
            f"Free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "outdoor",
        "photo_set": "B",
    },
    # ── 15. Summer Urgency ───────────────────────────────────────────────
    {
        "angle_name": "summer_urgency",
        "title_en": "Summer Dates Filling Fast — Book Now",
        "description_en": (
            "Summer is peak event season in SoCal and our dates are filling up FAST. If you have "
            "an outdoor wedding, graduation party, or any summer event, now is the time to lock "
            "in your luxury restroom trailer.\n\n"
            "Our 4-stall trailer features real flushing toilets, running water, hardwood-style "
            "floors, full-length mirrors, LED lighting, and a Bluetooth speaker. Full AC to keep "
            "your guests cool even on the hottest SoCal days.\n\n"
            "We deliver, set up, and pick up — all included in one flat rate. No surprise fees.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Don't wait until the last minute and miss your date. The best weekends in June, "
            "July, and August go first.\n\n"
            f"Reserve your date today: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "seasonal",
        "photo_set": "C",
    },
    # ── 16. Fall Wedding Season ──────────────────────────────────────────
    {
        "angle_name": "fall_wedding_season",
        "title_en": "Fall Wedding Season: Secure Your Luxury Restrooms",
        "description_en": (
            "Fall is the most popular wedding season in Southern California — perfect weather, "
            "golden light, and gorgeous outdoor venues. Make sure your restroom plan is as polished "
            "as the rest of your wedding.\n\n"
            "Our luxury 4-stall trailer features real flushing toilets, running water, "
            "hardwood-style floors, full-length mirrors, LED lighting, and a Bluetooth speaker. "
            "Climate-controlled for those warm fall afternoons and cool evenings.\n\n"
            "We deliver to vineyards, estates, ranches, and backyards across SoCal. "
            "Setup and pickup are included.\n\n"
            f"All-inclusive rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "September through November weekends book fast. Secure your date before it's gone.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "seasonal",
        "photo_set": "D",
    },
    # ── 17. Spring Events ────────────────────────────────────────────────
    {
        "angle_name": "spring_events",
        "title_en": "Spring Event Season is Here",
        "description_en": (
            "Spring in SoCal means outdoor events are back in full swing. Weddings, graduations, "
            "quinceaneras, baby showers, and corporate picnics — everyone is celebrating outside "
            "and the calendar is filling up fast.\n\n"
            "Don't forget about restrooms. Our luxury 4-stall restroom trailer brings real "
            "flushing toilets, running hot and cold water, hardwood-style floors, full-length "
            "mirrors, LED interior lighting, and a Bluetooth speaker to any venue. "
            "Climate-controlled with AC for warm days and heat for cool evenings.\n\n"
            "We handle delivery, professional setup, leveling, and pickup after your event. You "
            "just enjoy the spring weather and your celebration without worrying about restrooms.\n\n"
            f"Flat rate: {PRICE}. Delivery, setup, and pickup included.\n"
            f"Serving {SERVICE_AREA}.\n\n"
            "Spring dates are already booking — don't wait and miss yours.\n\n"
            f"Get a free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "seasonal",
        "photo_set": "A",
    },
    # ── 18. Winter Holiday ───────────────────────────────────────────────
    {
        "angle_name": "winter_holiday",
        "title_en": "Winter Holiday Parties Deserve Luxury",
        "description_en": (
            "Hosting the family holiday party this year? Between Thanksgiving, Christmas, and "
            "New Year's Eve, your home bathrooms are going to be working overtime. Don't make "
            "Grandma wait in line behind 30 cousins.\n\n"
            "Our luxury 4-stall restroom trailer takes the pressure off. Real flushing toilets, "
            "running hot and cold water, hardwood-style floors, full-length mirrors, LED interior "
            "lighting, and a Bluetooth speaker for holiday music. Heated for those chilly SoCal "
            "winter nights so every guest stays warm and comfortable.\n\n"
            "Drops right into your driveway or yard. We handle delivery, professional setup, "
            "leveling, and come back to pick it up after the holidays. Zero hassle for you.\n\n"
            f"One rate: {PRICE}. All-inclusive. Serving {SERVICE_AREA}.\n\n"
            "Give your holiday guests the five-star treatment they deserve.\n\n"
            f"Reserve now: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "seasonal",
        "photo_set": "B",
    },
    # ── 19. Large Event (200+) ───────────────────────────────────────────
    {
        "angle_name": "large_event_200_plus",
        "title_en": "Hosting 200+ Guests? Our 4-Stall Trailer Has You Covered",
        "description_en": (
            "Big events need big restroom capacity. Our luxury 4-stall restroom trailer "
            "comfortably handles crowds of 200 or more, keeping lines short and guests happy.\n\n"
            "Each stall is a private room with real flushing toilets, running water, "
            "hardwood-style floors, full-length mirrors, LED lighting, and a Bluetooth speaker. "
            "Climate-controlled with AC and heat.\n\n"
            "Whether it's a wedding, festival, corporate event, or large family celebration — "
            "4 stalls running simultaneously means your guests aren't stuck waiting.\n\n"
            "We deliver to any venue in SoCal, handle all setup and leveling, and pick up when "
            "your event wraps.\n\n"
            f"All-inclusive rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Large events need real restrooms, not plastic boxes.\n\n"
            f"Get a quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "outdoor",
        "photo_set": "C",
    },
    # ── 20. Intimate Event (<50) ─────────────────────────────────────────
    {
        "angle_name": "intimate_event_small",
        "title_en": "Even Small Events Deserve Big Luxury",
        "description_en": (
            "Hosting an intimate gathering of 30 to 50 guests? You still deserve luxury "
            "restrooms — maybe even more so, because small events are all about the details.\n\n"
            "Our 4-stall restroom trailer features real flushing toilets, running water, "
            "hardwood-style floors, full-length mirrors, LED lighting, and a Bluetooth speaker. "
            "Climate-controlled with AC and heat.\n\n"
            "Perfect for intimate weddings, dinner parties, anniversary celebrations, and "
            "small corporate gatherings. The trailer looks elegant and blends right into "
            "your venue.\n\n"
            "We handle delivery, setup, and pickup.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Small event, big impression. That's our specialty.\n\n"
            f"Book your date: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "niche",
        "photo_set": "D",
    },
    # ── 21. AC/Heat Feature ──────────────────────────────────────────────
    {
        "angle_name": "ac_heat_feature",
        "title_en": "Climate-Controlled Comfort — AC in Summer, Heat in Winter",
        "description_en": (
            "SoCal summers hit 100 degrees. SoCal winter nights dip into the 40s. Nobody wants "
            "to step into a hot plastic box or a freezing porta potty.\n\n"
            "Our luxury 4-stall restroom trailer is fully climate-controlled. Air conditioning "
            "keeps it cool during summer events. Heating keeps it warm during winter parties. "
            "Your guests stay comfortable no matter when your event takes place.\n\n"
            "Plus: real flushing toilets, running hot and cold water, hardwood-style floors, "
            "full-length mirrors, LED lighting, and a Bluetooth speaker.\n\n"
            "We handle delivery, setup, and pickup.\n\n"
            f"Flat rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Climate-controlled restrooms — just one more reason our trailer beats every "
            "porta potty out there.\n\n"
            f"Free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "amenity",
        "photo_set": "A",
    },
    # ── 22. Running Water Feature ────────────────────────────────────────
    {
        "angle_name": "running_water_feature",
        "title_en": "Real Flushing Toilets & Running Water",
        "description_en": (
            "The number one complaint about porta potties? No running water. No flushing. "
            "Just chemicals, a plastic seat, and hand sanitizer that barely works.\n\n"
            "Our luxury 4-stall restroom trailer has real flushing toilets and running hot and "
            "cold water in every stall. Soap dispensers, paper towels, and a proper handwash "
            "station. Your guests wash their hands with real water — like they would at home. "
            "Because basic hygiene shouldn't be a luxury at an event.\n\n"
            "On top of that: hardwood-style floors, full-length vanity mirrors, LED interior "
            "lighting, a Bluetooth speaker, and full climate control with AC and heat.\n\n"
            "We deliver, set up, level, and pick up. Everything included in one flat price.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Real water. Real toilets. Real luxury. The way it should be.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "amenity",
        "photo_set": "B",
    },
    # ── 23. LED & Mirrors Feature ────────────────────────────────────────
    {
        "angle_name": "led_mirrors_feature",
        "title_en": "LED Lighting, Full-Length Mirrors, Music",
        "description_en": (
            "Our luxury restroom trailer isn't just functional — it's an experience. Step inside "
            "and you're greeted by soft LED ambient lighting, full-length vanity mirrors, and "
            "music from our Bluetooth speaker.\n\n"
            "Every detail is designed to make your guests feel pampered. Real flushing toilets, "
            "running hot and cold water, hardwood-style floors, and climate control with AC and "
            "heat. Four private stalls so there's never a long wait.\n\n"
            "Perfect for weddings, quinceaneras, and any event where aesthetics matter. The "
            "trailer looks incredible in photos — some couples even include it in their event "
            "shots.\n\n"
            "We handle everything: delivery, setup, and pickup.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Restrooms that look as good as they function.\n\n"
            f"Book now: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "amenity",
        "photo_set": "C",
    },
    # ── 24. Delivery Included ────────────────────────────────────────────
    {
        "angle_name": "delivery_included",
        "title_en": "Full Service: Delivery, Setup, and Pickup Included",
        "description_en": (
            "No hidden fees. No extra charges. When you rent our luxury 4-stall restroom trailer, "
            "delivery, professional setup, leveling, and pickup are all included in one flat "
            "price.\n\n"
            "Here's how it works: we deliver the trailer to your location the day before or "
            "morning of your event. We level it, connect the water, and make sure everything is "
            "perfect. After your event, we come back and haul it away. You don't lift a finger.\n\n"
            "Inside: real flushing toilets, running water, hardwood-style floors, full-length "
            "mirrors, LED lighting, Bluetooth speaker, and climate control with AC and heat.\n\n"
            f"One price: {PRICE}. No surprises. Serving {SERVICE_AREA}.\n\n"
            "Full service, full luxury, zero hassle.\n\n"
            f"Get your quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "amenity",
        "photo_set": "D",
    },
    # ── 25. Testimonial Style ────────────────────────────────────────────
    {
        "angle_name": "testimonial_style",
        "title_en": "See Why Our Clients Call Us the Surprise Hit of Their Event",
        "description_en": (
            "We hear it after almost every event: 'The restroom trailer was the surprise hit of "
            "the party.' Guests walk in expecting a porta potty and walk out saying, 'That was "
            "nicer than my bathroom at home.'\n\n"
            "Our luxury 4-stall trailer delivers: real flushing toilets, running hot and cold "
            "water, hardwood-style floors, full-length mirrors, LED lighting, and a Bluetooth "
            "speaker. Full climate control with AC and heat.\n\n"
            "From backyard weddings to corporate galas, every client tells us the same thing — "
            "the restrooms were a hit.\n\n"
            "We handle delivery, setup, and pickup. One flat rate, no surprises.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Be the next host who gets those compliments.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "testimonial",
        "photo_set": "A",
    },
    # ── 26. Photo-Ready ──────────────────────────────────────────────────
    {
        "angle_name": "photo_ready",
        "title_en": "Instagram-Worthy Restrooms for Your Event",
        "description_en": (
            "In the age of social media, every corner of your event gets photographed — including "
            "the restrooms. Make sure they're photo-ready.\n\n"
            "Our luxury 4-stall restroom trailer features soft LED ambient lighting, full-length "
            "vanity mirrors, hardwood-style floors, and a sleek modern interior that looks "
            "stunning in photos. Real flushing toilets, running water, Bluetooth speaker, and "
            "full climate control with AC and heat.\n\n"
            "Brides use our mirrors for touch-ups. Guests snap selfies in the lighting. Event "
            "planners love the aesthetic. It's not just a restroom — it's part of the decor.\n\n"
            "We deliver, set up, and pick up.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Give your event the finishing touch it deserves.\n\n"
            f"Book your date: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "amenity",
        "photo_set": "B",
    },
    # ── 27. SFV Local ────────────────────────────────────────────────────
    {
        "angle_name": "sfv_local",
        "title_en": "Proudly Serving San Fernando Valley & Greater LA",
        "description_en": (
            "We're a local SoCal company and we know the Valley, LA, and surrounding areas like "
            "the back of our hand. From Encino to Sylmar, Burbank to Calabasas — we deliver our "
            "luxury restroom trailer to every corner of the San Fernando Valley and Greater LA.\n\n"
            "Our 4-stall trailer features real flushing toilets, running water, hardwood-style "
            "floors, full-length mirrors, LED lighting, and a Bluetooth speaker. Full climate "
            "control with AC and heat.\n\n"
            "We also serve Ventura County, Santa Clarita, Pasadena, the Westside, South Bay, "
            "and everywhere in between.\n\n"
            "Delivery, setup, and pickup included.\n\n"
            f"Flat rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Local company, luxury service, fair price.\n\n"
            f"Call or text: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "niche",
        "photo_set": "C",
    },
    # ── 28. Price Value ──────────────────────────────────────────────────
    {
        "angle_name": "price_value",
        "title_en": "The Best $999 You'll Spend on Your Event",
        "description_en": (
            "You're spending thousands on food, decorations, and entertainment. For just "
            f"{PRICE}, you can make sure your guests have luxury restrooms too.\n\n"
            "Our 4-stall restroom trailer features real flushing toilets, running water, "
            "hardwood-style floors, full-length mirrors, LED lighting, and a Bluetooth speaker. "
            "Full climate control with AC and heat.\n\n"
            "Compare that to renting two porta potties for $400 — which your guests will hate — "
            "and the choice is obvious. For a few hundred more, you get a five-star restroom "
            "experience that elevates your entire event.\n\n"
            "Delivery, setup, and pickup are all included. One price, no add-ons.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "The smartest upgrade you'll make for your event.\n\n"
            f"Free quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "comparison",
        "photo_set": "D",
    },
    # ── 29. Last-Minute Availability ─────────────────────────────────────
    {
        "angle_name": "last_minute_availability",
        "title_en": "Last-Minute Event? We Might Still Have Your Date",
        "description_en": (
            "Event coming up soon and you just realized you need real restrooms? Don't panic — "
            "we specialize in making things happen fast. We often have last-minute availability "
            "for our luxury 4-stall restroom trailer.\n\n"
            "Real flushing toilets, running hot and cold water, hardwood-style floors, full-length "
            "mirrors, LED interior lighting, Bluetooth speaker, and full climate control with AC "
            "and heat. Everything your guests need for a five-star restroom experience, even on "
            "short notice.\n\n"
            "We can often deliver as soon as the day before your event. Delivery, professional "
            "setup, leveling, and pickup are all included in one flat rate. No rush fees, no "
            "surprise charges.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Call us today and we'll check availability for your date. Even if it's this "
            "weekend, we'll do our best to make it happen.\n\n"
            f"Call or text NOW: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "niche",
        "photo_set": "A",
    },
    # ── 30. Weekend Warrior ──────────────────────────────────────────────
    {
        "angle_name": "weekend_warrior",
        "title_en": "Weekend Events Are Our Specialty",
        "description_en": (
            "Friday delivery. Saturday event. Sunday pickup. That's our bread and butter.\n\n"
            "Most events happen on weekends and we've got the logistics dialed in. Our luxury "
            "4-stall restroom trailer arrives the day before, gets set up and leveled, and is "
            "ready for your guests by the time the first one shows up.\n\n"
            "Inside: real flushing toilets, running water, hardwood-style floors, full-length "
            "mirrors, LED lighting, and a Bluetooth speaker. Full climate control with AC and "
            "heat.\n\n"
            "We pick up the morning after so you don't have to worry about anything on event "
            "day.\n\n"
            f"All-inclusive rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Weekends are busy — book your date early to lock it in.\n\n"
            f"Reserve now: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "niche",
        "photo_set": "B",
    },
    # ── 31. Backyard Wedding ─────────────────────────────────────────────
    {
        "angle_name": "backyard_wedding",
        "title_en": "Backyard Wedding? Luxury Restrooms Make All the Difference",
        "description_en": (
            "Backyard weddings are beautiful, personal, and meaningful. But let's be honest — "
            "your house has two bathrooms and you've invited 120 guests. The math doesn't work.\n\n"
            "Our luxury 4-stall restroom trailer solves the problem instantly. Real flushing "
            "toilets, running water, hardwood-style floors, full-length mirrors, LED lighting, "
            "and a Bluetooth speaker. Climate-controlled with AC and heat. It tucks into your "
            "driveway or side yard and looks like it belongs there.\n\n"
            "Your guests get a five-star restroom experience. You get peace of mind knowing "
            "nobody is waiting in a line that wraps around the house.\n\n"
            "Delivery, setup, and pickup included.\n\n"
            f"Rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "The one upgrade every backyard bride wishes she'd made sooner.\n\n"
            f"Book your date: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "wedding",
        "photo_set": "C",
    },
    # ── 32. Destination Event ────────────────────────────────────────────
    {
        "angle_name": "destination_event",
        "title_en": "Bringing 5-Star Restrooms to Any Location in Greater LA",
        "description_en": (
            "Mountain ranch? Beach bluff? Vineyard in the hills? Desert estate? If you can dream "
            "the venue, we can bring luxury restrooms to it.\n\n"
            "Our 4-stall restroom trailer is built to go anywhere. We've delivered to remote "
            "estates, hilltop vineyards, beachside parks, and everything in between. Real flushing "
            "toilets, running water, hardwood-style floors, full-length mirrors, LED lighting, "
            "and a Bluetooth speaker. Full climate control with AC and heat.\n\n"
            "No matter how remote the location, we deliver, set up, level the trailer, and come "
            "back to pick it up after your event.\n\n"
            f"Flat rate: {PRICE}. Serving {SERVICE_AREA}.\n\n"
            "Your dream venue shouldn't mean compromise on restrooms. We bring the luxury to you.\n\n"
            f"Get a quote: {BIZ_PHONE}\n"
            f"Email: {BIZ_EMAIL}\n"
            f"{BIZ_WEBSITE}"
        ),
        "title_es": None,
        "description_es": None,
        "category": "niche",
        "photo_set": "D",
    },
]


# ══════════════════════════════════════════════════════════════════════════
# POSTING ENGINE CLASS
# ══════════════════════════════════════════════════════════════════════════

class PostingEngine:
    """Database-backed daily posting engine with 32 rotating angles.

    Usage::

        engine = PostingEngine()
        posts = engine.generate_daily_posts()
        slack_msg = engine.format_slack_message(posts)
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = Path(db_path) if db_path else Path.home() / ".nexus" / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_tables()
        self._seed_angles()

    # ── DB helpers ────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_tables(self):
        """Create posting_angles and posting_log tables if they don't exist.

        Handles migration from the old schema (angle_number, niche, hashtags, etc.)
        by detecting the old columns and replacing the table with the new schema.
        """
        try:
            conn = self._conn()

            # Check if posting_angles exists with old schema (has 'angle_number' column)
            try:
                cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(posting_angles)").fetchall()
                }
                if cols and "angle_name" not in cols:
                    log.info("Old posting_angles schema detected — migrating to new schema")
                    conn.execute("DROP TABLE IF EXISTS posting_angles")
                    conn.commit()
            except Exception:
                pass  # Table doesn't exist yet

            # Check if posting_log exists with old schema (has 'body' instead of 'content')
            try:
                log_cols = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(posting_log)").fetchall()
                }
                if log_cols and "content" not in log_cols:
                    log.info("Old posting_log schema detected — migrating to new schema")
                    conn.execute("DROP TABLE IF EXISTS posting_log")
                    conn.commit()
            except Exception:
                pass  # Table doesn't exist yet

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS posting_angles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    angle_name TEXT NOT NULL UNIQUE,
                    title_en TEXT NOT NULL,
                    description_en TEXT NOT NULL,
                    title_es TEXT,
                    description_es TEXT,
                    category TEXT,
                    photo_set TEXT DEFAULT 'A',
                    last_used_at TEXT,
                    times_used INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS posting_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    angle_id INTEGER,
                    platform TEXT,
                    language TEXT,
                    posted_at TEXT DEFAULT (datetime('now')),
                    content TEXT,
                    FOREIGN KEY (angle_id) REFERENCES posting_angles(id)
                );
            """)
            conn.commit()
            conn.close()
            log.info("Tables ensured: posting_angles, posting_log")
        except Exception as e:
            log.error("Failed to create tables: %s", e)

    def _seed_angles(self):
        """Insert the 32 seed angles if the table is empty."""
        try:
            conn = self._conn()
            count = conn.execute("SELECT COUNT(*) FROM posting_angles").fetchone()[0]
            if count >= len(SEED_ANGLES):
                log.info("Angles already seeded (%d rows)", count)
                conn.close()
                return

            for angle in SEED_ANGLES:
                conn.execute(
                    """INSERT OR IGNORE INTO posting_angles
                       (angle_name, title_en, description_en, title_es, description_es,
                        category, photo_set)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        angle["angle_name"],
                        angle["title_en"],
                        angle["description_en"],
                        angle.get("title_es"),
                        angle.get("description_es"),
                        angle.get("category", "general"),
                        angle.get("photo_set", "A"),
                    ),
                )
            conn.commit()
            final = conn.execute("SELECT COUNT(*) FROM posting_angles").fetchone()[0]
            conn.close()
            log.info("Seeded %d posting angles (total now: %d)", len(SEED_ANGLES), final)
        except Exception as e:
            log.error("Failed to seed angles: %s", e)

    # ── Angle selection ──────────────────────────────────────────────────

    def get_todays_angle(self) -> dict:
        """Pick the best angle for today.

        Rules:
          - Not used in the last 30 days
          - Photo set not used in the last 7 days
          - Prefer least-used angles overall (times_used ASC)
        """
        try:
            conn = self._conn()
            cutoff_30d = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            cutoff_7d = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

            # Photo sets used in last 7 days
            recent_photo_sets = {
                row[0]
                for row in conn.execute(
                    """SELECT DISTINCT pa.photo_set
                       FROM posting_log pl
                       JOIN posting_angles pa ON pa.id = pl.angle_id
                       WHERE pl.posted_at > ?""",
                    (cutoff_7d,),
                ).fetchall()
            }

            # Best angle: not used in 30 days, photo set not used in 7 days, least-used
            rows = conn.execute(
                """SELECT * FROM posting_angles
                   WHERE (last_used_at IS NULL OR last_used_at < ?)
                   ORDER BY times_used ASC, RANDOM()""",
                (cutoff_30d,),
            ).fetchall()

            # Prefer angles whose photo set hasn't been used recently
            best = None
            for row in rows:
                if row["photo_set"] not in recent_photo_sets:
                    best = dict(row)
                    break

            # Fallback: ignore photo set constraint
            if not best and rows:
                best = dict(rows[0])

            # Final fallback: just pick the least used angle
            if not best:
                row = conn.execute(
                    "SELECT * FROM posting_angles ORDER BY times_used ASC, RANDOM() LIMIT 1"
                ).fetchone()
                if row:
                    best = dict(row)

            conn.close()

            if best:
                log.info(
                    "Today's angle: #%d '%s' (category=%s, photo_set=%s, used %d times)",
                    best["id"], best["angle_name"], best["category"],
                    best["photo_set"], best["times_used"],
                )
            else:
                log.warning("No angles available in database")

            return best or {}

        except Exception as e:
            log.error("Error selecting today's angle: %s", e)
            return {}

    # ── Post generation ──────────────────────────────────────────────────

    def generate_daily_posts(self) -> list[dict]:
        """Generate 4 posts for today:
        1. FB Marketplace EN
        2. FB Marketplace ES
        3. Craigslist EN
        4. Craigslist ES

        Returns list of dicts with keys:
            platform, language, title, body, angle_id, angle_name, photo_set
        """
        angle = self.get_todays_angle()
        if not angle:
            log.error("Cannot generate posts — no angle available")
            return []

        posts = []

        # Determine if this angle has Spanish content
        has_es = bool(angle.get("title_es") and angle.get("description_es"))

        # 1. FB Marketplace EN
        fb_en = self.format_fb_marketplace_post(angle, language="en")
        posts.append({
            "platform": "fb_marketplace",
            "language": "en",
            "title": angle["title_en"],
            "body": fb_en,
            "angle_id": angle["id"],
            "angle_name": angle["angle_name"],
            "photo_set": angle["photo_set"],
        })

        # 2. FB Marketplace ES
        if has_es:
            fb_es = self.format_fb_marketplace_post(angle, language="es")
            posts.append({
                "platform": "fb_marketplace",
                "language": "es",
                "title": angle["title_es"],
                "body": fb_es,
                "angle_id": angle["id"],
                "angle_name": angle["angle_name"],
                "photo_set": angle["photo_set"],
            })

        # 3. Craigslist EN
        cl_en = self.format_craigslist_post(angle, language="en")
        posts.append({
            "platform": "craigslist",
            "language": "en",
            "title": angle["title_en"],
            "body": cl_en,
            "angle_id": angle["id"],
            "angle_name": angle["angle_name"],
            "photo_set": angle["photo_set"],
        })

        # 4. Craigslist ES
        if has_es:
            cl_es = self.format_craigslist_post(angle, language="es")
            posts.append({
                "platform": "craigslist",
                "language": "es",
                "title": angle["title_es"],
                "body": cl_es,
                "angle_id": angle["id"],
                "angle_name": angle["angle_name"],
                "photo_set": angle["photo_set"],
            })

        # Mark this angle as used
        self._mark_angle_used(angle["id"])

        # Record each post in posting_log
        for post in posts:
            self.record_post(
                angle_id=post["angle_id"],
                platform=post["platform"],
                language=post["language"],
                content=post["body"],
            )

        log.info(
            "Generated %d posts for angle '%s' (id=%d)",
            len(posts), angle["angle_name"], angle["id"],
        )
        return posts

    def _mark_angle_used(self, angle_id: int):
        """Update last_used_at and increment times_used for an angle."""
        try:
            conn = self._conn()
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """UPDATE posting_angles
                   SET last_used_at = ?, times_used = times_used + 1
                   WHERE id = ?""",
                (now, angle_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            log.error("Failed to mark angle %d as used: %s", angle_id, e)

    # ── Formatters ───────────────────────────────────────────────────────

    def format_fb_marketplace_post(self, angle: dict, language: str = "en") -> str:
        """Format for Facebook Marketplace: title, price, category, description, location, tags."""
        if language == "es" and angle.get("title_es"):
            title = angle["title_es"]
            desc = angle["description_es"]
            location_label = "Ubicacion"
            price_label = "Precio"
            category_label = "Categoria"
            tags_label = "Etiquetas"
            category_value = "Servicios para Eventos"
        else:
            title = angle["title_en"]
            desc = angle["description_en"]
            location_label = "Location"
            price_label = "Price"
            category_label = "Category"
            tags_label = "Tags"
            category_value = "Event Services"

        # Craigslist region rotation by day-of-year
        day_of_year = datetime.now(LA_TZ).timetuple().tm_yday
        region = CL_REGIONS[day_of_year % len(CL_REGIONS)]

        lines = [
            title,
            "",
            desc,
            "",
            f"{price_label}: {PRICE}",
            f"{category_label}: {category_value}",
            f"{location_label}: {region}, CA",
            "",
        ]

        # Tags
        tag_pool_en = [
            "luxury restroom", "portable bathroom", "restroom trailer", "wedding restroom",
            "event rental", "porta potty alternative", "SoCal events", "outdoor wedding",
            "party rental", "Los Angeles",
        ]
        tag_pool_es = [
            "bano de lujo", "bano portatil", "trailer de bano", "bano para bodas",
            "renta para eventos", "alternativa porta potty", "eventos SoCal", "boda al aire libre",
            "renta para fiestas", "Los Angeles",
        ]
        tags = tag_pool_es if language == "es" else tag_pool_en
        lines.append(f"{tags_label}: {', '.join(tags)}")

        return "\n".join(lines)

    def format_craigslist_post(self, angle: dict, language: str = "en") -> str:
        """Format for Craigslist with HTML body, title, location."""
        if language == "es" and angle.get("title_es"):
            title = angle["title_es"]
            desc = angle["description_es"]
        else:
            title = angle["title_en"]
            desc = angle["description_en"]

        day_of_year = datetime.now(LA_TZ).timetuple().tm_yday
        region = CL_REGIONS[day_of_year % len(CL_REGIONS)]

        # Convert newlines to <br> for CL HTML
        desc_html = desc.replace("\n\n", "</p><p>").replace("\n", "<br>")

        cl_body = dedent(f"""\
            <h2>{title}</h2>

            <p>{desc_html}</p>

            <p><strong>Posting ID:</strong> zoar-{angle.get('id', 0):03d}-{language}</p>
            <p><strong>Location:</strong> {region}, CA</p>
            <p><strong>Category:</strong> Services &gt; Event Services</p>
        """)

        return cl_body.strip()

    def format_instagram_post(self, angle: dict) -> str:
        """Format for Instagram: caption with hashtags, photo suggestion."""
        title = angle["title_en"]
        desc = angle["description_en"]

        # Trim description for IG (keep it punchy, ~150 words max)
        paragraphs = desc.strip().split("\n\n")
        caption_body = "\n\n".join(paragraphs[:3])  # First 3 paragraphs

        # Add CTA
        caption_body += "\n\nBook your date: (424) 235-8979"
        caption_body += "\nzoarbathroomrental.com"

        # Hashtag block (separate line at bottom)
        hashtags = (
            "#luxuryrestroom #restroomtrailer #portablebathroom #eventrentals "
            "#weddingplanning #outdoorwedding #SoCalEvents #LosAngeles "
            "#SanFernandoValley #quinceaneraparty #partyrentals #eventplanner "
            "#weddingday #luxuryevents #portapottyalternative"
        )

        return f"{caption_body}\n\n.\n.\n.\n{hashtags}"

    def format_fb_page_post(self, angle: dict) -> str:
        """Format for Facebook Page: engaging post with link."""
        title = angle["title_en"]
        desc = angle["description_en"]

        # FB Page posts are conversational, shorter than marketplace listings
        paragraphs = desc.strip().split("\n\n")
        post_body = "\n\n".join(paragraphs[:3])

        post_body += "\n\nStarting at $999 — delivery, setup, and pickup included."
        post_body += "\n\nBook your date: (424) 235-8979"
        post_body += "\nzoarbathroomrental.com"

        return post_body

    def generate_all_daily_posts(self) -> list[dict]:
        """Generate 6 posts for today (4 marketplace + IG + FB Page).

        Returns list of dicts with keys:
            platform, language, title, body, angle_id, angle_name, photo_set
        """
        angle = self.get_todays_angle()
        if not angle:
            log.error("Cannot generate posts — no angle available")
            return []

        posts = []
        has_es = bool(angle.get("title_es") and angle.get("description_es"))

        # 1. FB Marketplace EN
        posts.append({
            "platform": "fb_marketplace", "language": "en",
            "title": angle["title_en"],
            "body": self.format_fb_marketplace_post(angle, "en"),
            "angle_id": angle["id"], "angle_name": angle["angle_name"],
            "photo_set": angle["photo_set"],
        })

        # 2. FB Marketplace ES
        if has_es:
            posts.append({
                "platform": "fb_marketplace", "language": "es",
                "title": angle["title_es"],
                "body": self.format_fb_marketplace_post(angle, "es"),
                "angle_id": angle["id"], "angle_name": angle["angle_name"],
                "photo_set": angle["photo_set"],
            })

        # 3. Craigslist EN
        posts.append({
            "platform": "craigslist", "language": "en",
            "title": angle["title_en"],
            "body": self.format_craigslist_post(angle, "en"),
            "angle_id": angle["id"], "angle_name": angle["angle_name"],
            "photo_set": angle["photo_set"],
        })

        # 4. Craigslist ES
        if has_es:
            posts.append({
                "platform": "craigslist", "language": "es",
                "title": angle["title_es"],
                "body": self.format_craigslist_post(angle, "es"),
                "angle_id": angle["id"], "angle_name": angle["angle_name"],
                "photo_set": angle["photo_set"],
            })

        # 5. Instagram Post (English only)
        posts.append({
            "platform": "instagram", "language": "en",
            "title": angle["title_en"],
            "body": self.format_instagram_post(angle),
            "angle_id": angle["id"], "angle_name": angle["angle_name"],
            "photo_set": angle["photo_set"],
        })

        # 6. Facebook Page Post (English only)
        posts.append({
            "platform": "fb_page", "language": "en",
            "title": angle["title_en"],
            "body": self.format_fb_page_post(angle),
            "angle_id": angle["id"], "angle_name": angle["angle_name"],
            "photo_set": angle["photo_set"],
        })

        # Mark angle as used
        self._mark_angle_used(angle["id"])

        # Record each post
        for post in posts:
            self.record_post(post["angle_id"], post["platform"], post["language"], post["body"])

        log.info("Generated %d posts (incl IG + FB Page) for angle '%s'", len(posts), angle["angle_name"])
        return posts

    def format_slack_message(self, posts: list[dict]) -> str:
        """Format all posts into a single Slack message for #posting channel."""
        now_la = datetime.now(LA_TZ)
        date_str = now_la.strftime("%A, %B %d, %Y")

        lines = [
            f"*DAILY POSTING PACKAGE — {date_str}*",
            "=" * 40,
            "",
        ]

        for i, post in enumerate(posts, 1):
            platform_label = {
                "fb_marketplace": "FB Marketplace",
                "craigslist": "Craigslist",
                "instagram": "Instagram",
                "fb_page": "Facebook Page",
            }.get(post["platform"], post["platform"])

            lang_label = "EN" if post["language"] == "en" else "ES"
            lang_flag = ":flag-us:" if post["language"] == "en" else ":flag-mx:"

            lines.append(f"*{i}. {platform_label} ({lang_label})* {lang_flag}")
            lines.append(f"*Title:* {post['title']}")
            lines.append(f"*Photo Set:* {post['photo_set']}")
            lines.append(f"*Angle:* {post['angle_name']}")
            lines.append("```")
            lines.append(post["body"])
            lines.append("```")
            lines.append("")

        lines.append("-" * 40)
        lines.append(
            f"_Angle #{posts[0]['angle_id']} | "
            f"Photo set {posts[0]['photo_set']} | "
            f"{len(posts)} posts generated_"
            if posts else "_No posts generated_"
        )

        return "\n".join(lines)

    # ── Recording & History ──────────────────────────────────────────────

    def record_post(self, angle_id: int, platform: str, language: str, content: str):
        """Record a generated post in posting_log."""
        try:
            conn = self._conn()
            conn.execute(
                """INSERT INTO posting_log (angle_id, platform, language, content)
                   VALUES (?, ?, ?, ?)""",
                (angle_id, platform, language, content),
            )
            conn.commit()
            conn.close()
            log.info("Recorded post: angle=%d, platform=%s, lang=%s", angle_id, platform, language)
        except Exception as e:
            log.error("Failed to record post: %s", e)

    def get_posting_history(self, days: int = 30) -> list[dict]:
        """Return posting history for the last N days."""
        try:
            conn = self._conn()
            cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute(
                """SELECT pl.id, pl.angle_id, pa.angle_name, pa.category,
                          pl.platform, pl.language, pl.posted_at
                   FROM posting_log pl
                   LEFT JOIN posting_angles pa ON pa.id = pl.angle_id
                   WHERE pl.posted_at > ?
                   ORDER BY pl.posted_at DESC""",
                (cutoff,),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error("Failed to get posting history: %s", e)
            return []

    def get_posting_calendar(self) -> str:
        """Show which angles were posted when, formatted as a readable calendar."""
        try:
            conn = self._conn()
            rows = conn.execute(
                """SELECT date(pl.posted_at) as post_date,
                          pa.angle_name, pa.category, pa.photo_set,
                          pl.platform, pl.language
                   FROM posting_log pl
                   LEFT JOIN posting_angles pa ON pa.id = pl.angle_id
                   ORDER BY pl.posted_at DESC
                   LIMIT 120"""
            ).fetchall()
            conn.close()

            if not rows:
                return "No posting history found."

            lines = ["POSTING CALENDAR", "=" * 60]
            current_date = None

            for row in rows:
                row = dict(row)
                post_date = row["post_date"]
                if post_date != current_date:
                    current_date = post_date
                    lines.append("")
                    lines.append(f"--- {post_date} ---")

                platform_short = {
                    "fb_marketplace": "FB",
                    "craigslist": "CL",
                }.get(row["platform"], row["platform"] or "?")

                lang = (row["language"] or "?").upper()
                lines.append(
                    f"  [{platform_short}/{lang}] {row['angle_name'] or '?'} "
                    f"({row['category'] or '?'}) photo={row['photo_set'] or '?'}"
                )

            # Stats
            conn2 = self._conn()
            total = conn2.execute("SELECT COUNT(*) FROM posting_log").fetchone()[0]
            angles_used = conn2.execute(
                "SELECT COUNT(DISTINCT angle_id) FROM posting_log"
            ).fetchone()[0]
            total_angles = conn2.execute("SELECT COUNT(*) FROM posting_angles").fetchone()[0]
            conn2.close()

            lines.append("")
            lines.append("=" * 60)
            lines.append(f"Total posts: {total} | Angles used: {angles_used}/{total_angles}")

            return "\n".join(lines)

        except Exception as e:
            log.error("Failed to generate posting calendar: %s", e)
            return f"Error generating calendar: {e}"

    # ── Utilities ────────────────────────────────────────────────────────

    def get_all_angles(self) -> list[dict]:
        """Return all angles from the database."""
        try:
            conn = self._conn()
            rows = conn.execute(
                "SELECT * FROM posting_angles ORDER BY id"
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            log.error("Failed to get angles: %s", e)
            return []

    def get_angle_by_id(self, angle_id: int) -> Optional[dict]:
        """Return a single angle by ID."""
        try:
            conn = self._conn()
            row = conn.execute(
                "SELECT * FROM posting_angles WHERE id = ?", (angle_id,)
            ).fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            log.error("Failed to get angle %d: %s", angle_id, e)
            return None

    def get_stats(self) -> dict:
        """Return posting engine statistics."""
        try:
            conn = self._conn()
            total_angles = conn.execute("SELECT COUNT(*) FROM posting_angles").fetchone()[0]
            total_posts = conn.execute("SELECT COUNT(*) FROM posting_log").fetchone()[0]
            angles_with_es = conn.execute(
                "SELECT COUNT(*) FROM posting_angles WHERE title_es IS NOT NULL"
            ).fetchone()[0]

            # Posts in last 7 days
            cutoff_7d = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
            posts_7d = conn.execute(
                "SELECT COUNT(*) FROM posting_log WHERE posted_at > ?", (cutoff_7d,)
            ).fetchone()[0]

            # Posts in last 30 days
            cutoff_30d = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            posts_30d = conn.execute(
                "SELECT COUNT(*) FROM posting_log WHERE posted_at > ?", (cutoff_30d,)
            ).fetchone()[0]

            # Angles never used
            never_used = conn.execute(
                "SELECT COUNT(*) FROM posting_angles WHERE times_used = 0"
            ).fetchone()[0]

            # Category breakdown
            categories = {}
            for row in conn.execute(
                "SELECT category, COUNT(*) as cnt FROM posting_angles GROUP BY category"
            ).fetchall():
                categories[row[0]] = row[1]

            # Photo set breakdown
            photo_sets = {}
            for row in conn.execute(
                "SELECT photo_set, COUNT(*) as cnt FROM posting_angles GROUP BY photo_set"
            ).fetchall():
                photo_sets[row[0]] = row[1]

            conn.close()

            return {
                "total_angles": total_angles,
                "angles_with_spanish": angles_with_es,
                "total_posts_logged": total_posts,
                "posts_last_7_days": posts_7d,
                "posts_last_30_days": posts_30d,
                "angles_never_used": never_used,
                "categories": categories,
                "photo_sets": photo_sets,
            }
        except Exception as e:
            log.error("Failed to get stats: %s", e)
            return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════
# MODULE-LEVEL CONVENIENCE
# ══════════════════════════════════════════════════════════════════════════

_engine: Optional[PostingEngine] = None


def get_posting_engine(db_path: Optional[str | Path] = None) -> PostingEngine:
    """Get or create the singleton PostingEngine instance."""
    global _engine
    if _engine is None:
        _engine = PostingEngine(db_path=db_path)
    return _engine
