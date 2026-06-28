import asyncio
from playwright.async_api import async_playwright
import requests
import os
import random
import re
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

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

def get_token_info(pair_address):
    """
    Interroge l'API DexScreener pour trouver la vraie adresse du token 
    ET vérifier l'âge de la plus vieille pool de ce token.
    """
    try:
        res = requests.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair_address}", timeout=5)
        data = res.json()
        if data.get('pair'):
            token_address = data['pair'].get('baseToken', {}).get('address')
            if not token_address: 
                return None, 999
                
            token_res = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=5)
            token_data = token_res.json()
            pairs = token_data.get('pairs', [])
            
            oldest_age_hours = 0
            now = datetime.now(timezone.utc)
            
            for p in pairs:
                created_at_val = p.get('pairCreatedAt')
                if created_at_val:
                    if isinstance(created_at_val, (int, float)):
                        created_at = datetime.fromtimestamp(created_at_val / 1000, timezone.utc)
                    else:
                        created_at = datetime.fromisoformat(str(created_at_val).replace('Z', '+00:00'))
                        
                    age_hours = (now - created_at).total_seconds() / 3600
                    if age_hours > oldest_age_hours:
                        oldest_age_hours = age_hours
                        
            return token_address, oldest_age_hours
            
    except:
        pass
    return None, 999

async def get_new_solana_tokens(page):
    print("[*] Scraping DexScreener (3 à 7 jours / 72h-168h)...")
    # Filtre 72h à 168h
    url = "https://dexscreener.com/solana?rankBy=pairAge&order=asc&minLiq=20000&minMarketCap=100000&minAge=72&maxAge=168&profile=1"
    
    rows = []
    for attempt in range(3):
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        rows = await page.query_selector_all("a[href*='/solana/']")
        if len(rows) > 0:
            print(f"[+] Cloudflare nous a laissé passer (Tentative {attempt+1}).")
            break
        print(f"[*] Bloqué par Cloudflare. Nouvelle tentative dans 10s...")
        await asyncio.sleep(10)
        
    if not rows:
        return []
    
    tokens = []
    seen_pairs = set()
    
    print("[*] Scroll mémorisé pour collecter les 25 premiers tokens...")
    for scroll_count in range(30):
        rows = await page.query_selector_all("a[href*='/solana/']")
        
        for row in rows:
            try:
                href = await row.get_attribute("href")
                if href and "/solana/" in href:
                    pair_address = href.split("/solana/")[1].split("?")[0]
                    
                    if len(pair_address) >= 32 and pair_address not in seen_pairs:
                        seen_pairs.add(pair_address)
                        
                        real_token_addr, token_age = get_token_info(pair_address)
                        if not real_token_addr:
                            continue
                            
                        # FILTRE ÂGE RÉEL : On veut entre 72h et 168h
                        if token_age < 72 or token_age > 168:
                            print(f"    -> [REJETÉ ÂGE] Token vieux de {token_age:.0f}h ignoré.")
                            continue
                            
                        row_text = await row.inner_text()
                        text_parts = row_text.split('\n')
                        name = text_parts[1] if len(text_parts) > 1 else "Unknown"
                        
                        print(f"    -> [GARDÉ 3-7j] {name} | Token: {real_token_addr[:8]}...")
                        tokens.append({"name": name, "address": real_token_addr})
                        
                        if len(tokens) >= 25:
                            return tokens
            except:
                continue
            
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1.5)

    print(f"[+] Found {len(tokens)} tokens valides au total.")
    return tokens

async def check_trenchradar(page, token):
    print(f"[*] Vérification TrenchRadar pour {token['name']}...")
    url = "https://www.trenchradar.net/app?chain=solana"
    
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        
        search_input = await page.wait_for_selector("input[type='text'], input[type='search'], input[placeholder*='search' i], input[placeholder*='address' i]", timeout=15000)
        
        if search_input:
            await search_input.fill(token['address'])
            await page.keyboard.press("Enter")
            
            print("    -> Attente du chargement de TrenchRadar...")
            await asyncio.sleep(10)
            
            body_text = await page.evaluate("document.body.innerText")
            body_lower = body_text.lower()
            
            # 1. Extraire le Trust Score
            score = 0
            match_score = re.search(r'(\d{1,3})\s*\n*Trust Score', body_text, re.IGNORECASE)
            if not match_score:
                match_score = re.search(r'Trust Score[:\s]*(\d{1,3})', body_lower)
            if match_score:
                score = int(match_score.group(1))
                
            # 2. Extraire le Top 5 Holders
            top5_pct = 100.0
            match_top5 = re.search(r'top\s*5\s*(?:holders?|hold).*?(\d+\.?\d*)\s*%', body_lower, re.DOTALL)
            if match_top5:
                top5_pct = float(match_top5.group(1))
            
            print(f"    -> Trust Score: {score}/100 | Top 5 Holders: {top5_pct}%")
            
            # RÈGLE FINALE : Score >= 70 ET Top 5 < 20%
            if score >= 70 and top5_pct < 20.0:
                return score, top5_pct
            else:
                return None, None
                
    except Exception as e:
        print(f"    -> Erreur: {e}")
        return None, None

async def main():
    delay = random.uniform(1, 5)
    print(f"[*] Waiting for {delay:.2f} seconds...")
    await asyncio.sleep(delay)

    async with async_playwright() as p:
        print("[*] Lancement de Chromium (Fenêtre réelle)...")
        chr_browser = await p.chromium.launch(headless=False, args=['--no-sandbox', '--disable-setuid-sandbox'])
        chr_context = await chr_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080},
            locale='fr-FR'
        )
        dex_page = await chr_context.new_page()
        await dex_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        tokens = await get_new_solana_tokens(dex_page)
        await dex_page.close()
        
        if not tokens:
            print("[-] Aucun token trouvé.")
            return

        tr_page = await chr_context.new_page()
        await tr_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        print("🤖 Agent starting up...")
        
        found_good_coin = False
        for token in tokens:
            score, top5_val = await check_trenchradar(tr_page, token)
            
            # Si le token respecte les deux règles (Score >= 70 ET Top5 < 20%)
            if score is not None:
                found_good_coin = True
                message = f"🚀 <b>High Score & Faible Concentration !</b>\n\nName: <b>{token['name']}</b>\nAddress: <code>{token['address']}</code>\n\nRésultat: Trust Score de {score}/100 et Top 5 Holders à {top5_val}% sur TrenchRadar"
                await send_telegram_message(message)
            
            await asyncio.sleep(3)
            
        if not found_good_coin:
            print("[-] Aucun token n'a eu un Score >= 70 et Top5 < 20% cette fois.")
            
        print("✅ Agent finished task.")

if __name__ == "__main__":
    asyncio.run(main())
