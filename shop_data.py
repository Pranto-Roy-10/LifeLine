"""
Bangladesh-wide shop / service-provider seed data for LifeLine.

Covers all 64 districts plus multiple neighbourhoods in Dhaka,
giving ~78 locations × 7 shop types = 546 shops.
"""


def generate_all_shops():
    """Return a list of dicts ready to be passed to Shop(**d)."""

    # ── Every location: (city, area, lat, lng, landmark) ──────────────
    LOCATIONS = [
        # ═══ Dhaka City – 15 key neighbourhoods ═══
        ("Dhaka", "Mohammadpur",  23.7660, 90.3590, "Mohammadpur Bus Stand"),
        ("Dhaka", "Badda",        23.7800, 90.4260, "Badda Link Road"),
        ("Dhaka", "Banani",       23.7940, 90.4030, "Banani 11"),
        ("Dhaka", "Dhanmondi",    23.7470, 90.3750, "Dhanmondi 27"),
        ("Dhaka", "Gulshan",      23.7820, 90.4150, "Gulshan 1 Circle"),
        ("Dhaka", "Mirpur",       23.8080, 90.3680, "Mirpur 10 Circle"),
        ("Dhaka", "Uttara",       23.8750, 90.3950, "Uttara Sector 7"),
        ("Dhaka", "Motijheel",    23.7330, 90.4170, "Motijheel Circle"),
        ("Dhaka", "Shahbag",      23.7380, 90.3960, "Shahbag Circle"),
        ("Dhaka", "Jatrabari",    23.7100, 90.4350, "Jatrabari Bus Stand"),
        ("Dhaka", "Khilgaon",     23.7450, 90.4330, "Khilgaon Flyover"),
        ("Dhaka", "Rampura",      23.7620, 90.4410, "Rampura TV Center"),
        ("Dhaka", "Bashundhara",  23.8130, 90.4280, "Bashundhara Gate"),
        ("Dhaka", "Malibagh",     23.7490, 90.4250, "Malibagh Chowdhury Para"),
        ("Dhaka", "Tejgaon",      23.7660, 90.3930, "Tejgaon I/A"),

        # ═══ Other Districts – Dhaka Division ═══
        ("Narayanganj",  "Narayanganj Sadar",  23.6238, 90.5000, "Narayanganj Court"),
        ("Gazipur",      "Gazipur Sadar",      23.9999, 90.4203, "Gazipur Chowrasta"),
        ("Tangail",      "Tangail Sadar",      24.2513, 89.9167, "Tangail Bus Stand"),
        ("Faridpur",     "Faridpur Sadar",     23.6070, 89.8533, "Faridpur Court"),
        ("Manikganj",    "Manikganj Sadar",    23.8644, 90.0047, "Manikganj Bus Stand"),
        ("Munshiganj",   "Munshiganj Sadar",   23.5422, 90.5305, "Munshiganj Court"),
        ("Narsingdi",    "Narsingdi Sadar",    23.9322, 90.7151, "Narsingdi Bus Stand"),
        ("Madaripur",    "Madaripur Sadar",    23.1641, 90.1896, "Madaripur Court"),
        ("Gopalganj",    "Gopalganj Sadar",    23.0050, 89.8266, "Gopalganj Bus Stand"),
        ("Kishoreganj",  "Kishoreganj Sadar",  24.4449, 90.7766, "Kishoreganj Court"),
        ("Shariatpur",   "Shariatpur Sadar",   23.2423, 90.4348, "Shariatpur Court"),
        ("Rajbari",      "Rajbari Sadar",      23.7574, 89.6445, "Rajbari Bus Stand"),

        # ═══ Chittagong Division ═══
        ("Chittagong",     "Agrabad",              22.3250, 91.8100, "Agrabad Commercial Area"),
        ("Chittagong",     "GEC Circle",           22.3569, 91.7832, "GEC More"),
        ("Chittagong",     "Nasirabad",            22.3700, 91.8000, "Nasirabad Housing"),
        ("Chittagong",     "Halishahar",           22.3400, 91.7700, "Halishahar Housing"),
        ("Cox's Bazar",    "Cox's Bazar Sadar",    21.4272, 92.0058, "Cox's Bazar Beach Road"),
        ("Comilla",        "Comilla Sadar",        23.4607, 91.1809, "Comilla Town Hall"),
        ("Noakhali",       "Noakhali Sadar",       22.8696, 91.0995, "Noakhali Court"),
        ("Feni",           "Feni Sadar",           23.0159, 91.3976, "Feni Trunk Road"),
        ("Rangamati",      "Rangamati Sadar",      22.6372, 92.1800, "Rangamati Lake"),
        ("Bandarban",      "Bandarban Sadar",      22.1953, 92.2184, "Bandarban Bazar"),
        ("Khagrachari",    "Khagrachari Sadar",    23.1193, 91.9847, "Khagrachari Court"),
        ("Brahmanbaria",   "Brahmanbaria Sadar",   23.9641, 91.1115, "Brahmanbaria Court"),
        ("Chandpur",       "Chandpur Sadar",       23.2333, 90.6712, "Chandpur Launch Ghat"),
        ("Lakshmipur",     "Lakshmipur Sadar",     22.9425, 90.8280, "Lakshmipur Court"),

        # ═══ Rajshahi Division ═══
        ("Rajshahi",          "Rajshahi Sadar",          24.3745, 88.6042, "Rajshahi Court"),
        ("Rajshahi",          "Shaheb Bazar",            24.3700, 88.5900, "Shaheb Bazar Zero Point"),
        ("Bogura",            "Bogura Sadar",            24.8465, 89.3773, "Bogura Satmatha"),
        ("Pabna",             "Pabna Sadar",             24.0064, 89.2372, "Pabna Court"),
        ("Natore",            "Natore Sadar",            24.4206, 89.0000, "Natore Rajbari"),
        ("Naogaon",           "Naogaon Sadar",           24.7936, 88.9318, "Naogaon Bus Stand"),
        ("Chapainawabganj",   "Chapainawabganj Sadar",   24.5965, 88.2775, "Chapainawabganj Court"),
        ("Sirajganj",         "Sirajganj Sadar",         24.4533, 89.7100, "Sirajganj Bus Stand"),
        ("Joypurhat",         "Joypurhat Sadar",         25.0968, 89.0227, "Joypurhat Bus Stand"),

        # ═══ Khulna Division ═══
        ("Khulna",     "Khulna Sadar",     22.8456, 89.5403, "Khulna Railway Station"),
        ("Khulna",     "Sonadanga",        22.8200, 89.5500, "Sonadanga Bus Stand"),
        ("Jashore",    "Jashore Sadar",    23.1667, 89.2167, "Jashore Court"),
        ("Satkhira",   "Satkhira Sadar",   22.7185, 89.0769, "Satkhira Court"),
        ("Bagerhat",   "Bagerhat Sadar",   22.6512, 89.7851, "Bagerhat Bus Stand"),
        ("Kushtia",    "Kushtia Sadar",    23.9013, 89.1200, "Kushtia Court"),
        ("Meherpur",   "Meherpur Sadar",   23.7622, 88.6318, "Meherpur Court"),
        ("Jhenaidah",  "Jhenaidah Sadar",  23.5448, 89.1726, "Jhenaidah Bus Stand"),
        ("Magura",     "Magura Sadar",     23.4871, 89.4190, "Magura Court"),
        ("Narail",     "Narail Sadar",     23.1725, 89.4951, "Narail Court"),
        ("Chuadanga",  "Chuadanga Sadar",  23.6402, 88.8420, "Chuadanga Court"),

        # ═══ Barisal Division ═══
        ("Barisal",     "Barisal Sadar",     22.7010, 90.3535, "Barisal Launch Ghat"),
        ("Patuakhali",  "Patuakhali Sadar",  22.3596, 90.3290, "Patuakhali Court"),
        ("Bhola",       "Bhola Sadar",       22.6859, 90.6482, "Bhola Launch Ghat"),
        ("Jhalokathi",  "Jhalokathi Sadar",  22.6406, 90.1987, "Jhalokathi Court"),
        ("Pirojpur",    "Pirojpur Sadar",    22.5841, 89.9720, "Pirojpur Bus Stand"),
        ("Barguna",     "Barguna Sadar",     22.1510, 90.1266, "Barguna Court"),

        # ═══ Sylhet Division ═══
        ("Sylhet",       "Sylhet Sadar",       24.8949, 91.8687, "Amberkhana Point"),
        ("Sylhet",       "Zindabazar",         24.8900, 91.8700, "Zindabazar Circle"),
        ("Habiganj",     "Habiganj Sadar",     24.3745, 91.4151, "Habiganj Court"),
        ("Moulvibazar",  "Moulvibazar Sadar",  24.4829, 91.7774, "Moulvibazar Court"),
        ("Sunamganj",    "Sunamganj Sadar",    25.0658, 91.3950, "Sunamganj Court"),

        # ═══ Rangpur Division ═══
        ("Rangpur",      "Rangpur Sadar",      25.7439, 89.2752, "Rangpur Town Hall"),
        ("Dinajpur",     "Dinajpur Sadar",     25.6217, 88.6354, "Dinajpur Rajbari"),
        ("Thakurgaon",   "Thakurgaon Sadar",   26.0336, 88.4616, "Thakurgaon Bus Stand"),
        ("Panchagarh",   "Panchagarh Sadar",   26.3411, 88.5542, "Panchagarh Court"),
        ("Nilphamari",   "Nilphamari Sadar",   25.9316, 88.8560, "Nilphamari Court"),
        ("Lalmonirhat",  "Lalmonirhat Sadar",  25.9923, 89.2847, "Lalmonirhat Court"),
        ("Kurigram",     "Kurigram Sadar",     25.8055, 89.6364, "Kurigram Court"),
        ("Gaibandha",    "Gaibandha Sadar",    25.3288, 89.5286, "Gaibandha Court"),

        # ═══ Mymensingh Division ═══
        ("Mymensingh",  "Mymensingh Sadar",  24.7471, 90.4203, "Mymensingh Town Hall"),
        ("Jamalpur",    "Jamalpur Sadar",    24.9375, 89.9376, "Jamalpur Bus Stand"),
        ("Sherpur",     "Sherpur Sadar",     25.0204, 90.0170, "Sherpur Court"),
        ("Netrokona",   "Netrokona Sadar",   24.8882, 90.7278, "Netrokona Court"),
    ]

    # ── Shop-type configs ─────────────────────────────────────────────
    # (shop_type, [name_suffixes], base_service_fee, base_labor_cost,
    #  rating_min, rating_max)
    TYPES = [
        ("tire_shop",  ["Tire Center", "Auto Repair", "Wheel Service"],
         150, 400, 3.6, 4.7),
        ("hospital",   ["General Hospital", "Medical Center", "Health Clinic"],
         600, 2000, 3.8, 4.8),
        ("pharmacy",   ["Pharmacy", "Medicine Corner", "Drug House"],
         70, 0, 3.7, 4.6),
        ("grocery",    ["Grocery", "Super Shop", "Bazar"],
         70, 0, 3.8, 4.5),
        ("stationary", ["Stationery", "Book & Supplies", "Copy Center"],
         45, 0, 3.7, 4.5),
        ("restaurant", ["Restaurant", "Food Corner", "Hotel & Restaurant"],
         100, 0, 3.8, 4.7),
        ("salon",      ["Salon", "Beauty Parlor", "Hair Studio"],
         150, 350, 3.8, 4.8),
    ]

    # ── Deterministic generation ──────────────────────────────────────
    shops = []
    phone_counter = 1700000001

    for li, (city, area, lat, lng, landmark) in enumerate(LOCATIONS):
        for ti, (stype, suffixes, fee, labor, rmin, rmax) in enumerate(TYPES):
            suffix = suffixes[li % len(suffixes)]

            # deterministic rating spread
            rating = round(
                rmin + ((li * 7 + ti * 3) % 11) / 10.0 * (rmax - rmin), 1
            )
            rating = min(rating, rmax)

            # tiny lat/lng jitter so 7 shops aren't stacked on one point
            shop_lat = round(lat + (ti - 3) * 0.002, 6)
            shop_lng = round(lng + (ti - 3) * 0.002, 6)

            shops.append({
                "name": f"{area} {suffix}",
                "shop_type": stype,
                "address": f"{area}, {city}",
                "phone": f"0{phone_counter}",
                "lat": shop_lat,
                "lng": shop_lng,
                "area": area,
                "landmark": landmark,
                "rating": rating,
                "service_fee": fee + (li % 5) * 10,
                "labor_cost": (labor + (li % 4) * 50) if labor > 0 else 0,
            })
            phone_counter += 1

    return shops


# Total expected count (for re-seed checks)
EXPECTED_SHOP_COUNT = len(generate_all_shops())


if __name__ == "__main__":
    data = generate_all_shops()
    print(f"Generated {len(data)} shops across Bangladesh")
    # Show breakdown by type
    from collections import Counter
    by_type = Counter(s["shop_type"] for s in data)
    for t, c in sorted(by_type.items()):
        print(f"  {t}: {c}")
    # Show breakdown by city
    by_city = Counter(s["address"].split(", ")[-1] for s in data)
    print(f"\nCities/districts covered: {len(by_city)}")
    for city, c in sorted(by_city.items(), key=lambda x: -x[1]):
        print(f"  {city}: {c} shops")
