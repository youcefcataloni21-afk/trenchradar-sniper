import asyncio
from playwright.async_api import async_playwright
import requests
import os
import random
import re

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SCORE_THRESHOLD = 70

async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
        print("[+] Message Telegram envoyé.")
    except:
        pass

def parse_dollar_value(val_str):
    try:
        val_str = val_str.replace('$', '').replace(',', '').strip()
        if 'M' in val_str:
            return float(val_str.replace('M', '')) * 1_000_000
        elif 'K' in val_str:
            return float(val_str.replace('K', '')) * 1_000
        else:
            return float(val_str)
    except:
        return 0

def get_age_hours(row_text):
    # On cherche un motif comme "5h", "2d", "1w"
    match = re.search(r'(\d+)\s*([hdwy])', row_text)
    if match:
        val = int(match.group(1))
        unit = match.group(2)
        if unit == 'h': return val
        elif unit == 'd': return val * 24
        elif unit == 'w': return val * 24 * 7
        elif unit == 'y': return val * 24 * 365
    return 0

async def get_new_solana_tokens(page):
    print("[*] Scraping DexScreener (Newest / Age Ascending)...")
    # NOUVEAU : On trie par Âge ascendant (du plus récent au plus ancien)
    await page.goto("https://dexscreener.com/solana?rank=age&order=asc", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)
    
    tokens = []
    seen_addresses = set()
    last_count = 0
    stable_scrolls = 0
    
    print("[*] Scroll et collecte avec TOUS les filtres (Age 24h-7j, Finances, Profil)...")
    for scroll_count in range(100):
        rows = await page.query_selector_all("a[href*='/solana/']")
        
        for row in rows:
            try:
                href = await row.get_attribute("href")
                if href and "/solana/" in href:
                    address = href.split("/solana/")[1].split("?")[0]
                    
                    if len(address) >= 32 and address not in seen_addresses:
                        seen_addresses.add(address)
                        
                        row_text = await row.inner_text()
                        age_hours = get_age_hours(row_text)
                        
                        # FILTRE 1 : L'Âge
                        # Si le token a plus de 7 jours (168h), comme la page est triée du plus récent au plus vieux,
                        # tout ce qui suit sera encore plus vieux. On arrête tout !
                        if age_hours > 168:
                            print("[*] Token de plus de 7 jours atteint. Arrêt de la recherche.")
                            return tokens
                            
                        # Si le token a moins de 24h, on l'ignore pour l'instant
                        if age_hours < 24:
                            continue
                            
                        text_parts = row_text.split('\n')
                        dollar_strings = [s for s in text_parts if s.startswith('$') and len(s) > 1]
                        
                        # FILTRE 2 : Finances
                        if len(dollar_strings) >= 2:
                            liq_val = parse_dollar_value(dollar_strings[-2])
                            mcap_val = parse_dollar_value(dollar_strings[-1])
                            
                            if liq_val >= 20000 and mcap_val >= 100000:
                                # FILTRE 3 : Profil (Twitter, Telegram, Website)
                                links = await row.eval_on_selector_all('a', '(elements) => elements.map(e => e.href)')
                                has_socials = False
                                for link in links:
                                    if 'twitter.com' in link or 'x.com' in link or 't.me' in link or 'telegram.me' in link or ('http' in link and 'dexscreener.com' not in link and 'solscan.io' not in link and 'solana.fm' not in link and 'pump.fun' not in link):
                                        has_socials = True
                                        break
                                        
                                if has_socials:
                                    name = text_parts[1] if len(text_parts) > 1 else "Unknown"
                                    print(f"    -> [GARDÉ 1-7j] {name} | Liq: ${liq_val:,.0f} | Mcap: ${mcap_val:,.0f} | Profil: Oui")
                                    tokens.append({"name": name, "address": address})
            except:
                continue
                
        if len(tokens) >= 50:
            break
            
        # Sécurité si la page ne défile plus
        if len(seen_addresses) == last_count:
            stable_scrolls += 1
            if stable_scrolls > 15:
                print("[*] Fin de la page atteinte.")
                break
        else:
            stable_scrolls = 0
        last_count = len(seen_addresses)
        
        await page.evaluate("window.scrollBy(0, 2000)")
        await asyncio.sleep(1.5)

    print(f"[+] Total tokens uniques vus: {len(seen_addresses)}")
    print(f"[+] Found {len(tokens)} tokens valides au total.")
    return tokens

async def get_trenchradar_score(page, address: str) -> int:
    print(f"[*] Checking TrenchRadar for {address[:8]}...")
    url = "https://www.trenchradar.net/app?chain=solana"
    
    captured_score = None
    
    async def handle_response(response):
        nonlocal captured_score
        if response.request.resource_type in ["xhr", "fetch"]:
            try:
                body = await response.text()
                if address.lower() in body.lower() or "trust_score" in body.lower() or "score" in body.lower():
                    match = re.search(r'"(?:trust_)?score":\s*"?(\d{1,3})"?', body, re.IGNORECASE)
                    if match:
                        captured_score = int(match.group(1))
            except:
                pass

    page.on("response", handle_response)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        search_input = await page.wait_for_selector(
            "input[type='text'], input[type
