import os
os.system("uv pip install playwright")
os.system("uv run playwright install chromium")

import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        
        errors = []
        page.on('console', lambda msg: print(f"CONSOLE [{msg.type}]: {msg.text}") if msg.type == "error" else None)
        page.on('pageerror', lambda err: print(f"PAGEERROR: {err}"))
        
        print("Navigating to cookies_login...")
        try:
            await page.goto('http://127.0.0.1:8000/cookies_login', timeout=5000)
            await page.wait_for_timeout(2000)
            
            print("Clicking Bilibili...")
            await page.locator(".tab-btn[onclick*='bilibili']").click()
            await page.wait_for_timeout(1000)
            
            print("Clicking Netease...")
            await page.locator(".tab-btn[onclick*='netease']").click()
            await page.wait_for_timeout(1000)
            
            print("DOM check for Netease fields:")
            fields = await page.locator("#dynamic-fields").inner_text()
            print("Fields render:", repr(fields))
            
        except Exception as e:
            print(f"Exception during playwright run: {e}")
            
        await browser.close()

asyncio.run(run())
