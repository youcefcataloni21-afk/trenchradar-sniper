import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    async with async_playwright() as p:
        print("[*] Lancement de Firefox pour DexScreener...")
        ff_browser = await p.firefox.launch(headless=True)
        ff_context = await ff_browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
            viewport={'width': 1920, 'height': 1080},
            locale='en-US'
        )
        page = await ff_context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        print("[*] Ouverture de DexScreener...")
        await page.goto("https://dexscreener.com/solana", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)
        
        try:
            # 1. Cliquer sur le bouton "Filters" (ou "Filtres")
            print("[*] Recherche du bouton Filtres...")
            filter_button = page.locator("button:has-text('Filters')").first
            await filter_button.click(timeout=5000)
            print("[+] Bouton Filtres cliqué !")
            await asyncio.sleep(2)
            
            # 2. Prendre une capture d'écran du menu ouvert
            await page.screenshot(path="filters_menu.png", full_page=False)
            print("[+] Capture d'écran 'filters_menu.png' sauvegardée.")
            
            # 3. Lire le texte du menu pour voir comment les options s'appellent
            body_text = await page.evaluate("document.body.innerText")
            print("\n--- TEXTE DE LA PAGE APRES CLIC FILTRES ---")
            print(body_text[:1500])
            print("-------------------------------------------\n")
            
        except Exception as e:
            print(f"[-] Impossible de cliquer sur Filtres: {e}")
            # Si le bouton n'est pas trouvé, on prend une capture de la page de base
            await page.screenshot(path="dex_page.png", full_page=False)
            print("[+] Capture d'écran 'dex_page.png' sauvegardée à la place.")
            
        await ff_browser.close()

if __name__ == "__main__":
    asyncio.run(main())
