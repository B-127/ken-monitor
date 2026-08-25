#!/usr/bin/env python3
"""Generate data/entities.csv, the curated resolver for the 74 holdings.

    python -m tools.build_entities                        # write the CSV
    python -m tools.build_entities --check                # verify, write nothing
    python -m tools.build_entities --workbook Ken.xlsx    # cross-check coverage

The table below is hand-authored, not derived: it encodes what a company is
actually called, what it used to be called, which words corroborate a match
and which words disprove one. That knowledge cannot be scraped, so it lives
here in readable form and is regenerated into CSV rather than edited as CSV.

Field notes
-----------
ambiguous  'yes' when the name alone could plausibly refer to something else
           (Peabody the awards, Stanmore the tube station, Blackstone the
           private-equity firm). Ambiguous entities need a context term in the
           headline to score 'high'; without one they are kept but flagged.
negative   Words that positively disprove a match — a collision we know about.
verified   Date the corporate status was confirmed against a primary or
           reputable secondary source. Blank means carried from the July 2022
           workbook and NOT yet checked. See README, "Verifying the resolver".

Aliases must be written accent-folded ("Tofas", not "Tofas" with a cedilla);
headlines are folded before matching, so the folded form catches both.

When to include the bare company name
-------------------------------------
Two cases that look alike and are not:

  Include it, and set ambiguous='yes'. The bare form refers to the SAME
  company, so leaving it out silently loses recall — most headlines say
  "Peabody swings to profit", not "Peabody Energy swings to profit".
  Collisions are then handled by context and negative terms.
  Applies to: Peabody, Stanmore, Cavendish, Xanadu, Karoon.

  Exclude it. The bare form refers to a DIFFERENT legal entity that is not in
  the portfolio, so matching it would be a false positive rather than a
  missed one. "Polyplex" is the Indian parent, not Polyplex Thailand;
  "Richard Pieris" is the separately listed parent, not Richard Pieris
  Exports; "Goodyear" is the US parent, not Goodyear Indonesia; "MACA" is a
  common acronym; "Indeks" is the ordinary Turkish word for index.

Getting this backwards is the single easiest way to degrade the tool, in
either direction. Test 'a full offline run...' in tests/test_all.py pins the
Peabody case specifically.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import schema

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "data", "entities.csv")

# Shared context vocabularies, so a market's corroborating words are written once.
_SL = "Sri Lanka|Colombo|CSE|Sri Lankan"
_ID = "Indonesia|Jakarta|Indonesian|IDX|Tbk"
_VN = "Vietnam|Hanoi|Ho Chi Minh|Vietnamese|HOSE"
_TH = "Thailand|Bangkok|Thai|SET"
_TR = "Turkey|Turkiye|Istanbul|Turkish|Borsa"
_HK = "Hong Kong|HKEX|Hang Seng"
_AU = "Australia|ASX|Australian"

# ticker, name, aliases, context, negative, country, industry, ambiguous, status, note, verified
ROWS: list[tuple] = [
    # ---- Australia -------------------------------------------------------
    ("MLD AU Equity", "Thiess (formerly MACA)",
     "Thiess|MACA Limited|MACA Ltd", "mining|contractor|coal|" + _AU, "",
     "Australia", "Mining Contractor", "no", "acquired",
     "MACA acquired by Thiess, control Oct 2022; Thiess is privately held so news flow is thinner than a listed name.",
     "2026-08-24"),
    ("YAL AU Equity", "Yancoal Australia",
     "Yancoal|Yancoal Australia", "coal|mining|" + _AU, "",
     "Australia", "Coal Mining", "no", "active", "", ""),
    ("MYE AU Equity", "Mastermyne Group",
     "Mastermyne|Metarock|Metarock Group", "coal|mining|" + _AU, "",
     "Australia", "Mining Contractor", "no", "active",
     "Renamed Metarock in 2022, reverted to Mastermyne Nov 2024; both names kept as aliases.",
     "2026-08-24"),
    ("WAF AU Equity", "West African Resources",
     "West African Resources|Sanbrado|Kiaka", "gold|mining|Burkina Faso|" + _AU, "",
     "Australia", "Gold Mining", "no", "active", "", ""),
    ("KAR AU Equity", "Karoon Energy",
     "Karoon Energy|Karoon Gas|~Karoon", "oil|gas|energy|Brazil|Bauna|" + _AU, "",
     "Australia", "Oil and Gas", "yes", "active",
     "'Karoon' also names a river and province in Iran; context required.", ""),
    ("XAM AU Equity", "Xanadu Mines",
     "Xanadu Mines|~Xanadu|Kharmagtai", "copper|gold|Mongolia|mining|" + _AU, "",
     "Australia", "Copper and Gold Mining", "yes", "active",
     "Appeared twice in the July 2022 sheet, tagged gold in one row and copper in the other; merged here.",
     ""),
    ("BSX AU Equity", "Blackstone Minerals",
     "Blackstone Minerals", "nickel|Ta Khoa|Vietnam|mining|" + _AU,
     "Blackstone Inc|Blackstone Group|Schwarzman|Blackstone Real Estate",
     "Australia", "Nickel Mining", "no", "active",
     "Collides with Blackstone Inc, the private-equity firm.", ""),
    ("SIH AU Equity", "Sihayo Gold",
     "Sihayo Gold|Sihayo Pungkut|Sihayo", "gold|mining|Indonesia|Sumatra|" + _AU, "",
     "Australia", "Gold Mining", "no", "active", "", ""),
    ("SXE AU Equity", "Southern Cross Electrical Engineering",
     "Southern Cross Electrical|Southern Cross Electrical Engineering|SCEE Group",
     "electrical|contractor|infrastructure|" + _AU,
     "Southern Cross Austereo|Southern Cross University|Southern Cross Station|Southern Cross Media",
     "Australia", "Mining Contractor", "no", "active",
     "'Southern Cross' collides with a broadcaster, a university and a Melbourne station.", ""),
    ("SMR AU Equity", "Stanmore Resources",
     "Stanmore Resources|Stanmore Coal|~Stanmore", "coal|mining|Queensland|Bowen|" + _AU,
     "Stanmore station|Stanmore tube|Jubilee line|Stanmore Middlesex",
     "Australia", "Coal Mining", "yes", "active",
     "Row had no symbol in the July 2022 sheet; ASX code SMR assigned here. 'Stanmore' is also a London suburb and Tube station.",
     ""),

    # ---- United States ---------------------------------------------------
    ("HCC US Equity", "Warrior Met Coal",
     "Warrior Met Coal|Warrior Met", "coal|metallurgical|Alabama|mining", "",
     "USA", "Coal Mining", "no", "active", "", ""),
    ("BTU US Equity", "Peabody Energy",
     "Peabody Energy|Peabody Coal|~Peabody", "coal|mining|thermal|metallurgical|BTU",
     "Peabody Essex|Peabody Award|Peabody Institute|Peabody Hotel|George Peabody|Peabody Massachusetts",
     "USA", "Coal Mining", "yes", "active",
     "'Peabody' collides with the Peabody Awards, the Peabody Essex Museum and Peabody, Massachusetts.",
     ""),
    ("LPI US Equity", "Crescent Energy (formerly Laredo Petroleum / Vital Energy)",
     "Crescent Energy|~Vital Energy|Laredo Petroleum",
     "Permian|Midland Basin|Texas|shale|oil|CRGY|VTLE",
     "TSXV|Vital Energy Inc. (TSXV",
     "USA", "Oil and Gas", "yes", "acquired",
     "Laredo renamed Vital Energy Jan 2023, then acquired by Crescent Energy. A separate Canadian 'Vital Energy Inc' trades on the TSXV, hence the context requirement.",
     "2026-08-24"),

    # ---- Hong Kong / China -----------------------------------------------
    ("2678 HK Equity", "Texhong International Group",
     "Texhong International|Texhong Textile|Texhong", "textile|yarn|fabric|" + _HK, "",
     "China", "Textiles", "no", "renamed",
     "Texhong Textile renamed Texhong International Group, Feb 2023; stock code 2678 unchanged.",
     "2026-08-24"),
    ("1382 HK Equity", "Pacific Textiles Holdings",
     "Pacific Textiles|~Pacific Textile", "knitted|fabric|textile|Vietnam|" + _HK, "",
     "Hong Kong", "Textiles", "yes", "active",
     "Generic word pair; context required.", ""),
    ("2111 HK Equity", "Best Pacific International",
     "Best Pacific International|~Best Pacific", "elastic|lace|fabric|textile|" + _HK, "",
     "Hong Kong", "Textiles", "yes", "active",
     "Generic word pair; context required.", ""),
    ("1982 HK Equity", "Nameson Holdings",
     "Nameson Holdings|Nameson", "knitwear|garment|textile|" + _HK, "",
     "Hong Kong", "Textiles", "no", "active", "", ""),
    ("420 HK Equity", "Fountain Set Holdings",
     "Fountain Set", "knitted|fabric|textile|dyeing|" + _HK, "",
     "China", "Textiles", "no", "active", "", ""),
    ("1122 HK Equity", "Qingling Motors",
     "Qingling Motors|Qingling", "truck|vehicle|Isuzu|automotive|" + _HK, "",
     "China", "Automotive", "no", "active", "", ""),
    ("743 HK Equity", "Asia Cement (China) Holdings",
     "Asia Cement (China)|Asia Cement China|~Asia Cement",
     "China|Sichuan|Hubei|clinker|" + _HK,
     "Asia Cement Corporation|Taiwan Cement",
     "China", "Cement and Materials", "yes", "active",
     "Distinct from Asia Cement Corporation of Taiwan.", ""),
    ("3315 HK Equity", "Goldpac Group",
     "Goldpac", "payment|card|smart card|fintech|" + _HK, "",
     "China", "Credit Card Manufacturer", "no", "active", "", ""),
    ("2343 HK Equity", "Pacific Basin Shipping",
     "Pacific Basin Shipping|~Pacific Basin",
     "shipping|dry bulk|handysize|supramax|freight|" + _HK, "",
     "Hong Kong", "Dry Bulk Shipping", "yes", "active",
     "'Pacific Basin' is also a plain geographic phrase; context required.", ""),
    ("320 HK Equity", "Computime Group",
     "Computime", "electronics|controls|manufacturing|" + _HK, "",
     "Hong Kong", "Electronic Manufacturing Services", "no", "active",
     "Listed twice in the July 2022 sheet; deduplicated here.", ""),
    ("6822 HK Equity", "King's Flair International",
     "King's Flair|Kings Flair", "houseware|kitchen|consumer|" + _HK, "",
     "Hong Kong", "Electronic Manufacturing Services", "no", "active", "", ""),
    ("1418 HK Equity", "Sinomax Group",
     "Sinomax", "memory foam|mattress|pillow|bedding|" + _HK, "",
     "Hong Kong", "Memory Foam Manufacturer", "no", "active", "", ""),
    ("184 HK Equity", "Keck Seng Investments",
     "Keck Seng Investments|~Keck Seng", "hotel|property|" + _HK,
     "Keck Seng (Malaysia)|Bursa Malaysia",
     "Hong Kong", "Real Estate", "yes", "active",
     "Distinct from Keck Seng (Malaysia) Berhad.", ""),
    ("VALUE SP Equity", "Valuetronics Holdings",
     "Valuetronics", "electronics|manufacturing|Singapore|EMS", "",
     "Hong Kong", "Electronic Manufacturing Services", "no", "active", "", ""),

    # ---- Indonesia -------------------------------------------------------
    ("HEXA IJ Equity", "Hexindo Adiperkasa",
     "Hexindo Adiperkasa|Hexindo", "excavator|Hitachi|heavy equipment|" + _ID, "",
     "Indonesia", "Machinery Distributor", "no", "active", "", ""),
    ("ITMG IJ Equity", "Indo Tambangraya Megah",
     "Indo Tambangraya|Indo Tambangraya Megah", "coal|mining|" + _ID, "",
     "Indonesia", "Coal Mining", "no", "active", "", ""),
    ("AMFG IJ Equity", "Asahimas Flat Glass",
     "Asahimas Flat Glass|Asahimas", "glass|manufacturing|" + _ID, "",
     "Indonesia", "Glass Manufacturer", "no", "active", "", ""),
    ("TOTL IJ Equity", "Total Bangun Persada",
     "Total Bangun Persada|Total Bangun", "construction|contractor|" + _ID, "",
     "Indonesia", "Contractor", "no", "active", "", ""),
    ("NRCA IJ Equity", "Nusa Raya Cipta",
     "Nusa Raya Cipta", "construction|contractor|" + _ID, "",
     "Indonesia", "Contractor", "no", "active", "", ""),
    ("SSIA IJ Equity", "Surya Semesta Internusa",
     "Surya Semesta Internusa|Surya Semesta", "industrial estate|property|" + _ID, "",
     "Indonesia", "Real Estate", "no", "active", "", ""),
    ("LPCK IJ Equity", "Lippo Cikarang",
     "Lippo Cikarang", "property|township|" + _ID, "",
     "Indonesia", "Real Estate", "no", "active", "", ""),
    ("ADMF IJ Equity", "Adira Dinamika Multi Finance",
     "Adira Dinamika|Adira Finance", "financing|multifinance|" + _ID, "",
     "Indonesia", "Financial", "no", "active", "", ""),
    ("CFIN IJ Equity", "Clipan Finance Indonesia",
     "Clipan Finance|Clipan", "financing|multifinance|" + _ID, "",
     "Indonesia", "Financial", "no", "active", "", ""),
    ("AMAG IJ Equity", "Asuransi Multi Artha Guna",
     "Asuransi Multi Artha Guna|Multi Artha Guna", "insurance|asuransi|" + _ID, "",
     "Indonesia", "Financial", "no", "active", "", ""),
    ("LPPF IJ Equity", "Matahari Department Store",
     "Matahari Department Store|Matahari Dept", "retail|department store|" + _ID,
     "Matahari Putra Prima|MPPA",
     "Indonesia", "Retail", "no", "active",
     "Distinct from Matahari Putra Prima (MPPA), a separate listing.", ""),
    ("EPMT IJ Equity", "Enseval Putera Megatrading",
     "Enseval Putera Megatrading|Enseval", "distribution|pharmaceutical|" + _ID, "",
     "Indonesia", "Pharmaceuticals", "no", "active", "", ""),
    ("GDYR IJ Equity", "Goodyear Indonesia",
     "Goodyear Indonesia", "tire|ban|Bogor|" + _ID, "",
     "Indonesia", "Tire Manufacturer", "no", "active",
     "Bare 'Goodyear' is excluded as an alias; it would match the US parent constantly.", ""),
    ("RIGS IJ Equity", "Rig Tenders Indonesia",
     "Rig Tenders Indonesia|~Rig Tenders", "barge|tug|coal|shipping|" + _ID, "",
     "Indonesia", "Oil and Gas Pipeline and Vessels", "yes", "active",
     "'Rig tenders' is also a generic industry phrase.", ""),

    # ---- Vietnam ---------------------------------------------------------
    ("GAS VN Equity", "PetroVietnam Gas",
     "PetroVietnam Gas|PV Gas|PetroVietnam Gas Joint Stock", "gas|LNG|pipeline|" + _VN, "",
     "Vietnam", "Oil and Gas Pipeline and Vessels", "no", "active", "", ""),
    ("VIP VN Equity", "Vietnam Petroleum Transport (VIPCO)",
     "Vietnam Petroleum Transport|VIPCO", "tanker|shipping|petroleum|" + _VN, "",
     "Vietnam", "Oil and Gas Pipeline and Vessels", "no", "active", "", ""),
    ("QNS VN Equity", "Quang Ngai Sugar",
     "Quang Ngai Sugar|Vinasoy", "sugar|soy milk|beverage|" + _VN, "",
     "Vietnam", "Food and Beverage", "no", "active", "", ""),
    ("SMB VN Equity", "Sai Gon - Mien Trung Beer",
     "Sai Gon Mien Trung Beer|Saigon Mien Trung Beer|Bia Sai Gon Mien Trung",
     "beer|brewery|beverage|" + _VN, "",
     "Vietnam", "Food and Beverage", "no", "active", "", ""),
    ("MSH VN Equity", "Song Hong Garment",
     "Song Hong Garment|May Song Hong", "garment|textile|apparel|" + _VN, "",
     "Vietnam", "Textiles", "no", "active",
     "'Song Hong' is also the Vietnamese name of the Red River.", ""),
    ("DSN VN Equity", "Dam Sen Water Park",
     "Dam Sen Water Park|Dam Sen Waterpark|Cong vien nuoc Dam Sen",
     "water park|tourism|entertainment|" + _VN, "",
     "Vietnam", "Entertainment", "no", "active", "", ""),
    ("HLD VN Equity", "Hudland",
     "Hudland|HUD Land", "property|real estate|" + _VN, "",
     "Vietnam", "Real Estate", "no", "active", "", ""),
    ("KHP VN Equity", "Khanh Hoa Power",
     "Khanh Hoa Power|Khanh Hoa Electricity", "power|electricity|utility|" + _VN, "",
     "Vietnam", "Power", "no", "active",
     "Spelled 'Khahn Hoa Power' in the July 2022 sheet; corrected here.", ""),

    # ---- Thailand --------------------------------------------------------
    ("SAT TB Equity", "Somboon Advance Technology",
     "Somboon Advance Technology|Somboon Advance", "auto parts|automotive|" + _TH, "",
     "Thailand", "Automotive", "no", "active", "", ""),
    ("STANLY TB Equity", "Thai Stanley Electric",
     "Thai Stanley Electric|Thai Stanley", "lighting|auto parts|automotive|" + _TH, "",
     "Thailand", "Automotive", "no", "active", "", ""),
    ("LALIN TB Equity", "Lalin Property",
     "Lalin Property", "housing|property|real estate|" + _TH, "",
     "Thailand", "Real Estate", "no", "active", "", ""),
    ("LPN TB Equity", "LPN Development",
     "LPN Development|Lumpini Place", "condominium|property|real estate|" + _TH, "",
     "Thailand", "Real Estate", "no", "active", "", ""),
    ("TOPP TB Equity", "Thai O.P.P.",
     "Thai O.P.P.|Thai OPP", "packaging|film|plastic|" + _TH, "",
     "Thailand", "Plastic Film", "no", "active", "", ""),
    ("PTL TB Equity", "Polyplex Thailand",
     "Polyplex Thailand", "PET film|packaging|plastic|" + _TH, "",
     "Thailand", "Plastic Film", "no", "active",
     "Bare 'Polyplex' excluded; it would match the Indian parent.", ""),
    ("MCS TB Equity", "MCS Steel",
     "MCS Steel", "steel|structural|fabrication|" + _TH, "",
     "Thailand", "Steel", "no", "active",
     "Present in the holdings sheet only, not the July 2022 universe sheet.", ""),

    # ---- Turkey ----------------------------------------------------------
    ("TOASO TI Equity", "Tofas",
     "Tofas|Tofas Turk Otomobil", "automotive|Fiat|Stellantis|Bursa|" + _TR, "",
     "Turkey", "Automotive", "no", "active", "", ""),
    ("EREGL TI Equity", "Eregli Demir ve Celik (Erdemir)",
     "Erdemir|Eregli Demir|Eregli Demir ve Celik", "steel|iron|" + _TR, "",
     "Turkey", "Steel", "no", "active", "", ""),
    ("TTRAK TI Equity", "Turk Traktor",
     "Turk Traktor|TurkTraktor|Turk Traktor ve Ziraat",
     "tractor|agriculture|CNH|" + _TR, "",
     "Turkey", "Tractor Sales", "no", "active", "", ""),
    ("CIMSA TI Equity", "Cimsa Cimento",
     "Cimsa Cimento|Cimsa", "cement|clinker|Sabanci|" + _TR, "",
     "Turkey", "Cement and Materials", "no", "active", "", ""),
    ("INDES TI Equity", "Indeks Bilgisayar",
     "Indeks Bilgisayar|Indeks Teknoloji", "technology|distributor|IT|" + _TR, "",
     "Turkey", "Electronics Distributor", "no", "active",
     "'Indeks' is the ordinary Turkish word for index; only compound aliases are used.", ""),
    ("DESPC TI Equity", "Despec Bilgisayar",
     "Despec Bilgisayar|~Despec", "technology|distributor|consumable|IT|" + _TR, "",
     "Turkey", "Electronics Distributor", "yes", "active", "", ""),

    # ---- Russia (retained though uninvestable) ----------------------------
    ("GAZP RM Equity", "Gazprom",
     "Gazprom", "gas|pipeline|Russia|energy", "Gazprombank",
     "Russia", "Oil and Gas", "no", "sanctioned",
     "Retained on instruction although effectively uninvestable post-sanctions. The July 2022 sheet also carried the US ADR line (OGZPY); merged here.",
     "2026-08-24"),
    ("LKOH RM Equity", "Lukoil",
     "Lukoil", "oil|refinery|Russia|energy", "",
     "Russia", "Oil and Gas", "no", "sanctioned",
     "Retained on instruction although effectively uninvestable post-sanctions.", "2026-08-24"),
    ("BANEP RM Equity", "Bashneft",
     "Bashneft", "oil|refinery|Russia|Bashkortostan", "",
     "Russia", "Oil and Gas", "no", "sanctioned",
     "Retained on instruction although effectively uninvestable post-sanctions.", "2026-08-24"),
    ("ATAD LI Equity", "Tatneft",
     "Tatneft", "oil|refinery|Russia|Tatarstan", "",
     "Russia", "Oil and Gas", "no", "sanctioned",
     "Recorded as 'Taftnet' in the July 2022 sheet; corrected to Tatneft (ATAD was the London ADR line).",
     "2026-08-24"),

    # ---- Sri Lanka -------------------------------------------------------
    ("LLUB SL Equity", "Chevron Lubricants Lanka",
     "Chevron Lubricants Lanka|Caltex Lubricants Lanka|Chevron Lanka",
     "lubricant|oil|" + _SL, "",
     "Sri Lanka", "Petroleum Retailer", "no", "renamed",
     "Carried as 'Caltex Lubricants' in the July 2022 sheet; both names kept as aliases.", ""),
    ("TKYO SL Equity", "Tokyo Cement",
     "Tokyo Cement", "cement|clinker|" + _SL, "",
     "Sri Lanka", "Cement and Materials", "no", "active", "", ""),
    ("TILE SL Equity", "Lanka Tiles",
     "Lanka Tiles|Lanka Tile", "tile|ceramic|" + _SL, "",
     "Sri Lanka", "Cement and Materials", "no", "active", "", ""),
    ("REXP SL Equity", "Richard Pieris Exports",
     "Richard Pieris Exports", "rubber|export|" + _SL, "",
     "Sri Lanka", "Rubber", "no", "active",
     "Bare 'Richard Pieris' excluded; it would match the separately listed parent.", ""),
    ("LIOC SL Equity", "Lanka IOC",
     "Lanka IOC|Lanka Indian Oil", "fuel|petroleum|retail|" + _SL, "",
     "Sri Lanka", "Petroleum Retailer", "no", "active", "", ""),

    # ---- Philippines / Canada / UK ---------------------------------------
    ("SHLPH PM Equity", "Pilipinas Shell",
     "Pilipinas Shell", "fuel|refinery|retail|Philippines|Manila", "",
     "Philippines", "Petroleum Retailer", "no", "active", "", ""),
    ("FP CN Equity", "FP Newspapers",
     "FP Newspapers|FP Canadian Newspapers|Winnipeg Free Press",
     "Winnipeg|Manitoba|newspaper|publishing|Canada", "",
     "Canada", "Media", "no", "active",
     "'FP' alone is far too generic; only compound aliases are used. Corporate status not yet verified.", ""),
    ("CNKS LN Equity", "Cavendish Financial (formerly Cenkos)",
     "Cavendish Financial|Cavendish plc|~Cavendish|Cenkos Securities|Cenkos|finnCap",
     "AIM|broker|investment bank|London|nomad|corporate finance", "",
     "UK", "Financial", "yes", "acquired",
     "Cenkos merged with finnCap Sept 2023; combined group renamed Cavendish Financial plc (AIM: CAV). 'Cavendish' collides with the laboratory, the banana and Cavendish Square.",
     "2026-08-24"),
]


def build_rows() -> list[dict]:
    rows = []
    for (ticker, name, aliases, context, negative, country, industry,
         ambiguous, status, note, verified) in ROWS:
        rows.append({
            "ticker": ticker, "name": name, "aliases": aliases,
            "context_terms": context, "negative_terms": negative,
            "country": country, "industry": industry, "ambiguous": ambiguous,
            "status": status, "note": note, "verified": verified,
        })
    return rows


def cross_check_workbook(path: str, rows: list[dict]) -> None:
    """Report holdings in the workbook that have no row here, and vice versa."""
    try:
        import pandas as pd
    except ImportError:
        print("  (pandas not installed — skipping workbook cross-check)")
        return
    sheets = pd.read_excel(path, sheet_name=None)
    names = set()
    for df in sheets.values():
        if "Company Name" in df.columns:
            names |= {str(v).strip() for v in df["Company Name"].dropna()}
    covered = " | ".join(r["aliases"] + " " + r["name"] for r in rows).lower()
    missing = [n for n in sorted(names)
               if n and not any(w.lower() in covered for w in n.split() if len(w) > 3)]
    print(f"  workbook names: {len(names)}; resolver rows: {len(rows)}")
    if missing:
        print(f"  not obviously covered ({len(missing)}): {missing}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    ap.add_argument("--workbook", help="portfolio .xlsx to cross-check coverage against")
    args = ap.parse_args()

    rows = build_rows()

    tickers = [r["ticker"] for r in rows]
    if len(tickers) != len(set(tickers)):
        dupes = {t for t in tickers if tickers.count(t) > 1}
        print(f"duplicate tickers in the table: {sorted(dupes)}", file=sys.stderr)
        return 2

    if args.workbook:
        cross_check_workbook(args.workbook, rows)

    if not args.check:
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=schema.ENTITY_COLS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"wrote {len(rows)} entities to {os.path.relpath(OUT, REPO)}")

    # Validate what we just produced through the real loader.
    from tools import entities as ent
    loaded = ent.load(OUT)
    unverified = [e.ticker for e in loaded if not e.verified]
    ambiguous = [e.ticker for e in loaded if e.ambiguous]
    print(f"validated {len(loaded)} entities")
    print(f"  ambiguous (need context to score high): {len(ambiguous)}")
    print(f"  corporate status not yet verified      : {len(unverified)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
