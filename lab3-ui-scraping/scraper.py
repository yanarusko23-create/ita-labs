"""
Лабораторна робота 3. UI-скрапінг robota.ua (варіант 13).
Каталог IT-вакансій -> jobs_data.csv
Метод: Playwright (headless) + перехоплення фонових відповідей (Network Interception).
"""
import re
from datetime import datetime, timedelta

import pandas as pd
from playwright.sync_api import (
    sync_playwright,
    Response,
    Route,
    TimeoutError as PlaywrightTimeoutError,  # таймаути Playwright
)

# ---------- Налаштування ----------
START_URL = "https://robota.ua/zapros/python-developer/ukraine"
TARGET_PATTERN = "getPublishedVacanciesList"   # патерн фонового GraphQL-запиту
MAX_SHOW_MORE_CLICKS = 1                       # скільки разів натиснути «Показати ще»
TARGET_CSV = "jobs_data.csv"

collected_items = []   # сюди потрапляють вакансії з перехоплених відповідей


# ---------- 1. Блокування важких ресурсів (Route Abort) ----------
def route_filter(route: Route):
    """Перериває завантаження зображень, медіа та шрифтів.
    CSS, JS, HTML та JSON дозволені (CSS потрібен для перевірок Playwright)."""
    if route.request.resource_type in ["image", "media", "font"]:
        route.abort()
    else:
        route.continue_()


# ---------- 2. Перехоплення JSON (Network Interception) ----------
def is_vacancies_response(response: Response) -> bool:
    """Чи є відповідь тим самим запитом зі списком вакансій."""
    return (
        TARGET_PATTERN in response.url
        and response.request.method == "POST"
        and response.status == 200
    )


def handle_network_response(response: Response):
    """Слухач: бере JSON з відповіді API й складає вакансії у collected_items."""
    if not is_vacancies_response(response):
        return
    if "application/json" not in response.headers.get("content-type", ""):
        return
    try:
        payload = response.json()
        items = payload["data"]["publishedVacancies"]["items"]
        print(f"[+] Перехоплено пачку: {len(items)} вакансій з API")
        for item in items:
            collected_items.append({
                "id": item.get("id"),
                "title": item.get("title"),
                "company": (item.get("company") or {}).get("name"),
                "city": (item.get("city") or {}).get("name"),
                "date_text": item.get("sortDateText"),
            })
    except Exception as err:
        print(f"[-] Помилка парсингу JSON: {err}")


# ---------- 3. Допоміжне: «4 години тому» -> календарна дата ----------
def parse_publication_date(text, now):
    """Сайт віддає відносну дату. Перераховуємо її у YYYY-MM-DD від моменту запуску.
    Якщо формат невідомий, залишаємо вихідний текст."""
    if not text:
        return ""
    t = text.lower().strip()
    if "щойно" in t or "сьогодні" in t:
        return now.date().isoformat()
    if "вчора" in t:
        return (now - timedelta(days=1)).date().isoformat()
    match = re.search(r"(\d+)", t)
    number = int(match.group(1)) if match else 1
    units = [
        (("хвилин",), timedelta(minutes=1)),
        (("годин",), timedelta(hours=1)),
        (("дн", "ден"), timedelta(days=1)),
        (("тиж",), timedelta(weeks=1)),
        (("місяц",), timedelta(days=30)),
    ]
    for keys, delta in units:
        if any(k in t for k in keys):
            return (now - number * delta).date().isoformat()
    return text


# ---------- 4. Запасний варіант: семантичні локатори ----------
def fallback_from_dom(page):
    """Якщо API перехопити не вдалося: беремо хоча б посилання на вакансії з DOM."""
    print("[!] Запасний режим: збір даних із DOM через локатори")
    links = page.locator("a[href*='/vacancy']")
    for i in range(links.count()):
        link = links.nth(i)
        title = link.inner_text().strip()
        href = link.get_attribute("href")
        if title:
            collected_items.append({
                "id": href, "title": title, "company": "Не вказано",
                "city": "Не вказано", "date_text": "",
            })


# ---------- 5. Основний сценарій ----------
def main():
    with sync_playwright() as p:
        # Headless-режим, але через встановлений Google Chrome:
        # вбудований Chromium отримував від Cloudflare відповідь 403.
        browser = p.chromium.launch(
            headless=True,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
        )
        page = context.new_page()
        page.route("**/*", route_filter)                     # блокуємо картинки/шрифти/медіа
        page.on("response", handle_network_response)         # слухач відповідей

        # Навігація з обробкою таймауту. Чекаємо першу відповідь із вакансіями.
        print(f"[*] Відкриваємо каталог: {START_URL}")
        try:
            with page.expect_response(is_vacancies_response, timeout=30000):
                page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError as err:
            print(f"[!] Таймаут під час відкриття сторінки / очікування API: {err}")

        # Пагінація: клік на «Показати ще» (Playwright сам чекає, поки кнопка стане клікабельною)
        for n in range(MAX_SHOW_MORE_CLICKS):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            show_more = page.get_by_text(
                re.compile(r"Показати ще|Показати більше|Більше вакансій", re.I)
            ).first
            try:
                print(f"[*] Клік №{n + 1} на «Показати ще»")
                with page.expect_response(is_vacancies_response, timeout=30000):
                    show_more.click(timeout=15000)
            except PlaywrightTimeoutError:
                print("[!] Кнопку «Показати ще» не знайдено або нова порція не прийшла")
                break

        if not collected_items:
            fallback_from_dom(page)

        browser.close()

    # ---------- 6. Очищення, мапінг, експорт ----------
    if not collected_items:
        print("[-] Дані не зібрано")
        return

    now = datetime.now()
    df = pd.DataFrame(collected_items)
    df = df.dropna(subset=["title"])
    df = df.drop_duplicates(subset=["id"])
    df["city"] = df["city"].fillna("Не вказано")
    df["company"] = df["company"].fillna("Не вказано")
    df["date"] = df["date_text"].apply(lambda t: parse_publication_date(t, now))

    result = df[["title", "company", "city", "date"]].rename(columns={
        "title": "Назва посади",
        "company": "Компанія",
        "city": "Локація",
        "date": "Дата публікації",
    })
    result.to_csv(TARGET_CSV, index=False, encoding="utf-8-sig")
    print(f"\n[✓] Успішно зібрано унікальних записів: {len(result)}")
    print(f"[✓] Артефакт збережено у файл: {TARGET_CSV}")


if __name__ == "__main__":
    main()
