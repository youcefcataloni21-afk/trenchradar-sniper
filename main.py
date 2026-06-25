import asyncio
from playwright.async_api import async_playwright
import requests
import os
import random
import re

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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
                        tokens.append({"name": name, "address": address})
                        if len(tokens) >= 1: # Juste 1 pour le test
                            break 
        except:
            continue
    return tokens

async def main():
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

        token = tokens[0]
        print(f"[*] Test TrenchRadar pour {token['name']} ({token['address'][:8]}...)")

        # 2. Chromium pour TrenchRadar
        chr_browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
        chr_context = await chr_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale='en-US'
        )
        page = await chr_context.new_page()

        try:
            url = "https://www.trenchradar.net/app?chain=solana"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            search_input = await page.wait_for_selector(
                "input[type='text'], input[type='search'], input[placeholder*='search' i], input[placeholder*='address' i]",
                timeout=15000
            )
            
            if search_input:
                await search_input.fill(token['address'])
                await page.keyboard.press("Enter")
                
                print("    -> Waiting for TrenchRadar to calculate score...")
                try:
                    await page.wait_for_function(
                        """() => document.body.innerText.includes('Trust Score')""",
                        timeout=30000
                    )
                except:
                    print("    -> 'Trust Score' text did not appear in time.")
                    
                # Lire le texte de la page
                body_text = await page.evaluate("document.body.innerText")
                
                # NOUVEAU : Afficher 500 caractères autour de "Trust Score"
                score_index = body_text.find("Trust Score")
                if score_index != -1:
                    print(f"\n--- CONTEXTE AUTOUR DE 'TRUST SCORE' ---")
                    print(body_text[max(0, score_index-200):score_index+300])
                    print("----------------------------------------\n")
                else:
                    print("\n--- TEXTE DE LA PAGE (1000 chars) ---")
                    print(body_text[:1000])
                    print("-------------------------------------\n")
                
        except Exception as e:
            print(f"Error: {e}")
            
        await chr_browser.close()

if __name__ == "__main__":
    asyncio.run(main())
