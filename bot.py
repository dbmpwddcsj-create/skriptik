import os
import re
import time
import random
import asyncio
import qrcode
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

# ---------- Конфигурация ----------
CITY = os.getenv("CITY", "Москва")
SEARCH_QUERY = os.getenv("SEARCH_QUERY", "фитнес зал")
SESSION_PATH = os.getenv("SESSION_PATH", "./wa_session")
SLEEP_HOURS = int(os.getenv("SLEEP_HOURS", "6"))

MESSAGE_WITH_SITE = os.getenv("MESSAGE_WITH_SITE", 
    "Здравствуйте! ... (текст для сайта)"
)
MESSAGE_NO_SITE = os.getenv("MESSAGE_NO_SITE",
    "Здравствуйте! ... (текст без сайта)"
)

PROCESSED_FILE = "processed_phones.txt"
os.makedirs(SESSION_PATH, exist_ok=True)

# ---------- Вспомогательные функции ----------
def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_processed(phone):
    with open(PROCESSED_FILE, 'a') as f:
        f.write(phone + "\n")

def normalize_phone(raw):
    raw = re.sub(r'[\s\-\(\)]', '', raw)
    if raw.startswith('8'):
        return '+7' + raw[1:]
    elif raw.startswith('7') and not raw.startswith('+'):
        return '+' + raw
    elif raw.startswith('9') and len(raw) == 10:
        return '+7' + raw
    elif raw.startswith('+'):
        return raw
    return None

# ---------- Ожидание QR ----------
async def wait_for_qr_scan(page):
    print("🔍 Проверка авторизации WhatsApp...")
    canvas = await page.query_selector('canvas[aria-label="Scan me!"]')
    if not canvas:
        return True
    parent = await canvas.evaluate_handle('el => el.parentElement')
    data_ref = await parent.get_attribute('data-ref')
    if not data_ref:
        div_ref = await page.query_selector('div[data-ref]')
        if div_ref:
            data_ref = await div_ref.get_attribute('data-ref')
    if not data_ref:
        print("❌ Не удалось извлечь данные для QR-кода.")
        return False
    qr = qrcode.QRCode(box_size=2, border=1)
    qr.add_data(data_ref)
    qr.make(fit=True)
    print("\n📲 ОТСКАНИРУЙТЕ QR-КОД В WHATSAPP:\n")
    qr.print_ascii(invert=True)
    print("\n⏳ Ожидание сканирования... (бесконечно)\n")
    while True:
        await asyncio.sleep(5)
        if await page.query_selector('canvas[aria-label="Scan me!"]') is None:
            print("✅ QR-код успешно отсканирован!")
            return True

# ---------- Отправка WhatsApp ----------
async def send_whatsapp(phone, message):
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            SESSION_PATH,
            headless=True,
            args=['--no-sandbox']
        )
        page = await browser.new_page()
        await page.goto("https://web.whatsapp.com", wait_until="domcontentloaded")
        if await page.query_selector('canvas[aria-label="Scan me!"]'):
            print("⚠️ Сессия отсутствует или недействительна.")
            await wait_for_qr_scan(page)
        chat_input = page.locator('div[contenteditable="true"][data-tab="10"]')
        await chat_input.click()
        await chat_input.fill(phone)
        await page.keyboard.press("Enter")
        await page.wait_for_selector('div[contenteditable="true"][spellcheck="true"]', timeout=15000)
        msg_box = page.locator('div[contenteditable="true"][spellcheck="true"]')
        await msg_box.click()
        await msg_box.fill(message)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(3000)
        await browser.close()

# ---------- Поиск через Google (с обработкой ошибок) ----------
async def search_google(query):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        ua = random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36'
        ])
        await page.set_extra_http_headers({'User-Agent': ua})
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}&num=30"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector('div#search', timeout=30000)
        except Exception:
            print("⚠️ Google не ответил (возможно капча). Пропускаем.")
            await browser.close()
            return []
        if await page.query_selector('form#captcha-form'):
            print("⚠️ Обнаружена капча Google. Пропускаем.")
            await browser.close()
            return []
        results = []
        items = await page.query_selector_all('div.g')
        for item in items:
            title_elem = await item.query_selector('h3')
            link_elem = await item.query_selector('a')
            if title_elem and link_elem:
                title = await title_elem.inner_text()
                link = await link_elem.get_attribute('href')
                if link and link.startswith('http') and 'google.com' not in link:
                    results.append({'name': title, 'url': link})
        await browser.close()
        return results

# ---------- Проверка WhatsApp на сайте ----------
async def check_whatsapp_on_page(url):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            page = await browser.new_page()
            await page.goto(url, timeout=20000, wait_until="domcontentloaded")
            content = await page.content()
            phone_patterns = [
                r'\+7\s*\(?\d{3}\)?\s*\d{3}[\s-]?\d{2}[\s-]?\d{2}',
                r'8\s*\(?\d{3}\)?\s*\d{3}[\s-]?\d{2}[\s-]?\d{2}',
                r'\b7\d{10}\b'
            ]
            found = []
            for pattern in phone_patterns:
                matches = re.findall(pattern, content)
                for m in matches:
                    clean = normalize_phone(m)
                    if clean:
                        found.append(clean)
            wa_links = re.findall(r'wa\.me/(\d+)|api\.whatsapp\.com/send\?phone=(\d+)', content)
            for link in wa_links:
                num = link[0] or link[1]
                if num:
                    found.append(normalize_phone(num))
            if found:
                return found[0]
            return None
    except Exception:
        return None

# ---------- НОВЫЙ ПАРСИНГ через Яндекс Карты ----------
async def search_yandex_maps(query, city):
    """Ищет организации на Яндекс.Картах, возвращает список с телефоном и признаком сайта"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = await browser.new_page()
        # Формируем URL для поиска на Яндекс.Картах
        url = f"https://yandex.ru/maps/?text={query.replace(' ', '+')}+{city.replace(' ', '+')}&sll=37.617698,55.755864&ll=37.617698,55.755864&z=12"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            print("⚠️ Яндекс Карты не загрузились.")
            return []
        # Ждём появления списка организаций
        await page.wait_for_timeout(5000)  # ждём подгрузку результатов
        # Селекторы могут меняться, но чаще всего карточки находятся в ul[data-testid="search-results"] > li
        cards = await page.query_selector_all('ul[data-testid="search-results"] > li')
        if not cards:
            # пробуем альтернативный селектор
            cards = await page.query_selector_all('div[data-testid="search-results"] div[data-testid="search-snippet"]')
        results = []
        for card in cards[:20]:
            # Извлекаем название
            name_elem = await card.query_selector('span[class*="title"]')
            if not name_elem:
                continue
            name = await name_elem.inner_text()
            # Извлекаем телефон (может быть в ссылке tel:)
            phone_elem = await card.query_selector('a[href^="tel:"]')
            phone = None
            if phone_elem:
                phone_raw = await phone_elem.get_attribute('href')
                phone = normalize_phone(phone_raw.replace('tel:', ''))
            # Проверяем наличие сайта (обычно ссылка с классом, содержащим "website")
            site_elem = await card.query_selector('a[href^="http"][target="_blank"]')
            has_site = site_elem is not None
            # Если сайт не найден, но есть ссылка на соцсети – не считаем сайтом
            if has_site:
                link = await site_elem.get_attribute('href')
                if link and any(s in link for s in ['vk.com', 'facebook.com', 'instagram.com', 'youtube.com']):
                    has_site = False
            if phone:
                results.append({
                    'name': name,
                    'phone': phone,
                    'has_site': has_site
                })
        await browser.close()
        return results

# ---------- Основной бесконечный цикл ----------
async def main():
    while True:
        print(f"\n🚀 Запуск нового цикла поиска ({CITY}, {SEARCH_QUERY})")
        processed = load_processed()
        all_contacts = []

        # 1. Google (если не работает – пропускаем)
        print("🔎 Поиск через Google...")
        google_results = await search_google(f"{SEARCH_QUERY} {CITY}")
        for item in google_results:
            phone = await check_whatsapp_on_page(item['url'])
            if phone and phone not in processed:
                all_contacts.append({'name': item['name'], 'phone': phone, 'has_site': True})
            await asyncio.sleep(1)

        # 2. Яндекс Карты (вместо 2GIS)
        print("🔎 Поиск через Яндекс Карты...")
        yandex_results = await search_yandex_maps(SEARCH_QUERY, CITY)
        for item in yandex_results:
            if item['phone'] not in processed:
                all_contacts.append(item)

        # Убираем дубли
        unique = {}
        for c in all_contacts:
            if c['phone'] not in unique:
                unique[c['phone']] = c
        contacts = list(unique.values())

        print(f"📋 Найдено уникальных контактов: {len(contacts)}")

        if contacts:
            for contact in contacts:
                phone = contact['phone']
                if phone in processed:
                    continue
                msg = MESSAGE_WITH_SITE if contact['has_site'] else MESSAGE_NO_SITE
                try:
                    await send_whatsapp(phone, msg)
                    save_processed(phone)
                    print(f"✅ Отправлено {phone} (сайт: {contact['has_site']})")
                    delay = random.randint(60, 180)
                    print(f"⏳ Ждём {delay} сек.")
                    await asyncio.sleep(delay)
                except Exception as e:
                    print(f"⚠️ Ошибка для {phone}: {e}")
        else:
            print("⚠️ Контактов не найдено. Повтор через 15 минут.")
            await asyncio.sleep(900)  # 15 минут
            continue

        print(f"💤 Цикл завершён. Следующий запуск через {SLEEP_HOURS} часов.")
        await asyncio.sleep(SLEEP_HOURS * 3600)

if __name__ == "__main__":
    asyncio.run(main())
