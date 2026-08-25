"""City-name -> IATA mapping for the DGCA domestic city-pair file.

The DGCA publishes *city* names, not airport codes, and the strings are not clean:
the same city appears under more than one label (`MUMBAI` and `MUMBAI (MUMBAI)`),
casing and spacing vary between monthly releases, and a few cities have had more
than one commercial airport over the series.

This module is deliberately an explicit, hand-checked mapping rather than a fuzzy
matcher. A silent mis-match here would corrupt every route weight downstream, and
a route weight is not the kind of number you want to discover was wrong in December.
Anything not in this table is dropped from weighting and reported, never guessed.
"""

from __future__ import annotations

# Canonical alias -> IATA. Keys are upper-cased and whitespace-collapsed before lookup.
CITY_TO_IATA: dict[str, str] = {
    "DELHI": "DEL",
    "MUMBAI": "BOM",
    "MUMBAI (MUMBAI)": "BOM",  # duplicate label in the DGCA source, same city
    "BENGALURU": "BLR",
    "BANGALORE": "BLR",
    "HYDERABAD": "HYD",
    "KOLKATA": "CCU",
    "CHENNAI": "MAA",
    "AHMEDABAD": "AMD",
    "PUNE": "PNQ",
    "GOA": "GOI",
    "GOA (MOPA)": "GOX",  # Manohar Intl, separate airport, kept distinct on purpose
    "MOPA": "GOX",
    "GUWAHATI": "GAU",
    "SRINAGAR": "SXR",
    "PATNA": "PAT",
    "LUCKNOW": "LKO",
    "KOCHI": "COK",
    "COCHIN": "COK",
    "JAIPUR": "JAI",
    "VARANASI": "VNS",
    "BHUBANESWAR": "BBI",
    "INDORE": "IDR",
    "NAGPUR": "NAG",
    "CHANDIGARH": "IXC",
    "COIMBATORE": "CJB",
    "THIRUVANANTHAPURAM": "TRV",
    "TRIVANDRUM": "TRV",
    "VISAKHAPATNAM": "VTZ",
    "AMRITSAR": "ATQ",
    "RAIPUR": "RPR",
    "RANCHI": "IXR",
    "DEHRADUN": "DED",
    "JAMMU": "IXJ",
    "LEH": "IXL",
    "IMPHAL": "IMF",
    "AGARTALA": "IXA",
    "DIBRUGARH": "DIB",
    "SILCHAR": "IXS",
    "PORT BLAIR": "IXZ",
    "MADURAI": "IXM",
    "MANGALURU": "IXE",
    "MANGALORE": "IXE",
    "TIRUPATI": "TIR",
    "VIJAYAWADA": "VGA",
    "SURAT": "STV",
    "RAJKOT": "RAJ",
    "VADODARA": "BDQ",
    "AURANGABAD": "IXU",
    "BHOPAL": "BHO",
    "JODHPUR": "JDH",
    "UDAIPUR": "UDR",
    "JABALPUR": "JLR",
    "GORAKHPUR": "GOP",
    "PRAYAGRAJ": "IXD",
    "ALLAHABAD": "IXD",
    "BAGDOGRA": "IXB",
    "DHARAMSHALA": "DHM",
    "SHIMLA": "SLV",
    "KANNUR": "CNN",
    "KOZHIKODE": "CCJ",
    "CALICUT": "CCJ",
    "HUBLI": "HBX",
    "BELAGAVI": "IXG",
    "TIRUCHIRAPPALLI": "TRZ",
    "SALEM": "SXV",
    "PUDUCHERRY": "PNY",
    "RAJAHMUNDRY": "RJA",
    "KADAPA": "CDP",
    "JORHAT": "JRH",
    "TEZPUR": "TEZ",
    "LILABARI": "IXI",
    "AIZAWL": "AJL",
    "SHILLONG": "SHL",
    "DIMAPUR": "DMU",
    "ITANAGAR": "HGI",
    "GAYA": "GAY",
    "DARBHANGA": "DBR",
    "DEOGHAR": "DGH",
    "DURGAPUR": "RDP",
    "JHARSUGUDA": "JRG",
    "KOLHAPUR": "KLH",
    "NASHIK": "ISK",
    "SINDHUDURG": "SDW",
    "SOLAPUR": "SSE",
    "GWALIOR": "GWL",
    "KANPUR": "KNU",
    "BAREILLY": "BEK",
    "HINDON": "HDO",
    "AGRA": "AGR",
    "BIKANER": "BKB",
    "JAISALMER": "JSA",
    "KISHANGARH": "KQH",
    "PORBANDAR": "PBD",
    "BHUJ": "BHJ",
    "JAMNAGAR": "JGA",
    "DIU": "DIU",
    "KANDLA": "IXY",
    "PANTNAGAR": "PGH",
    "PITHORAGARH": "NNP",
    "PASIGHAT": "IXT",
    "TEZU": "TEI",
    "ZIRO": "ZER",
    "RUPSI": "RUP",
    "SHIVAMOGGA": "RQY",
    "BIDAR": "IXX",
    "KALABURAGI": "GBI",
    "AGATTI": "AGX",
    "KURNOOL": "KJB",
    "ADAMPUR": "AIP",
    "LUDHIANA": "LUH",
    "PAKYONG": "PYG",
    "AYODHYA": "AYJ",
    "RAJKOT (HIRASAR)": "HSR",
    # --- Airports the DGCA labels inconsistently or that opened mid-series. ---
    # Goa is published under three different labels across the window and Mumbai now has
    # two commercial airports. We keep distinct airports as distinct IATA codes rather than
    # collapsing them onto a city: the matched model compares an identical product, and
    # BOM and NMI are not the same product to a traveller. Only genuine duplicate *labels*
    # for the same airport are merged.
    "DABOLIM": "GOI",
    "GOA (DABOLIM, SOUTH GOA)": "GOI",
    "GOA (MOPA, NORTH GOA)": "GOX",
    "MUMBAI (NAVI MUMBAI)": "NMI",
    "NAVI MUMBAI": "NMI",
    "VISHAKHAPATNAM (VISAKHAPATNAM)": "VTZ",
    "VISHAKHAPATNAM": "VTZ",
    "MANGALORE (MANGALURU)": "IXE",
    "HINDON AIRPORT": "HDO",
    "HIRASAR (RAJKOT)": "HSR",
    "RAJKOT INTERNATIONAL AIRPORT": "HSR",
    "SHIRDI": "SAG",
    "VIJAYWADA": "VGA",
    "TIRUCHIRAPALLY": "TRZ",
    "ALLAHABAD (PRAYAGRAJ)": "IXD",
    "AGATTI ISLAND": "AGX",
    "AYODHYA INTERNATIONAL AIRPORT": "AYJ",
    "BELGAUM": "IXG",
    "BATHINDA": "BUP",
    "BHATINDA": "BUP",
    "BHAVNAGAR": "BHU",
    "BILASPUR": "PAB",
    "AMBIKAPUR": "AMB",
    "AMBIKAPUR AIRPORT": "AMB",
    "AMRAVATI": "AMV",
    "AMRAVATI AIRPORT": "AMV",
}


def normalise(name: str) -> str:
    """Upper-case and collapse whitespace so label drift between releases doesn't matter."""
    return " ".join(str(name).upper().split())


def to_iata(name: str) -> str | None:
    """Return the IATA code for a DGCA city label, or None if we have no verified mapping."""
    return CITY_TO_IATA.get(normalise(name))
