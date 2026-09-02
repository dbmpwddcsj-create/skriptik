import os
import csv
import asyncio
import qrcode
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

SESSION_PATH = os.getenv("SESSION_PATH", "./wa_session")
CSV_FILE = "clients.csv"
PROCESSED_FILE = "processed_phones.txt"

MESSAGE_WITH_SITE = os.getenv("MESSAGE_WITH_SITE", 
    "Здравствуйте! ... (текст для сайта)"
)
MESSAGE_NO_SITE = os.getenv("MESSAGE_NO_SITE",
    "Здравствуйте! ... (текст без сайта)"
)

os.makedirs(SESSION_PATH, exist_ok=True)

def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, 'r') as f:
        return set(line.strip() for line in f)

def save_processed(phone):
    with open(PROCESSED_FILE, 'a') as f:
        f.write(phone + "\n")

def normalize_phone(raw):
    raw = ''.join(filter(str.isdigit, raw))  # оставляем только цифры
    if raw.startswith('8') and len(raw) == 11:
        return '+7' + raw[1:]
    elif raw.startswith('7') and len(raw) == 11:
        return '+' + raw
    elif len(raw) == 10:
        return '+7' + raw
    return None

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

async def main():
    processed = load_processed()
    if not os.path.exists(CSV_FILE):
        print(f"❌ Файл {CSV_FILE} не найден. Создайте его с колонками: phone,name,site")
        return
    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        contacts = list(reader)
    print(f"📋 Загружено {len(contacts)} контактов.")
    for contact in contacts:
        phone_raw = contact.get('phone', '').strip()
        phone = normalize_phone(phone_raw)
        if not phone:
            print(f"⚠️ Неверный номер: {phone_raw} — пропускаем")
            continue
        if phone in processed:
            print(f"⏭️ {phone} уже обработан, пропускаем.")
            continue
        name = contact.get('name', '').strip()
        site_value = contact.get('site', '').strip()
        has_site = bool(site_value)  # если есть любой текст в колонке site → сайт есть
        msg = MESSAGE_WITH_SITE if has_site else MESSAGE_NO_SITE
        msg = msg.replace("{name}", name) if name else msg
        try:
            await send_whatsapp(phone, msg)
            save_processed(phone)
            print(f"✅ Отправлено {phone} (сайт: {has_site})")
            await asyncio.sleep(60)  # пауза 1 минута
        except Exception as e:
            print(f"⚠️ Ошибка для {phone}: {e}")
    print("✅ Все сообщения отправлены.")

if __name__ == "__main__":
    asyncio.run(main())
