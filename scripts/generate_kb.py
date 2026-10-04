"""
One-time script used to generate the Day 2 knowledge base articles.
Each article is a markdown file with a small YAML-style frontmatter block
(category, title, id) followed by the article body.

This script is not part of the running application — it's just how we
created knowledge_base/*.md the first time. Keep it in scripts/ for
reference / regenerating articles later if needed.
"""
import os

KB_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")

ARTICLES = {
    "account": [
        ("How do I create an account?",
         "To create an account, click **Sign Up** on the top right of the homepage. "
         "Enter your email address, choose a password (at least 8 characters), and "
         "click Create Account. You will receive a verification email — click the "
         "link inside it to activate your account."),
        ("How do I reset my password?",
         "Go to the login page and click **Forgot Password**. Enter the email "
         "address linked to your account. You will receive a password reset link "
         "valid for 30 minutes. If you don't see the email, check your spam folder."),
        ("How do I change my email address?",
         "Log in, go to **Account Settings > Profile**, and update your email "
         "address. You will need to verify the new email before the change takes "
         "effect. Your old email remains active until verification is complete."),
        ("How do I delete my account?",
         "Go to **Account Settings > Privacy > Delete Account**. This action is "
         "permanent and removes all your data after a 14-day grace period, during "
         "which you can cancel the deletion by logging back in."),
        ("Why was my account locked?",
         "Accounts are temporarily locked after 5 failed login attempts, for "
         "security. Wait 15 minutes and try again, or use the Forgot Password "
         "flow to reset your credentials immediately."),
    ],
    "billing": [
        ("What payment methods do you accept?",
         "We accept all major credit and debit cards (Visa, Mastercard, "
         "American Express), as well as PayPal. Bank transfers are available "
         "for annual enterprise plans only."),
        ("How do I update my billing information?",
         "Go to **Account Settings > Billing > Payment Methods** and click "
         "Edit next to your current card. Changes apply to your next billing "
         "cycle immediately."),
        ("How do I get an invoice for my payment?",
         "All invoices are automatically emailed after each successful charge. "
         "You can also download past invoices from **Account Settings > "
         "Billing > Invoice History**."),
        ("What is your refund policy?",
         "We offer a full refund within 14 days of purchase if you're not "
         "satisfied, no questions asked. Refunds are processed to the original "
         "payment method within 5-7 business days."),
        ("Why was I charged twice?",
         "Duplicate charges are usually a temporary authorization hold that "
         "disappears within 2-3 business days and is not an actual charge. If "
         "both charges are still present after 3 days, contact support with "
         "your order ID for a manual review."),
        ("How do I cancel my subscription?",
         "Go to **Account Settings > Billing > Subscription** and click Cancel "
         "Plan. You'll keep access until the end of your current billing period; "
         "no partial refunds are issued for unused time."),
    ],
    "technical": [
        ("The app is not loading. What should I do?",
         "First, try refreshing the page or restarting the app. Clear your "
         "browser cache or, on mobile, force-close and reopen the app. If the "
         "issue continues, check our status page for ongoing outages."),
        ("I'm getting a 'Session Expired' error.",
         "This happens when you've been logged out due to inactivity (after 24 "
         "hours) or a security-related logout. Simply log in again — your data "
         "is not affected."),
        ("How do I enable two-factor authentication (2FA)?",
         "Go to **Account Settings > Security > Two-Factor Authentication** "
         "and click Enable. Scan the QR code with an authenticator app like "
         "Google Authenticator, then enter the 6-digit code to confirm."),
        ("The page is loading very slowly.",
         "Slow loading is usually caused by a weak internet connection or "
         "heavy browser extensions. Try disabling ad-blockers for our site, or "
         "test on a different network to isolate the cause."),
        ("How do I clear my browser cache?",
         "In most browsers: open Settings > Privacy > Clear Browsing Data, "
         "select 'Cached images and files', and confirm. This resolves most "
         "display glitches and outdated version issues."),
        ("Which browsers are supported?",
         "We support the latest two versions of Chrome, Firefox, Safari, and "
         "Edge. Internet Explorer is not supported and may cause display "
         "issues."),
    ],
    "shipping": [
        ("How long does shipping take?",
         "Standard shipping takes 5-7 business days within the country. "
         "Express shipping (additional fee) takes 1-2 business days. "
         "International orders typically take 10-15 business days."),
        ("How do I track my order?",
         "Once your order ships, you'll receive a tracking number by email. "
         "You can also find it under **Order History** in your account, next "
         "to the relevant order."),
        ("Do you ship internationally?",
         "Yes, we ship to over 40 countries. International shipping costs and "
         "delivery times are calculated at checkout based on your address."),
        ("My order shows as delivered but I haven't received it.",
         "First, check with neighbors and your building's mailroom, as carriers "
         "sometimes mark packages delivered slightly early. If it doesn't turn "
         "up within 48 hours, contact support with your order ID for a "
         "replacement or refund."),
        ("Can I change my shipping address after ordering?",
         "You can change the address within 1 hour of placing the order, from "
         "**Order History > Edit Order**. After that, the order may already be "
         "processing and the address can no longer be changed."),
    ],
    "returns": [
        ("What is your return policy?",
         "Items can be returned within 30 days of delivery if unused and in "
         "original packaging. Some categories (e.g. perishables, personalized "
         "items) are not eligible for return."),
        ("How do I start a return?",
         "Go to **Order History**, select the item, and click Start Return. "
         "Print the prepaid return label provided and drop the package at any "
         "carrier location."),
        ("How long does a refund take after a return?",
         "Once we receive and inspect your returned item, refunds are "
         "processed within 5-7 business days to your original payment method."),
        ("Can I exchange an item instead of returning it?",
         "Yes, select Exchange instead of Return during the return process and "
         "choose the new size/color. Exchanges ship out as soon as we receive "
         "your original item."),
        ("Who pays for return shipping?",
         "Return shipping is free for defective or incorrect items. For "
         "change-of-mind returns, a small return shipping fee is deducted from "
         "your refund."),
    ],
    "general": [
        ("What are your customer support hours?",
         "Our support team is available Monday-Friday, 9 AM - 6 PM (your local "
         "time zone). This chatbot is available 24/7 for common questions and "
         "can escalate urgent issues to the team."),
        ("How do I contact a human agent?",
         "If this chatbot can't resolve your issue, type 'talk to a human' or "
         "ask directly — I'll flag your conversation for escalation to our "
         "support team, who will follow up by email."),
        ("Do you have a mobile app?",
         "Yes, our app is available on both the App Store and Google Play. "
         "Search for our brand name to download it for free."),
        ("Is my personal data safe?",
         "Yes. We use industry-standard encryption for data in transit and at "
         "rest, and we never sell your personal data to third parties. See our "
         "Privacy Policy for full details."),
    ],
}


def slugify(title: str) -> str:
    return (
        title.lower()
        .replace("?", "")
        .replace("'", "")
        .replace(",", "")
        .replace(".", "")
        .strip()
        .replace(" ", "-")
    )


def main():
    count = 0
    for category, articles in ARTICLES.items():
        cat_dir = os.path.join(KB_DIR, category)
        os.makedirs(cat_dir, exist_ok=True)
        for title, body in articles:
            count += 1
            article_id = f"{category}-{count:03d}"
            slug = slugify(title)
            filepath = os.path.join(cat_dir, f"{slug}.md")
            content = (
                f"---\n"
                f"id: {article_id}\n"
                f"category: {category}\n"
                f"title: \"{title}\"\n"
                f"---\n\n"
                f"# {title}\n\n"
                f"{body}\n"
            )
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
    print(f"Generated {count} knowledge base articles.")


if __name__ == "__main__":
    main()
