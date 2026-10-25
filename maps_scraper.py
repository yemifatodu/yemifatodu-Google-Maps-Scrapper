import logging
from typing import List, Optional
from playwright.sync_api import sync_playwright, Page
from dataclasses import dataclass, asdict
import pandas as pd
import argparse
from urllib.parse import quote_plus
import platform
import time
import os

PLACE_LINK_XPATH = '//a[contains(@href, "/maps/place")]'
NAME_XPATH = '//h1[contains(@class, "DUwDvf")]'


@dataclass
class Place:
    name: str = ""
    address: str = ""
    website: str = ""
    phone_number: str = ""
    reviews_count: Optional[int] = None
    reviews_average: Optional[float] = None
    store_shopping: str = "No"
    in_store_pickup: str = "No"
    store_delivery: str = "No"
    place_type: str = ""
    opens_at: str = ""
    introduction: str = ""


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )


def extract_text(page: Page, xpath: str) -> str:
    try:
        if page.locator(xpath).count() > 0:
            return page.locator(xpath).first.inner_text(timeout=5000)
    except Exception as e:
        logging.warning(f"Failed to extract text for xpath {xpath}: {e}")
    return ""


def extract_place(page: Page) -> Place:
    # XPaths (Google changes these often; update them here if fields come back empty)
    name_xpath = NAME_XPATH
    address_xpath = '//button[@data-item-id="address"]//div[contains(@class, "fontBodyMedium")]'
    website_xpath = '//a[@data-item-id="authority"]//div[contains(@class, "fontBodyMedium")]'
    phone_number_xpath = '//button[contains(@data-item-id, "phone:tel:")]//div[contains(@class, "fontBodyMedium")]'
    reviews_count_xpath = '//div[contains(@class, "TIHn2")]//div[contains(@class, "dmRWX")]//div//span//span//span[@aria-label]'
    reviews_average_xpath = '//div[contains(@class, "TIHn2")]//div[contains(@class, "dmRWX")]//div//span[@aria-hidden]'
    info1 = '//div[@class="LTs0Rc"][1]'
    info2 = '//div[@class="LTs0Rc"][2]'
    info3 = '//div[@class="LTs0Rc"][3]'
    opens_at_xpath = '//button[contains(@data-item-id, "oh")]//div[contains(@class, "fontBodyMedium")]'
    opens_at_xpath2 = '//div[@class="MkV9"]//span[@class="ZDu9vd"]//span[2]'
    place_type_xpath = '//div[@class="LBgpqf"]//button[contains(@class, "DkEaL")]'
    intro_xpath = '//div[contains(@class, "WeS02d")]//div[contains(@class, "PYvSYb")]'

    place = Place()
    place.name = extract_text(page, name_xpath)
    place.address = extract_text(page, address_xpath)
    place.website = extract_text(page, website_xpath)
    place.phone_number = extract_text(page, phone_number_xpath)
    place.place_type = extract_text(page, place_type_xpath)
    place.introduction = extract_text(page, intro_xpath) or "None Found"

    # Reviews Count
    reviews_count_raw = extract_text(page, reviews_count_xpath)
    if reviews_count_raw:
        try:
            temp = (reviews_count_raw.replace('\xa0', '').replace('(', '')
                    .replace(')', '').replace(',', ''))
            place.reviews_count = int(temp)
        except Exception as e:
            logging.warning(f"Failed to parse reviews count: {e}")

    # Reviews Average
    reviews_avg_raw = extract_text(page, reviews_average_xpath)
    if reviews_avg_raw:
        try:
            temp = reviews_avg_raw.replace(' ', '').replace(',', '.')
            place.reviews_average = float(temp)
        except Exception as e:
            logging.warning(f"Failed to parse reviews average: {e}")

    # Store Info
    for info_xpath in [info1, info2, info3]:
        info_raw = extract_text(page, info_xpath)
        if info_raw:
            temp = info_raw.split('·')
            if len(temp) > 1:
                check = temp[1].replace("\n", "").lower()
                if 'shop' in check:
                    place.store_shopping = "Yes"
                if 'pickup' in check:
                    place.in_store_pickup = "Yes"
                if 'delivery' in check:
                    place.store_delivery = "Yes"

    # Opens At
    opens_at_raw = extract_text(page, opens_at_xpath)
    if opens_at_raw:
        opens = opens_at_raw.split('⋅')
        if len(opens) > 1:
            place.opens_at = opens[1].replace("\u202f", "")
        else:
            place.opens_at = opens_at_raw.replace("\u202f", "")
    else:
        opens_at2_raw = extract_text(page, opens_at_xpath2)
        if opens_at2_raw:
            opens = opens_at2_raw.split('⋅')
            if len(opens) > 1:
                place.opens_at = opens[1].replace("\u202f", "")
            else:
                place.opens_at = opens_at2_raw.replace("\u202f", "")
    return place


def wait_for_new_place(page: Page, expected: str, previous_name: str, url_before: str):
    """Wait until the side panel shows the place we just clicked, not the previous one."""
    page.wait_for_function(
        """([expected, previous, urlBefore]) => {
            if (location.href === urlBefore) return false;
            const h = document.querySelector('h1[class*="DUwDvf"]');
            if (!h) return false;
            const now = h.innerText.trim().toLowerCase();
            if (!now) return false;
            const exp = (expected || '').toLowerCase();
            if (exp) return now === exp || now.includes(exp) || exp.includes(now);
            return now !== (previous || '').toLowerCase();
        }""",
        arg=[expected, previous_name, url_before],
        timeout=20000,
    )


def count_unique_places(page: Page) -> int:
    """Google lists some places more than once; count each place a single time."""
    hrefs = page.locator(PLACE_LINK_XPATH).evaluate_all(
        "els => els.map(e => e.getAttribute('href') || '')"
    )
    return len({h.split("/data=")[0] for h in hrefs if h})


def launch_browser(p):
    """Use an installed Chrome or Edge if possible, otherwise Playwright's bundled Chromium."""
    if platform.system() == "Windows":
        for channel in ("chrome", "msedge"):
            try:
                logging.info(f"Launching browser channel: {channel}")
                return p.chromium.launch(channel=channel, headless=False)
            except Exception as e:
                logging.warning(f"Could not launch {channel}: {e}")
    logging.info("Launching Playwright's bundled Chromium")
    return p.chromium.launch(headless=False)


def dismiss_consent(page: Page):
    """Click through Google's cookie/consent screen if it appears."""
    for label in ("Accept all", "Reject all", "I agree"):
        try:
            button = page.get_by_role("button", name=label)
            if button.count() > 0:
                logging.info(f"Consent screen found, clicking '{label}'")
                button.first.click(timeout=3000)
                page.wait_for_timeout(2000)
                return
        except Exception:
            pass


def scrape_places(search_for: str, total: int, on_progress=None) -> List[Place]:
    setup_logging()
    places: List[Place] = []
    with sync_playwright() as p:
        browser = launch_browser(p)
        page = browser.new_page()
        page.set_default_timeout(60000)
        try:
            # Go straight to the search results page; no need to type into the search box
            search_url = f"https://www.google.com/maps/search/{quote_plus(search_for)}?hl=en"
            page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(2000)
            dismiss_consent(page)

            page.wait_for_selector(PLACE_LINK_XPATH, timeout=60000)
            page.hover(PLACE_LINK_XPATH)

            previously_counted = 0
            stale_rounds = 0
            while True:
                page.mouse.wheel(0, 10000)
                page.wait_for_timeout(2000)
                found = count_unique_places(page)
                logging.info(f"Currently Found: {found}")
                if found >= total:
                    break
                if found == previously_counted:
                    stale_rounds += 1
                    if stale_rounds >= 3:
                        logging.info("Arrived at all available")
                        break
                else:
                    stale_rounds = 0
                previously_counted = found

            listings = []
            seen_links = set()
            for link in page.locator(PLACE_LINK_XPATH).all():
                try:
                    key = (link.get_attribute("href") or "").split("/data=")[0]
                except Exception:
                    continue
                if not key or key in seen_links:
                    continue
                seen_links.add(key)
                listings.append((link.locator("xpath=.."), (link.get_attribute("aria-label") or "").strip()))
                if len(listings) >= total:
                    break
            logging.info(f"Total Found: {len(listings)}")

            seen_places = set()
            previous_name = ""
            for idx, (listing, label) in enumerate(listings):
                try:
                    url_before = page.url
                    listing.click()
                    wait_for_new_place(page, label, previous_name, url_before)
                    time.sleep(2)  # let phone, website and reviews finish loading
                    place = extract_place(page)
                    # If the panel changed while we were reading, read it again
                    if extract_text(page, NAME_XPATH).strip() != place.name.strip():
                        time.sleep(2)
                        place = extract_place(page)
                    if not place.name:
                        logging.warning(f"No name found for listing {idx + 1}, skipping.")
                    else:
                        previous_name = place.name
                        key = (place.name.strip().lower(), place.address.strip().lower())
                        if key in seen_places:
                            logging.info(f"Skipping duplicate: {place.name}")
                        else:
                            seen_places.add(key)
                            places.append(place)
                except Exception as e:
                    logging.warning(f"Failed to extract listing {idx + 1}: {e}")
                if on_progress:
                    on_progress(idx + 1, len(listings))
        except Exception:
            logging.exception("Scraping stopped early")
            try:
                page.screenshot(path="debug.png")
                logging.info("Saved screenshot to debug.png")
            except Exception:
                pass
        finally:
            browser.close()
    return places


def save_places_to_csv(places: List[Place], output_path: str = "result.csv", append: bool = False):
    df = pd.DataFrame([asdict(place) for place in places])
    if not df.empty:
        if not append:
            for column in df.columns:
                if df[column].nunique() == 1:
                    df.drop(column, axis=1, inplace=True)
        file_exists = os.path.isfile(output_path)
        mode = "a" if append else "w"
        header = not (append and file_exists)
        df.to_csv(output_path, index=False, mode=mode, header=header, encoding="utf-8-sig")
        logging.info(f"Saved {len(df)} places to {output_path} (append={append})")
    else:
        logging.warning("No data to save. DataFrame is empty.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--search", type=str, help="Search query for Google Maps")
    parser.add_argument("-t", "--total", type=int, help="Total number of results to scrape")
    parser.add_argument("-o", "--output", type=str, default="result.csv", help="Output CSV file path")
    parser.add_argument("--append", action="store_true", help="Append results to the output file instead of overwriting")
    args = parser.parse_args()
    search_for = args.search or "turkish stores in toronto Canada"
    total = args.total or 1
    output_path = args.output
    append = args.append
    places = scrape_places(search_for, total)
    save_places_to_csv(places, output_path, append=append)


if __name__ == "__main__":
    main()
