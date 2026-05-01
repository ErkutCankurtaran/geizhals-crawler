from geizhals_crawler import GeizhalsKrawler, URLS

with GeizhalsKrawler() as crawler:
    offers = crawler.crawl_multiple_products(URLS)
    crawler.save_to_supabase(offers)
