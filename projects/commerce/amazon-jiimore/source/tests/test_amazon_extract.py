import sys
import unittest
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_DIR))

from amazon_extract import (
    CUSTOMER_TAG_FIELDS,
    PRODUCT_FIELDS,
    REVIEW_FIELDS,
    extract_customer_tags,
    extract_product,
    extract_reviews,
    find_next_review_url,
    find_review_url,
    page_status,
)


class AmazonExtractTests(unittest.TestCase):
    def test_plain_review_body_fallback_and_empty_body_filter(self):
        source = """
        <html><body>
          <div data-hook="review" id="R1">
            <span class="a-profile-name">Alice</span>
            <h5 class="review-title">Simple title</h5>
            <div class="review-body">Works well <i class="a-icon">5 stars</i></div>
          </div>
          <div data-hook="review" id="R2">
            <span class="a-profile-name">Empty</span>
            <div data-hook="review-title">No body</div>
            <div data-hook="review-body"><span class="aok-hidden">hidden</span></div>
          </div>
        </body></html>
        """
        rows = extract_reviews(source, "B000TEST01")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["评论用户名"], "Alice")
        self.assertEqual(rows[0]["评论简要"], "Simple title")
        self.assertEqual(rows[0]["评论内容"], "Works well")
        self.assertNotIn("5 stars", rows[0]["评论内容"])
        self.assertEqual(set(REVIEW_FIELDS), set(rows[0]) - {"_review_id"})

    def test_visible_translation_wins_and_hidden_original_is_ignored(self):
        source = """
        <div data-hook="review" id="R3">
          <span class="a-profile-name">Bob</span>
          <a data-hook="review-title">
            <i class="a-icon"><span class="a-icon-alt">5.0 stars</span></i>
            <span class="cr-translated-review-content">很好用</span>
            <span class="cr-original-review-content aok-hidden">Very good</span>
          </a>
          <span data-hook="review-body">
            <span class="cr-translated-review-content">质量很好</span>
            <span class="cr-original-review-content" style="display: none">Great quality</span>
          </span>
        </div>
        """
        rows = extract_reviews(source, "B000TEST01")
        self.assertEqual(rows[0]["评论简要"], "很好用")
        self.assertEqual(rows[0]["评论内容"], "质量很好")
        self.assertNotIn("Very good", rows[0]["评论简要"])
        self.assertNotIn("Great quality", rows[0]["评论内容"])

    def test_review_links_keep_current_asin_and_dom_next(self):
        source = """
        <html><body>
          <a href="/product-reviews/B09OTHER99">推荐商品评论</a>
          <a data-hook="see-all-reviews-link-foot"
             href="/-/zh/product-reviews/B000TEST01?reviewerType=all_reviews">全部评论</a>
          <ul class="a-pagination">
            <li class="a-last"><a href="/-/zh/product-reviews/B000TEST01?pageNumber=2">下一页</a></li>
          </ul>
        </body></html>
        """
        base = "https://www.amazon.com/dp/B000TEST01"
        review_url = find_review_url(source, "B000TEST01", base)
        next_url = find_next_review_url(source, "B000TEST01", base)
        self.assertEqual(review_url, "https://www.amazon.com/-/zh/product-reviews/B000TEST01?reviewerType=all_reviews")
        self.assertEqual(next_url, "https://www.amazon.com/-/zh/product-reviews/B000TEST01?pageNumber=2")

    def test_price_prefers_payable_whole_fraction_over_list_and_installment(self):
        source = """
        <div id="dp-container">
          <div id="corePriceDisplay_desktop_feature_div">
            <div class="a-price priceToPay apex-pricetopay-value">
              <span class="a-price-symbol">$</span>
              <span class="a-price-whole">79<span class="a-price-decimal">.</span></span>
              <span class="a-price-fraction">00</span>
              <span class="a-offscreen"> </span>
            </div>
            <div class="a-price a-text-price apex-basisprice-value"><span class="a-offscreen">$109.99</span></div>
          </div>
          <div id="price-block-amount"><span class="a-offscreen">$19.75</span></div>
        </div>
        """
        item = extract_product(source, "B00FLYWNYQ", "https://www.amazon.com/dp/B00FLYWNYQ")
        self.assertEqual(item["价格"], "$79.00")

    def test_payable_price_components_can_be_aria_hidden(self):
        source = """
        <div id="dp-container"><div id="corePriceDisplay_desktop_feature_div">
          <div class="a-price priceToPay apex-pricetopay-value">
            <span class="a-offscreen"> </span>
            <span aria-hidden="true" class="a-price-symbol">$</span>
            <span aria-hidden="true" class="a-price-whole">79<span>.</span></span>
            <span aria-hidden="true" class="a-price-fraction">00</span>
          </div>
          <div id="price-block-amount"><span class="a-offscreen">$19.75</span></div>
        </div></div>
        """
        item = extract_product(source, "B00FLYWNYQ", "https://www.amazon.com/dp/B00FLYWNYQ")
        self.assertEqual(item["价格"], "$79.00")

    def test_price_prefers_nonempty_payable_offscreen(self):
        source = """
        <div id="dp-container">
          <div id="corePrice_feature_div">
            <div class="a-price apex-pricetopay-value"><span class="a-offscreen">$11.00</span></div>
            <div class="a-price a-text-price"><span class="a-offscreen">$19.99</span></div>
          </div>
        </div>
        """
        item = extract_product(source, "B09XMYFTB7", "https://www.amazon.com/dp/B09XMYFTB7")
        self.assertEqual(item["价格"], "$11.00")

    def test_product_main_area_avoids_recommendation_and_keeps_empty_title(self):
        source = """
        <html><body>
          <div id="recommendations">
            <span class="a-offscreen">$999.99</span>
            <span class="a-icon-alt">1.0 out of 5 stars</span>
          </div>
          <div id="dp-container">
            <span id="productTitle"></span>
            <div id="averageCustomerReviews"><span class="a-icon-alt">4.6 out of 5 stars</span></div>
            <a id="acrCustomerReviewLink"><span id="acrCustomerReviewText">44,260 ratings</span></a>
            <div id="corePrice_feature_div"><span class="a-offscreen">$11.00</span></div>
            <div id="feature-bullets"><ul><li>Clear finish</li><li>Vegan formula</li></ul></div>
          </div>
        </body></html>
        """
        item = extract_product(source, "B09XMYFTB7", "https://www.amazon.com/dp/B09XMYFTB7")
        self.assertEqual(set(PRODUCT_FIELDS), set(item))
        self.assertEqual(item["标题"], "")
        self.assertEqual(item["评分"], "4.6")
        self.assertEqual(item["评分数"], 44260)
        self.assertEqual(item["价格"], "$11.00")
        self.assertIn("Clear finish", item["商品介绍"])
        self.assertNotEqual(item["价格"], "$999.99")

    def test_page_status_requires_real_login_or_challenge(self):
        nav_only = '<a href="/ap/signin">Sign in</a><h1>Product</h1>'
        self.assertEqual(page_status(nav_only, "https://www.amazon.com/dp/B000TEST01"), "ready")
        login = '<form action="/ap/signin"><input id="ap_email"><input id="ap_password"></form>'
        self.assertEqual(page_status(login, "https://www.amazon.com/dp/B000TEST01"), "login_required")
        challenge = '<title>Robot Check</title><div id="captchacharacters">abc</div>'
        self.assertEqual(page_status(challenge, "https://www.amazon.com/dp/B000TEST01"), "challenge")

    def test_archived_review_html_with_comments_is_safe(self):
        archived = Path(__file__).resolve().parent / "fixtures" / "评论详情页.html"
        source = archived.read_text(encoding="utf-8")
        rows = extract_reviews(source, "B00FLYWNYQ")
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(page_status(source, "https://www.amazon.com/-/zh/product-reviews/B00FLYWNYQ"), "ready")

    def test_customer_tags_keep_original_schema(self):
        source = """
        <div id="cr-product-insights-cards">
          <div class="a-spacing-small">Customers say the grip is strong</div>
          <div id="aspect-button-group-0"><button><span>easy to use</span><svg><path fill="#067D62"/></svg></button></div>
          <div class="insight-bottom-sheet-content">
            customers mention 100 positive 80 negative 20
            <p>Customers find it easy to use.</p>
          </div>
        </div>
        """
        rows = extract_customer_tags(source, "B09XMYFTB7")
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(CUSTOMER_TAG_FIELDS), set(rows[0]))
        self.assertEqual(rows[0]["标签"], "easy to use")
        self.assertEqual(rows[0]["标签属性"], "1")
        self.assertEqual(rows[0]["customers_mention"], "100")
        self.assertEqual(rows[0]["positive"], "80")
        self.assertEqual(rows[0]["negative"], "20")


if __name__ == "__main__":
    unittest.main()
