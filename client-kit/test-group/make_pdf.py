"""Builds lakbay-bikes-rentals.pdf, a 2-page test file with a running header, a footer and page numbers
(the file reader must drop those). Needs reportlab: python make_pdf.py"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).with_name("lakbay-bikes-rentals.pdf")
PAGES = [
    ("Bike rentals", [
        "Rent a bike from either shop, by the day or by the week.",
        "",
        "City bike: P350 per day, or P1,800 per week.",
        "Mountain bike: P600 per day.",
        "E-bike: P900 per day, charger included.",
        "Kids' bikes (ages 6 to 12): P200 per day.",
        "",
        "What you need to rent",
        "One valid government ID and a P2,000 refundable deposit, paid in cash or GCash.",
        "The deposit is returned when the bike comes back in good condition.",
    ]),
    ("Late returns and damage", [
        "Late returns cost P150 per hour, up to the price of one rental day.",
        "You pay for damage beyond normal wear. Flat tires are fixed for free.",
        "",
        "Included with every rental",
        "A helmet, a lock and a basic repair kit, at no extra cost.",
        "",
        "Booking",
        "Walk in, or book on lakbaybikes.ph up to 14 days ahead. Booked bikes are held until 10:00 AM.",
    ]),
]

c = canvas.Canvas(str(OUT), pagesize=A4)
w, h = A4
c.setTitle("Lakbay Bikes Rental Guide")
for n, (title, lines) in enumerate(PAGES, 1):
    c.setFont("Helvetica", 9)
    c.drawString(50, h - 40, "Lakbay Bikes Rental Guide")
    c.drawRightString(w - 50, h - 40, "Updated October 2026")
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, h - 90, title)
    y = h - 125
    for line in lines:
        bold = line in {"What you need to rent", "Included with every rental", "Booking"}
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 12)
        c.drawString(50, y, line)
        y -= 20
    c.setFont("Helvetica", 9)
    c.drawString(50, 40, "lakbaybikes.ph")
    c.drawRightString(w - 50, 40, f"Page {n} of {len(PAGES)}")
    c.showPage()
c.save()
print("wrote", OUT)
