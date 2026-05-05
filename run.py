from geizhals_crawler import GeizhalsKrawler, URLS

with GeizhalsKrawler() as crawler:
    crawler.crawl_multiple_products(URLS)
