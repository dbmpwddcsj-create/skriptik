import os
import csv
import asyncio
import qrcode
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

SESSION_PATH = os.getenv("SESSION_PATH", "./wa_session")
CSV_FILE = "clients.csv"

MESSAGE_WITH_SITE = os.getenv("MESSAGE_WITH_SITE", 
    "Здравствуйте! ... (текст для сайта)"
)
MESSAGE_NO_SITE = os.getenv("MESSAGE_NO_SITE",
    "Здравствуйте! ... (текст без сайта)"
)

os.makedirs(SESSION_PATH, exist_ok=True)

def normalize_phone(raw):
    raw = ''.join(filter(str.isdigit, raw))
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
    # Проверяем, есть ли файл CSV
    if not os.path.exists(CSV_FILE):
        print(f"❌ Файл {CSV_FILE} не найден. Создайте его с колонками: phone,name,site")
        return

    # Читаем все строки
    with open(CSV_FILE, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        contacts = list(reader)

    if not contacts:
        print("✅ В CSV нет контактов. Работа завершена.")
        return

    print(f"📋 Загружено {len(contacts)} контактов.")

    # Список для отправленных (чтобы потом удалить их из файла)
    sent_phones = []

    for idx, contact in enumerate(contacts):
        phone_raw = contact.get('phone', '').strip()
        phone = normalize_phone(phone_raw)
        if not phone:
            print(f"⚠️ Неверный номер: {phone_raw} — пропускаем")
            continue

        name = contact.get('name', '').strip()
        site_value = contact.get('site', '').strip()
        has_site = bool(site_value)
        msg = MESSAGE_WITH_SITE if has_site else MESSAGE_NO_SITE
        msg = msg.replace("{name}", name) if name else msg

        try:
            await send_whatsapp(phone, msg)
            sent_phones.append(idx)  # запоминаем индекс отправленного
            print(f"✅ Отправлено {phone} (сайт: {has_site})")
            await asyncio.sleep(60)  # пауза 1 минута
        except Exception as e:
            print(f"⚠️ Ошибка для {phone}: {e}")
            # Если ошибка критическая (например, сессия), можно прервать
            if "не авторизован" in str(e).lower():
                print("❌ Остановка из-за проблемы с авторизацией.")
                break

    # Удаляем отправленные строки из CSV (перезаписываем файл)
    if sent_phones:
        with open(CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            all_rows = list(reader)
        # Оставляем только те, которые не отправлены
        remaining = [row for i, row in enumerate(all_rows) if i not in sent_phones]
        if remaining:
            with open(CSV_FILE, 'w', encoding='utf-8', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['phone', 'name', 'site'])
                writer.writeheader()
                writer.writerows(remaining)
            print(f"🗑️ Удалено {len(sent_phones)} отправленных контактов из CSV. Осталось {len(remaining)}.")
        else:
            # Если все отправлены — удаляем файл или очищаем
            open(CSV_FILE, 'w', encoding='utf-8').close()
            print("🗑️ Все контакты отправлены. CSV очищен.")

    print("✅ Все сообщения отправлены.")

if __name__ == "__main__":
    asyncio.run(main())
