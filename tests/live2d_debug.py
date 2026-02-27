"""
Focused debug: capture model position at each screenshot and find the drift timing
"""
from playwright.sync_api import sync_playwright
import time
import json
import os

SCREENSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live2d_screenshots')

def main():
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
    
    console_logs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1920, 'height': 1080})
        
        def on_console(msg):
            console_logs.append({
                'time': time.time(),
                'type': msg.type,
                'text': msg.text[:300]
            })
        page.on('console', on_console)
        
        # Navigate
        print("=== LOADING PAGE ===")
        start_time = time.time()
        page.goto('http://localhost:48911', wait_until='domcontentloaded', timeout=30000)
        print(f"DOM loaded in {time.time() - start_time:.1f}s")
        
        # Even more rapid screenshots to catch the drift
        for i, delay in enumerate([0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.5, 3.0, 4.0, 6.0, 10.0, 15.0]):
            elapsed = time.time() - start_time
            wait_remaining = delay - elapsed
            if wait_remaining > 0:
                time.sleep(wait_remaining)
            
            fname = os.path.join(SCREENSHOTS_DIR, f'drift_{i}_{delay}s.png')
            page.screenshot(path=fname)
            
            pos_info = page.evaluate('''() => {
                const result = {};
                if (window.live2dManager) {
                    result.state = window.live2dManager._modelLoadState;
                    result.ready = window.live2dManager._isModelReadyForInteraction;
                    const m = window.live2dManager.currentModel;
                    if (m) {
                        result.x = Math.round(m.x * 100) / 100;
                        result.y = Math.round(m.y * 100) / 100;
                        result.alpha = m.alpha;
                        result.visible = m.visible;
                        result.scaleX = Math.round((m.scale?.x || 0) * 10000) / 10000;
                        // Check the PIXI canvas pixel at a few x positions to see where model is rendered
                        try {
                            const canvas = document.getElementById('live2d-canvas');
                            if (canvas) {
                                const ctx = canvas.getContext('2d');
                                // This won't work for WebGL canvas, so let's just check bounds
                                const bounds = m.getBounds();
                                result.boundsX = Math.round(bounds.x);
                                result.boundsY = Math.round(bounds.y);
                                result.boundsW = Math.round(bounds.width);
                                result.boundsH = Math.round(bounds.height);
                            }
                        } catch(e) {}
                    } else {
                        result.noModel = true;
                    }
                } else {
                    result.noManager = true;
                }
                return result;
            }''')
            print(f"  [{delay:5.1f}s] {json.dumps(pos_info, default=str)}")
        
        # Show filtered console logs - only the key initialization events
        print(f"\n=== KEY CONSOLE LOGS ({len(console_logs)} total) ===")
        key_keywords = ['Live2D Core', 'Live2D Model', 'Live2D Init', 'PIXI', '模型根路径',
                        '纹理', '情绪映射', '口型', 'Ticker', '吸附', '初始化完成',
                        '开始初始化', '加载到的偏好', '所有偏好', '偏好设置', 'anchor',
                        '位置已归一化', '缩放已归一化', '保存的缩放', '保存的位置',
                        'Tutorial', 'settling', 'ready', 'applying', '边界校正',
                        'applyModelSettings', '默认值']
        for log in console_logs:
            text_lower = log['text'].lower()
            if any(kw.lower() in text_lower for kw in key_keywords):
                relative_time = log['time'] - start_time
                print(f"  [{relative_time:6.2f}s] [{log['type']:7s}] {log['text'][:250]}")
        
        browser.close()
    
    print(f"\nDone! Screenshots in {SCREENSHOTS_DIR}")

if __name__ == '__main__':
    main()
