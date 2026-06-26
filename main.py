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

async def get_new_solana_tokens(page):
    print("[*] Scraping DexScreener avec les filtres natifs (URL)...")
    # NOUVEAU : On utilise votre URL avec tous les filtres intégrés
    url = "https://dexscreener.com/solana?rankBy=trendingScoreH6&order=desc&minLiq=20000&minMarketCap=100000&minAge=24&maxAge=168&profile=1"
    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)
    
    tokens = []
    seen_addresses = set()
    last_count = 0
    stable_scrolls = 0
    
    print("[*] Scroll mémorisé pour collecter tous les tokens filtrés...")
    for scroll_count in range(50): # On descend jusqu'à 50 fois
        rows = await page.query_selector_all("a[href*='/solana/']")
        
        for row in rows:
            try:
                href = await row.get_attribute("href")
                if href and "/solana/" in href:
                    address = href.split("/solana/")[1].split("?")[0]
                    
                    # Si c'est une vraie adresse et qu'on ne l'a pas déjà vue
                    if len(address) >= 32 and address not in seen_addresses:
                        seen_addresses.add(address)
                        
                        # Puisque l'URL a déjà tout filtré, on garde juste le nom et l'adresse
                        row_text = await row.inner_text()
                        text_parts = row_text.split('\n')
                        name = text_parts[1] if len(text_parts) > 1 else "Unknown"
                        
                        print(f"    -> [GARDÉ] {name} | {address[:8]}...")
                        tokens.append({"name": name, "address": address})
            except:
                continue
                
        if len(tokens) >= 50:
            break
            
        # Sécurité si la page ne défile plus
        if len(seen_addresses) == last_count:
            stable_scrolls += 1
            if stable_scrolls > 10:
                print("[*] Fin de la page atteinte.")
                break
        else:
            stable_scrolls = 0
        last_count = len(seen_addresses)
        
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
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
        
        search_input = await page.wait_for_selector("input[type='text']", timeout=15000)
        
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
