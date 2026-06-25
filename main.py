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

def is_valid_age(row_text):
    match = re.search(r'(\d+)\s*([hdwy])', row_text)
    if match:
        val = int(match.group(1))
        unit = match.group(2)
        
        hours = 0
        if unit == 'h': hours = val
        elif unit == 'd': hours = val * 24
        elif unit == 'w': hours = val * 24 * 7
        elif unit == 'y': hours = val * 24 * 365
            
        if 24 <= hours < 168:
            return True
    return False

async def get_new_solana_tokens(page):
    print("[*] Scraping DexScreener (Firefox)...")
    await page.goto("https://dexscreener.com/solana", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)
    
    tokens = []
    seen_addresses = set()
    last_count = 0
    stable_scrolls = 0
    
    print("[*] Scroll de la page et collecte des tokens...")
    for scroll_count in range(30): # On descend jusqu'à 30 fois
        rows = await page.query_selector_all("a[href*='/solana/']")
        
        for row in rows:
            try:
                href = await row.get_attribute("href")
                if href and "/solana/" in href:
                    address = href.split("/solana/")[1].split("?")[0]
                    
                    # Si on n'a pas déjà vu ce token, on l'analyse
                    if len(address) >= 32 and address not in seen_addresses:
                        seen_addresses.add(address)
                        
                        row_text = await row.inner_text()
                        
                        if is_valid_age(row_text):
                            text_parts = row_text.split('\n')
                            dollar_strings = [s for s in text_parts if s.startswith('$') and len(s) > 1]
                            
                            if len(dollar_strings) >= 2:
                                liq_val = parse_dollar_value(dollar_strings[-2])
                                mcap_val = parse_dollar_value(dollar_strings[-1])
                                
                                if liq_val >= 20000 and mcap_val >= 100000:
                                    name = text_parts[1] if len(text_parts) > 1 else "Unknown"
                                    print(f"    -> [GARDÉ 1-7j] {name} | Liq: ${liq_val:,.0f} | Mcap: ${mcap_val:,.0f}")
                                    tokens.append({"name": name, "address": address})
            except:
                continue
                
        # Si on a trouvé 50 tokens, on arrête
        if len(tokens) >= 50:
            break
            
        # Si on n'a trouvé aucun nouveau token lors des 5 derniers scrolls, on arrête
        if len(seen_addresses) == last_count:
            stable_scrolls += 1
            if stable_scrolls > 5:
                print("[*] Fin de la page atteinte.")
                break
        else:
            stable_scrolls = 0
        last_count = len(seen_addresses)
        
        # Descendre sur la page
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
            "input[type='text'], input[type='search'], input[placeholder*='search' i], input[placeholder*='address' i]",
            timeout=15000
        )
        
        if search_input:
            await search_input.fill(address)
            await page.keyboard.press("Enter")
            
            print("    -> Waiting for TrenchRadar to calculate score...")
            await asyncio.sleep(10) 
            
        page.remove_listener("response", handle_response)
        
        if captured_score is not None:
            print(f"    -> Score (API) trouvé: {captured_score}/100")
            return captured_score
            
        body_text = await page.evaluate("document.body.innerText")
        
        match = re.search(r'(\d{1,3})\s*\n*Trust Score', body_text, re.IGNORECASE)
        if match:
            score = int(match.group(1))
            print(f"    -> Score (Texte) trouvé: {score}/100")
            return score
            
        match_fallback = re.search(r'(\d{1,3})\s*/\s*100', body_text)
        if match_fallback:
            print(f"    -> Score (Fallback) trouvé: {match_fallback.group(1)}/100")
            return int(match_fallback.group(1))
            
        print(f"    -> Score non trouvé.")
        return 0
            
    except Exception as e:
        page.remove_listener("response", handle_response)
        print(f"    -> Error scraping TrenchRadar: {e}")
        return 0

async def main():
    delay = random.uniform(1, 5)
    print(f"[*] Waiting for {delay:.2f} seconds...")
    await asyncio.sleep(delay)

    async with async_playwright() as p:
        # 1. Firefox pour DexScreener
        print("[*] Lancement de Firefox pour DexScreener...")
        ff_browser = await p.firefox.launch(headless=True)
        ff_context = await ff_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            viewport={'width': 1920, 'height': 1080},
            locale='en-US'
        )
        dex_page = await ff_context.new_page()
        await dex_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        tokens = await get_new_solana_tokens(dex_page)
        await ff_browser.close()
        
        if not tokens:
            print("[-] Aucun token trouvé.")
            return

        # 2. Chromium pour TrenchRadar
        print("[*] Lancement de Chromium pour TrenchRadar...")
        chr_browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        chr_context = await chr_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale='en-US'
        )
        tr_page = await chr_context.new_page()

        print("🤖 Agent starting up...")
        
        found_good_coin = False
        for token in tokens:
            score = await get_trenchradar_score(tr_page, token['address'])
            if score >= SCORE_THRESHOLD:
                found_good_coin = True
                message = f"🚀 <b>High Score Token Trouvé !</b>\n\nName: <b>{token['name']}</b>\nAddress: <code>{token['address']}</code>\n\nRésultat: Trust Score >= 70/100 sur TrenchRadar"
                await send_telegram_message(message)
            
            await asyncio.sleep(3)
            
        if not found_good_coin:
            print("[-] Aucun token n'a eu un score >= 70 cette fois.")
            
        await chr_browser.close()
        print("✅ Agent finished task.")

if __name__ == "__main__":
    asyncio.run(main())
