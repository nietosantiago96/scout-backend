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


@app.get("/player/{player_name}")
def get_player_data(player_name: str, squad: str = ""):
    """
    Search for a player on Transfermarkt and return:
    - market_value: current market value
    - contract_end: contract expiration date
    - foot: preferred foot
    - minutes_pct: % of team minutes played this season
    """
    try:
        # 1. Search for the player
        search_query = normalize_name(player_name)
        search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={requests.utils.quote(search_query)}"
        
        resp = requests.get(search_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Find player in search results
        candidates = []  # list of (player_url, player_id, found_name, club_title, squad_match)
        
        for table in soup.select("div.box table.items"):
            for row in table.select("tbody tr"):
                name_cell = row.select_one("td.hauptlink a")
                if not name_cell:
                    continue
                
                href = name_cell.get("href", "")
                
                # CRITICAL: only accept actual player profiles, reject agents/coaches/clubs
                if "/profil/spieler/" not in href:
                    continue
                
                found_name = name_cell.get_text(strip=True)
                
                # Match name (flexible — at least one meaningful word matches)
                name_parts = [p for p in player_name.lower().replace(".", "").split() if len(p) > 1]
                found_lower = found_name.lower()
                name_match = any(p in found_lower for p in name_parts)
                
                if not name_match:
                    continue
                
                # Get club from the row
                club_cell = row.select_one("td.zentriert img.tiny_wappen")
                club_title = club_cell.get("title", "") if club_cell else ""
                squad_match = bool(squad) and squad.lower() in club_title.lower()
                
                player_url = "https://www.transfermarkt.com" + href
                match = re.search(r"/spieler/(\d+)", href)
                player_id = match.group(1) if match else None
                
                candidates.append({
                    "url": player_url,
                    "id": player_id,
                    "name": found_name,
                    "club": club_title,
                    "squad_match": squad_match,
                })
        
        if not candidates:
            raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found on Transfermarkt")
        
        # Prefer exact squad match; otherwise take first candidate
        best = next((c for c in candidates if c["squad_match"]), candidates[0])
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
