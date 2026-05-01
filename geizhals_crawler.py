import time
import random
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException
from supabase import create_client
from dotenv import load_dotenv
from dataclasses import dataclass
from typing import List, Optional
import re
import atexit

load_dotenv()
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SUPABASE_TABLE = "preise_at"

# ----------------------------------------------------------------
# ADD OR REMOVE URLS HERE
URLS = [
    "https://geizhals.at/philips-42oled810-a3479363.html",
    "https://geizhals.at/philips-48oled810-a3479438.html",
    "https://geizhals.at/philips-55oled810-a3479489.html",
    "https://geizhals.at/philips-65oled810-a3479563.html",
    "https://geizhals.at/philips-77oled810-a3479575.html",
]
# ----------------------------------------------------------------


@dataclass
class ProductOffer:
    brand: str
    sku: str
    verkäufer: str
    preis_ohne_versand: str
    preis_mit_versand: str
    datum: str
    uhrzeit: str
    url: str


class GeizhalsKrawler:
    """Selenium-based crawler for geizhals.at product pages."""

    _instances = []

    def __init__(self, headless=True, max_retries=3):
        self.driver = None
        self.wait = None
        self.max_retries = max_retries
        self._cookie_dismissed = False
        self.setup_driver(headless)
        GeizhalsKrawler._instances.append(self)
        atexit.register(self.cleanup_all_instances)

    @classmethod
    def cleanup_all_instances(cls):
        for instance in cls._instances:
            try:
                if instance.driver:
                    instance.driver.quit()
            except Exception:
                pass
        cls._instances.clear()

    def setup_driver(self, headless: bool):
        chrome_options = Options()

        if headless:
            chrome_options.add_argument("--headless")

        # Performance
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-plugins")
        # NOTE: do NOT disable JavaScript — Geizhals hides body via CSS until JS runs

        # Anti-detection
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            self.driver.set_page_load_timeout(30)
            self.wait = WebDriverWait(self.driver, 15)
        except Exception as e:
            print(f"Error setting up driver: {e}")
            raise

    def random_delay(self, min_s: float = 0.5, max_s: float = 2.0):
        time.sleep(random.uniform(min_s, max_s))

    def dismiss_cookie_banner(self):
        """Click the OneTrust 'Accept' button if the banner is visible."""
        if self._cookie_dismissed:
            return
        try:
            button = WebDriverWait(self.driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "#onetrust-accept-btn-handler"))
            )
            button.click()
            self._cookie_dismissed = True
            print("Cookie banner dismissed")
            self.random_delay(0.5, 1.0)
        except TimeoutException:
            pass  # Banner not present, no action needed
        except Exception as e:
            print(f"Cookie banner: {e}")

    # ------------------------------------------------------------------
    # Brand / SKU extraction
    # ------------------------------------------------------------------

    def extract_brand_and_sku(self) -> tuple[str, str]:
        """Extract brand and SKU from the product headline h1."""
        selectors = ["h1.variant__header__headline", "h1"]
        for selector in selectors:
            try:
                el = self.driver.find_element(By.CSS_SELECTOR, selector)
                title = el.text.strip()
                if title:
                    words = title.split()
                    brand = words[0] if words else "Unknown"
                    sku = " ".join(words[1:]) if len(words) > 1 else "Unknown"
                    return brand, sku
            except NoSuchElementException:
                continue
        return "Unknown", "Unknown"

    # ------------------------------------------------------------------
    # Merchant name extraction
    # ------------------------------------------------------------------

    def extract_merchant_name(self, offer_el) -> str:
        """Extract the merchant name from an offer element."""
        # Primary: visible caption text
        try:
            caption = offer_el.find_element(By.CSS_SELECTOR, ".merchant__logo-caption")
            name = caption.text.strip()
            if name:
                return name
        except NoSuchElementException:
            pass

        # Fallback: data-merchant-name attribute on the clickout link
        try:
            link = offer_el.find_element(By.CSS_SELECTOR, "a[data-merchant-name]")
            name = link.get_attribute("data-merchant-name")
            if name:
                return name
        except NoSuchElementException:
            pass

        return "Unbekannter Händler"

    # ------------------------------------------------------------------
    # Price parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_german_price(price_str: str) -> Optional[float]:
        """Parse a German-formatted price string to float.

        Handles:
        - '€ 1.079,00'   → 1079.0
        - '€ 27,90'      → 27.9
        - '€ 40,-'       → 40.0
        """
        try:
            clean = price_str.replace("€", "").replace("\xa0", "").strip()
            clean = clean.replace(",-", ",00")
            # German: dot = thousands separator, comma = decimal separator
            parts = clean.split(",")
            if len(parts) == 2:
                int_part = parts[0].replace(".", "").replace(" ", "")
                dec_part = parts[1].replace(" ", "")
                return float(f"{int_part}.{dec_part}")
            return float(clean.replace(".", "").replace(" ", ""))
        except Exception:
            return None

    @staticmethod
    def _format_german_price(value: float) -> str:
        """Format a float as a German price string, e.g. 1106.9 → '€ 1.106,90'."""
        try:
            # Python's locale-independent formatting
            int_part = int(value)
            dec_part = round((value - int_part) * 100)
            # Add thousands separator
            int_formatted = f"{int_part:,}".replace(",", ".")
            return f"€ {int_formatted},{dec_part:02d}"
        except Exception:
            return "Nicht gefunden"

    # ------------------------------------------------------------------
    # Price extraction from an offer element
    # ------------------------------------------------------------------

    def extract_prices(self, offer_el) -> tuple[str, str]:
        """Return (preis_ohne_versand, preis_mit_versand) for an offer element."""
        preis_ohne = "Nicht gefunden"
        preis_mit = "Nicht gefunden"

        # --- Price without shipping ---
        try:
            gh_price = offer_el.find_element(By.CSS_SELECTOR, ".offer__price .gh_price")
            preis_ohne = gh_price.text.strip()
        except NoSuchElementException:
            return preis_ohne, preis_mit

        # --- Price with shipping ---
        try:
            delivery_payment = offer_el.find_element(By.CSS_SELECTOR, ".offer__delivery-payment")
            delivery_text = delivery_payment.text

            if "GRATISVERSAND" in delivery_text.upper():
                preis_mit = preis_ohne
            else:
                # The first .gh_extracost in the delivery-payment block is the shipping cost
                # for the default "cheapest payment" filter already applied by the page.
                try:
                    extracost_el = delivery_payment.find_element(
                        By.CSS_SELECTOR, ".gh_extracost, [class*='extracost']"
                    )
                    shipping_str = extracost_el.text.strip()
                    base_val = self._parse_german_price(preis_ohne)
                    shipping_val = self._parse_german_price(shipping_str)

                    if base_val is not None and shipping_val is not None:
                        preis_mit = self._format_german_price(base_val + shipping_val)
                    else:
                        preis_mit = f"{preis_ohne} + {shipping_str}"

                except NoSuchElementException:
                    # No explicit shipping cost found — treat as same price
                    preis_mit = preis_ohne

        except NoSuchElementException:
            preis_mit = preis_ohne

        return preis_ohne, preis_mit

    # ------------------------------------------------------------------
    # Core extraction logic
    # ------------------------------------------------------------------

    def extract_offers(self, brand: str, sku: str, url: str) -> List[ProductOffer]:
        """Extract all offers visible on the current page."""
        offers = []
        now = datetime.now()
        datum = now.strftime("%Y-%m-%d")
        uhrzeit = now.strftime("%H:%M")

        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".offerlist .offer"))
            )
        except TimeoutException:
            print("Timeout: no offers found on page")
            return offers

        offer_elements = self.driver.find_elements(By.CSS_SELECTOR, ".offerlist .offer")
        print(f"Found {len(offer_elements)} offers")

        for i, offer_el in enumerate(offer_elements):
            try:
                merchant = self.extract_merchant_name(offer_el)
                preis_ohne, preis_mit = self.extract_prices(offer_el)

                if preis_ohne != "Nicht gefunden":
                    offers.append(
                        ProductOffer(
                            brand=brand,
                            sku=sku,
                            verkäufer=merchant,
                            preis_ohne_versand=preis_ohne,
                            preis_mit_versand=preis_mit,
                            datum=datum,
                            uhrzeit=uhrzeit,
                            url=url,
                        )
                    )

                if (i + 1) % 10 == 0:
                    print(f"  Processed {i + 1}/{len(offer_elements)} offers…")

            except Exception as e:
                print(f"Error on offer {i}: {e}")
                continue

        return offers

    def extract_product_info(self, url: str) -> List[ProductOffer]:
        """Load a Geizhals product page and extract all offers, with retries."""
        for attempt in range(self.max_retries):
            try:
                print(f"Loading: {url} (attempt {attempt + 1})")
                self.driver.get(url)
                self.random_delay(1.0, 2.0)

                self.wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

                # Dismiss cookie banner once per session
                self.dismiss_cookie_banner()

                brand, sku = self.extract_brand_and_sku()
                print(f"  Brand={brand}, SKU={sku}")

                return self.extract_offers(brand, sku, url)

            except Exception as e:
                print(f"Attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    self.random_delay(2.0, 4.0)

        print("All attempts failed for this URL")
        return []

    # ------------------------------------------------------------------
    # Supabase persistence
    # ------------------------------------------------------------------

    def save_to_supabase(self, all_offers: List[ProductOffer]):
        if not all_offers:
            print("No data to save")
            return
        try:
            client = create_client(SUPABASE_URL, SUPABASE_KEY)
            rows = [
                {
                    "brand": o.brand,
                    "sku": o.sku,
                    "verkäufer": o.verkäufer,
                    "preis_ohne_versand": o.preis_ohne_versand,
                    "preis_mit_versand": o.preis_mit_versand,
                    "datum": o.datum,
                    "uhrzeit": o.uhrzeit,
                    "url": o.url,
                }
                for o in all_offers
            ]
            client.table(SUPABASE_TABLE).insert(rows).execute()
            print(f"Saved {len(rows)} offers to Supabase table '{SUPABASE_TABLE}'")
        except Exception as e:
            print(f"Error saving to Supabase: {e}")

    # ------------------------------------------------------------------
    # Bulk crawl
    # ------------------------------------------------------------------

    def crawl_multiple_products(self, urls: List[str]) -> List[ProductOffer]:
        all_offers: List[ProductOffer] = []
        total = len(urls)
        print(f"Starting crawl of {total} URLs…")

        for i, url in enumerate(urls):
            print(f"\n--- {i + 1}/{total}: {url[:100]}…")
            offers = self.extract_product_info(url)
            if offers:
                all_offers.extend(offers)
                print(f"  Added {len(offers)} offers (running total: {len(all_offers)})")
            else:
                print("  No offers extracted")

            if i < total - 1:
                self.random_delay(0.5, 1.5)

        return all_offers

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self):
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None
        except Exception:
            pass
        if self in GeizhalsKrawler._instances:
            GeizhalsKrawler._instances.remove(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
