from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
import re
import unicodedata

app = FastAPI(title="Scout Analytics API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def normalize_name(name: str) -> str:
    """Normalize accents and special chars for search."""
    return unicodedata.normalize("NFKD", name).encode("ASCII", "ignore").decode("ASCII")


def parse_market_value(text: str) -> str:
    """Parse Transfermarkt market value string."""
    text = text.strip().replace("\xa0", " ")
    if not text or text == "-":
        return "N/D"
    return text


@app.get("/")
def root():
    return {"status": "ok", "service": "Scout Analytics API"}


def extract_search_terms(player_name: str) -> list:
    """
    Build a list of search query candidates, from most to least specific.
    Handles abbreviated first names like 'T. Palacios' which Transfermarkt's
    search handles poorly — falls back to surname-only search.
    """
    name = player_name.strip()
    queries = [name]  # try full name first

    # Strip single-letter abbreviations like "T." or "Ma." at the start
    parts = name.split()
    # Remove parts that are abbreviations (end with '.' or are <=2 chars)
    surname_parts = [p for p in parts if not (p.endswith(".") or len(p) <= 2)]
    if surname_parts and " ".join(surname_parts) != name:
        queries.append(" ".join(surname_parts))

    # Last resort: just the last word (usually the surname)
    if len(parts) > 1 and parts[-1] not in queries:
        queries.append(parts[-1])

    return queries


@app.get("/debug-search/{player_name}")
def debug_search(player_name: str):
    """Debug endpoint: shows raw search results to diagnose selector issues."""
    search_query = normalize_name(player_name)
    search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={requests.utils.quote(search_query)}"
    resp = requests.get(search_url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    all_links = []
    for a in soup.select("td.hauptlink a"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href and text:
            all_links.append({"text": text, "href": href})

    tables_found = len(soup.select("div.box table.items"))
    any_tables = len(soup.select("table"))

    return {
        "status_code": resp.status_code,
        "search_url": search_url,
        "tables_with_selector": tables_found,
        "any_tables_found": any_tables,
        "all_hauptlink_links": all_links[:30],
        "page_title": soup.title.get_text(strip=True) if soup.title else None,
    }


@app.get("/debug-candidates/{player_name}")
def debug_candidates(player_name: str, squad: str = "", pos: str = "", age: str = ""):
    """Debug endpoint: shows scored candidates without picking a winner."""
    search_terms = extract_search_terms(player_name)
    first_token = player_name.strip().split()[0] if player_name.strip() else ""
    initial = first_token[0].lower() if first_token and first_token[0].isalpha() else None
    target_age = None
    if age:
        try:
            target_age = int(age)
        except ValueError:
            pass

    results_by_term = {}

    for term in search_terms:
        search_query = normalize_name(term)
        search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={requests.utils.quote(search_query)}"
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")

        term_candidates = []
        for table in soup.select("div.box table.items"):
            for row in table.select("tbody tr"):
                name_cell = row.select_one("td.hauptlink a")
                if not name_cell:
                    continue
                href = name_cell.get("href", "")
                if "/profil/spieler/" not in href:
                    continue
                found_name = name_cell.get_text(strip=True)

                name_parts = [p for p in player_name.lower().replace(".", "").split() if len(p) > 1]
                found_lower = found_name.lower()
                if not any(p in found_lower for p in name_parts):
                    continue

                initial_match = False
                if initial:
                    ft = found_name.strip().split()[0] if found_name.strip() else ""
                    if ft and ft[0].lower() == initial:
                        initial_match = True

                club_title = ""
                club_img = row.select_one("img.tiny_wappen") or row.select_one("td.zentriert img")
                if club_img:
                    club_title = club_img.get("title", "") or club_img.get("alt", "")

                found_age = None
                for cell in row.select("td"):
                    txt = cell.get_text(strip=True)
                    if txt.isdigit() and 14 <= int(txt) <= 45:
                        found_age = int(txt)
                        break

                term_candidates.append({
                    "name": found_name,
                    "href": href,
                    "club": club_title,
                    "age": found_age,
                    "initial_match": initial_match,
                })

        results_by_term[term] = term_candidates
        if term_candidates:
            break

    return {
        "search_terms_tried": search_terms,
        "initial_extracted": initial,
        "target_age": target_age,
        "results_by_term": results_by_term,
    }


@app.get("/debug-profile-pos/{transfermarkt_slug}/{player_id}")
def debug_profile_position(transfermarkt_slug: str, player_id: str):
    """Debug: extract the actual position field from a player's TM profile page."""
    url = f"https://www.transfermarkt.com/{transfermarkt_slug}/profil/spieler/{player_id}"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    # Try the common TM position selectors
    position_candidates = []

    for sel in [
        "li.data-header__label",
        "span.info-table__content--bold",
        "div.detail-position__position",
        "span.data-header__content",
    ]:
        for el in soup.select(sel):
            txt = el.get_text(strip=True)
            if txt:
                position_candidates.append({"selector": sel, "text": txt})

    return {
        "url": url,
        "status_code": resp.status_code,
        "candidates": position_candidates[:25],
    }


@app.get("/player/{player_name}")
def get_player_data(player_name: str, squad: str = "", pos: str = "", age: str = ""):
    """
    Search for a player on Transfermarkt and return:
    - market_value: current market value
    - contract_end: contract expiration date
    - foot: preferred foot
    - minutes_pct: % of team minutes played this season

    Since the dataset has abbreviated first names (e.g. "T. Palacios"), and
    Transfermarkt's search struggles with abbreviations, this falls back to
    surname-only search and disambiguates candidates using:
    - first-name initial match (the "T." in "T. Palacios" must match "Thiago", "Tomás", etc.)
    - squad (strongest signal)
    - age (±1 year tolerance, since TM age may be a season old)
    - position
    """
    try:
        search_terms = extract_search_terms(player_name)
        candidates = []
        search_url = None

        # Extract the first-name initial from the original query, e.g. "T" from "T. Palacios"
        first_token = player_name.strip().split()[0] if player_name.strip() else ""
        initial = first_token[0].lower() if first_token and first_token[0].isalpha() else None

        target_age = None
        if age:
            try:
                target_age = int(age)
            except ValueError:
                pass

        # Try each search term until we find player profile candidates
        for term in search_terms:
            search_query = normalize_name(term)
            search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={requests.utils.quote(search_query)}"

            resp = requests.get(search_url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            term_candidates = []

            for table in soup.select("div.box table.items"):
                for row in table.select("tbody tr"):
                    name_cell = row.select_one("td.hauptlink a")
                    if not name_cell:
                        continue

                    href = name_cell.get("href", "")

                    # CRITICAL: only accept actual player profiles, reject agents/coaches/clubs/staff
                    if "/profil/spieler/" not in href:
                        continue

                    found_name = name_cell.get_text(strip=True)

                    # Match name (flexible — at least one meaningful word matches, e.g. surname)
                    name_parts = [p for p in player_name.lower().replace(".", "").split() if len(p) > 1]
                    found_lower = found_name.lower()
                    name_match = any(p in found_lower for p in name_parts)

                    if not name_match:
                        continue

                    # First-name initial match: "T." must match a found name starting with "T"
                    # (only enforced as a scoring signal, not a hard filter — TM nicknames vary)
                    initial_match = False
                    if initial:
                        found_first_token = found_name.strip().split()[0] if found_name.strip() else ""
                        if found_first_token and found_first_token[0].lower() == initial:
                            initial_match = True

                    # Club for this row — try multiple possible selectors (TM markup varies by table type)
                    club_title = ""
                    club_img = row.select_one("img.tiny_wappen") or row.select_one("td.zentriert img")
                    if club_img:
                        club_title = club_img.get("title", "") or club_img.get("alt", "")

                    # Position: look for any cell whose text matches common position abbreviations
                    pos_text = ""
                    for cell in row.select("td"):
                        txt = cell.get_text(strip=True)
                        if txt in ("CF", "AMF", "LW", "RW", "DMF", "CB", "LB", "RB",
                                   "LWB", "RWB", "LCMF", "RCMF", "GK", "ST", "CM",
                                   "LM", "RM", "CDM", "CAM", "LWF", "RWF"):
                            pos_text = txt
                            break

                    # Age: any numeric cell in plausible player-age range
                    found_age = None
                    for cell in row.select("td"):
                        txt = cell.get_text(strip=True)
                        if txt.isdigit() and 14 <= int(txt) <= 45:
                            found_age = int(txt)
                            break

                    age_match = (
                        target_age is not None
                        and found_age is not None
                        and abs(found_age - target_age) <= 1
                    )

                    # Squad match: strong signal, fuzzy on common name variations
                    squad_match = False
                    if squad:
                        sq = squad.lower()
                        ct = club_title.lower()
                        squad_match = sq in ct or ct in sq or any(
                            w in ct for w in sq.split() if len(w) > 3
                        )

                    pos_match = bool(pos) and pos.lower() in pos_text.lower()

                    player_url = "https://www.transfermarkt.com" + href
                    match = re.search(r"/spieler/(\d+)", href)
                    player_id = match.group(1) if match else None

                    # Score candidates: squad match is strongest, then age, then initial, then pos
                    score = (
                        (4 if squad_match else 0)
                        + (2 if age_match else 0)
                        + (2 if initial_match else 0)
                        + (1 if pos_match else 0)
                    )

                    term_candidates.append({
                        "url": player_url,
                        "id": player_id,
                        "name": found_name,
                        "club": club_title,
                        "age": found_age,
                        "squad_match": squad_match,
                        "initial_match": initial_match,
                        "score": score,
                    })

            if term_candidates:
                candidates = term_candidates
                break  # stop trying broader queries once we have player results

        if not candidates:
            raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found on Transfermarkt")

        # De-duplicate by href (search results often list the same player twice)
        seen_hrefs = set()
        deduped = []
        for c in candidates:
            href_key = c["url"]
            if href_key not in seen_hrefs:
                seen_hrefs.add(href_key)
                deduped.append(c)
        candidates = deduped

        candidates.sort(key=lambda c: -c["score"])

        # If multiple candidates tie on the top score AND we have a position to check,
        # visit each one's real profile to confirm exact position (search-page position is unreliable)
        if pos:
            top_score = candidates[0]["score"]
            tied = [c for c in candidates if c["score"] == top_score]
            if len(tied) > 1:
                pos_tokens = [p.strip().lower() for p in pos.split(",") if p.strip()]
                for c in tied:
                    try:
                        prof_resp = requests.get(c["url"], headers=HEADERS, timeout=8)
                        prof_soup = BeautifulSoup(prof_resp.text, "html.parser")
                        prof_text = prof_soup.get_text(" ", strip=True).lower()
                        c["profile_pos_match"] = any(tok in prof_text for tok in pos_tokens)
                    except Exception:
                        c["profile_pos_match"] = False
                candidates.sort(key=lambda c: (-c["score"], not c.get("profile_pos_match", False)))

        best = candidates[0]

        # If no squad was given or no match found, this candidate may be wrong —
        # flag low confidence in the response so the frontend can show a warning
        # Low confidence if we have no strong disambiguation signal confirming this is the right player
        low_confidence = best["score"] == 0 or (bool(squad) and not best["squad_match"] and not best["initial_match"])

        player_url = best["url"]
        player_id = best["id"]

        # 2. Get player profile page
        profile_resp = requests.get(player_url, headers=HEADERS, timeout=10)
        profile_resp.raise_for_status()
        profile_soup = BeautifulSoup(profile_resp.text, "html.parser")
        
        # Extract market value
        market_value = "N/D"
        mv_elem = profile_soup.select_one("a.data-header__market-value-wrapper")
        if mv_elem:
            market_value = parse_market_value(mv_elem.get_text(strip=True).split("Last")[0])
        
        # Extract contract end
        contract_end = "N/D"
        for item in profile_soup.select("li.data-header__label"):
            text = item.get_text(strip=True)
            if "Contract" in text or "Contrato" in text or "Jun" in text or "Dec" in text:
                span = item.select_one("span")
                if span:
                    contract_end = span.get_text(strip=True)
                    break
        
        # More reliable: look in info table
        for row in profile_soup.select("span.info-table__content--bold"):
            prev = row.find_previous("span", class_="info-table__content--regular")
            if prev and ("contract" in prev.get_text(strip=True).lower() or 
                        "contrato" in prev.get_text(strip=True).lower()):
                contract_end = row.get_text(strip=True)
                break
        
        # Extract foot
        foot = "N/D"
        for row in profile_soup.select("span.info-table__content--bold"):
            prev = row.find_previous("span", class_="info-table__content--regular")
            if prev and ("foot" in prev.get_text(strip=True).lower() or
                        "pie" in prev.get_text(strip=True).lower()):
                foot = row.get_text(strip=True)
                break
        
        # 3. Get minutes % from performance stats
        minutes_pct = None
        player_minutes = 0
        if player_id:
            # Get performance data page
            perf_url = f"https://www.transfermarkt.com/player/leistungsdaten/spieler/{player_id}/saison/2024/verein/0/liga/0/wettbewerb//pos/0/trainer_id/0/plus/1"
            perf_resp = requests.get(perf_url, headers=HEADERS, timeout=10)
            
            if perf_resp.status_code == 200:
                perf_soup = BeautifulSoup(perf_resp.text, "html.parser")
                
                # Find total minutes played this season
                player_minutes = 0
                for row in perf_soup.select("tfoot tr"):
                    cells = row.find_all("td")
                    # Minutes are usually in a specific column
                    for cell in cells:
                        text = cell.get_text(strip=True).replace(".", "").replace(",", "")
                        if "'" in cell.get_text(strip=True):
                            try:
                                player_minutes = int(text.replace("'", ""))
                                break
                            except:
                                pass
                
                # Calculate % (assuming ~3060 total team minutes per season — 34 games x 90 min)
                # Better: get from team page but this is a good approximation
                if player_minutes > 0:
                    total_team_minutes = 3060  # standard league season
                    minutes_pct = round((player_minutes / total_team_minutes) * 100, 1)
        
        return {
            "player": player_name,
            "matched_name": best["name"],
            "matched_club": best["club"],
            "low_confidence": low_confidence,
            "transfermarkt_url": player_url,
            "market_value": market_value,
            "contract_end": contract_end,
            "foot": foot,
            "minutes_played": player_minutes if player_minutes else None,
            "minutes_pct": minutes_pct,
            "total_team_minutes": 3060 if minutes_pct else None,
        }
    
    except HTTPException:
        raise
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail="Transfermarkt timeout")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "healthy"}
