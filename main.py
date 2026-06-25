import asyncio
from playwright.async_api import async_playwright
import requests
import os
import random
import re

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SCORE_THRESHOLD = 75

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

def is_older_than_24h(row_text):
    match = re.search(r'(\d+)\s*([hdwy])', row_text)
    if match:
        val = int(match.group(1))
        unit = match.group(2)
        if unit in ('d', 'w', 'y'):
            return True
        if unit == 'h' and val >= 24:
            return True
    return False

async def get_new_solana_tokens(page):
    print("[*] Scraping DexScreener (Firefox)...")
    await page.goto("https://dexscreener.com/solana", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(5)
    
    try:
        await page.wait_for_selector("a[href*='/solana/']", timeout=20000)
    except:
        print("[-] DexScreener bloqué ou layout changé.")
        return []

    all_links = await page.query_selector_all("a[href*='/solana/']")
    token_rows = []
    for row in all_links:
        href = await row.get_attribute("href")
        if href:
            address = href.split("/solana/")[1].split("?")[0]
            if len(address) >= 32:
                token_rows.append(row)

    tokens = []
    for row in token_rows[:50]:
        try:
            href = await row.get_attribute("href")
            if href and "/solana/" in href:
                address = href.split("/solana/")[1].split("?")[0]
                row_text = await row.inner_text()
                
                if not is_older_than_24h(row_text):
                    continue
                    
                text_parts = row_text.split('\n')
                dollar_strings = [s for s in text_parts if '$' in s and len(s) < 15]
                
                if len(dollar_strings) >= 2:
                    liq_val = parse_dollar_value(dollar_strings[-2])
                    mcap_val = parse_dollar_value(dollar_strings[-1])
                    
                    if liq_val >= 20000 and mcap_val >= 100000:
                        name = text_parts[1] if len(text_parts) > 1 else "Unknown"
                        print(f"    -> [GARDÉ >24h] {name} | Liq: ${liq_val:,.0f} | Mcap: ${mcap_val:,.0f}")
                        tokens.append({"name": name, "address": address})
                        if len(tokens) >= 15:
                            break 
        except:
            continue
    return tokens

async def get_trenchradar_score(page, address: str) -> int:
    """
    Navigates to TrenchRadar, searches for a token address, and extracts the Trust Score.
    Requires an active Playwright Page object.
    """
    print(f"[*] Checking TrenchRadar for {address[:8]}...")
    url = "https://www.trenchradar.net/app?chain=solana"
    
    try:
        # 1. Go to TrenchRadar
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        # 2. Find the search bar
        search_input = await page.wait_for_selector(
            "input[type='text'], input[type='search'], input[placeholder*='search' i], input[placeholder*='address' i]",
            timeout=15000
        )
        
        if search_input:
            # 3. Type the address and hit Enter
            await search_input.fill(address)
            await page.keyboard.press("Enter")
            
            # 4. Wait for the page to calculate and display the score
            print("    -> Waiting for TrenchRadar to calculate score...")
            try:
                await page.wait_for_function(
                    """() => document.body.innerText.includes('Trust Score')""",
                    timeout=30000 # Waits up to 30 seconds for the text to appear
                )
            except Exception:
                print("    -> 'Trust Score' text did not appear in time.")
                return 0
        else:
            print("    -> Search bar not found.")
            return 0
            
        # 5. Extract the text from the page
        body_text = await page.evaluate("document.body.innerText")
        
        # 6. Use Regex to find the score (e.g., "35 \n Trust Score")
        match = re.search(r'(\d{1,3})\s*\n*Trust Score', body_text, re.IGNORECASE)
        if match:
            score = int(match.group(1))
            print(f"    -> Score found: {score}/100")
            return score
        else:
            # Fallback just in case they change the layout to "35/100"
            match_fallback = re.search(r'(\d{1,3})\s*/\s*100', body_text)
            if match_fallback:
                print(f"    -> Score (fallback) found: {match_fallback.group(1)}/100")
                return int(match_fallback.group(1))
                
            print(f"    -> Score not found in page text.")
            return 0
            
    except Exception as e:
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
                message = f"🚀 <b>High Score Token Trouvé !</b>\n\nName: <b>{token['name']}</b>\nAddress: <code>{token['address']}</code>\n\nRésultat: Trust Score >= 75/100 sur TrenchRadar"
                await send_telegram_message(message)
            
            # Petite pause entre chaque token pour ne pas surcharger TrenchRadar
            await asyncio.sleep(3)
            
        if not found_good_coin:
            print("[-] Aucun token n'a eu un score >= 75 cette fois.")
            
        await chr_browser.close()
        print("✅ Agent finished task.")

if __name__ == "__main__":
    asyncio.run(main())
