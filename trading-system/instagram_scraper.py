#!/usr/bin/env python3
"""
Instagram Image Scraper with Browser Automation
Safely scrapes images from Instagram profiles using Playwright (headless browser).
Processes one profile at a time, using 'aaactive' folder during scraping,
then renames to the username when complete.
"""

import os
import re
import time
import json
import shutil
import asyncio
import gc
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote
import sys

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    # Try one more time in case it was just installed
    try:
        import importlib
        import sys
        if 'psutil' in sys.modules:
            del sys.modules['psutil']
        import psutil
        PSUTIL_AVAILABLE = True
    except ImportError:
        PSUTIL_AVAILABLE = False
        print("⚠️  Warning: psutil not installed. Memory monitoring will be limited.")
        print("  Install with: pip3 install psutil --break-system-packages --user")

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed. Please install it with:")
    print("  pip3 install playwright --break-system-packages --user")
    print("  python3 -m playwright install chromium")
    sys.exit(1)

# Resource management constants
MAX_MEMORY_PERCENT = 75  # Stop scraping if memory usage exceeds this percentage
MAX_IMAGES_PER_PROFILE = 999999  # Effectively no limit (very high number)
MAX_SCROLL_ATTEMPTS = 200  # Back to high value for completeness, but with memory management
MEMORY_CHECK_INTERVAL = 10  # Check memory every N scrolls (less frequent for speed)
DOM_CLEANUP_INTERVAL = 15  # Clean DOM every N scrolls (less frequent for speed)


def get_memory_usage():
    """Get current memory usage percentage."""
    if not PSUTIL_AVAILABLE:
        return 0  # Return 0 if psutil not available (no monitoring)
    try:
        system_memory = psutil.virtual_memory()
        return system_memory.percent
    except Exception:
        return 0


def check_memory_usage():
    """Check if memory usage is too high and return True if we should pause/stop."""
    if not PSUTIL_AVAILABLE:
        return False  # Skip memory checking if psutil not available
    memory_percent = get_memory_usage()
    if memory_percent > MAX_MEMORY_PERCENT:
        print(f"  ⚠️  High memory usage detected: {memory_percent:.1f}% (threshold: {MAX_MEMORY_PERCENT}%)")
        return True
    return False


class InstagramLinkParser(HTMLParser):
    """Parser to extract Instagram URLs from HTML bookmark file."""
    
    def __init__(self):
        super().__init__()
        self.instagram_urls = set()
        self.in_link = False
        self.current_url = None
    
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for attr, value in attrs:
                if attr == 'href' and value and 'instagram.com' in value.lower():
                    self.in_link = True
                    self.current_url = value
    
    def handle_endtag(self, tag):
        if tag == 'a':
            self.in_link = False
            if self.current_url:
                self.instagram_urls.add(self.current_url)
                self.current_url = None
    
    def handle_data(self, data):
        pass


def extract_instagram_usernames(html_file):
    """Extract unique Instagram usernames from bookmark HTML file."""
    parser = InstagramLinkParser()
    
    print(f"Reading {html_file}...")
    with open(html_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    parser.feed(content)
    
    usernames = set()
    for url in parser.instagram_urls:
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        
        if path:
            username = path.split('/')[0].split('?')[0]
            username = username.split('?')[0].split('#')[0]
            if username and username not in ['p', 'explore', 'stories', 'accounts']:
                usernames.add(username)
    
    return sorted(list(usernames))


def remove_username_from_bookmarks(html_file, username):
    """Remove a username's link from the bookmarks HTML file."""
    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Remove anchor tags containing the username
        pattern = r'<a[^>]*href="[^"]*' + re.escape(username) + r'[^"]*"[^>]*>.*?</a>'
        new_content = re.sub(pattern, '', content, flags=re.IGNORECASE | re.DOTALL)
        
        if new_content != content:
            with open(html_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
            return True
        return False
    except Exception as e:
        print(f"  Warning: Could not remove {username} from bookmarks: {e}")
        return False


def sanitize_folder_name(username):
    """Create a safe folder name from username."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        username = username.replace(char, '_')
    return username


async def extract_images_from_page(page, username, max_posts=2000):
    """
    Extract image URLs from Instagram profile page using Playwright.
    Yields batches of new image URLs as we scroll (for incremental downloading).
    Now includes memory management and resource limits to prevent system crashes.
    """
    image_urls = set()
    batch_size = 20  # Yield batches every 20 new images found
    
    try:
        print(f"  Loading profile page for @{username}...")
        profile_url = f"https://www.instagram.com/{username}/"
        
        # Navigate to profile with shorter timeout and more lenient wait
        try:
            await page.goto(profile_url, wait_until='domcontentloaded', timeout=30000)
            print(f"  ✓ Page loaded")
        except Exception as e:
            print(f"  ⚠️  Page load timeout/error: {e}, continuing anyway...")
        
        # Wait a bit for page to fully render (reduced for speed)
        print(f"  Waiting for content to load...")
        await page.wait_for_timeout(3000)  # Reduced from 5000 for faster loading
        
        # Check if profile exists or is private
        page_content = await page.content()
        if 'Sorry, this page' in page_content or 'Page Not Found' in page_content:
            print(f"  ERROR: Profile @{username} does not exist")
            return  # Can't return value from generator
        
        if 'This Account is Private' in page_content or 'This account is private' in page_content:
            print(f"  WARNING: Profile @{username} is private")
            return  # Can't return value from generator
        
        # Try to find post links/containers
        print(f"  Searching for posts...")
        
        # Wait for page to fully load - Instagram uses various selectors
        # Try multiple selectors that Instagram uses
        print(f"  Looking for content containers...")
        loaded = False
        for selector in ['article', 'main', '[role="main"]', 'section', 'div[style*="flex"]']:
            try:
                await page.wait_for_selector(selector, timeout=3000)
                print(f"    ✓ Found content using selector: {selector}")
                loaded = True
                break
            except Exception as e:
                continue
        
        if not loaded:
            print(f"  ⚠️  Could not find posts container, continuing anyway...")
            # Wait a bit more for dynamic content (reduced for speed)
            await page.wait_for_timeout(1500)  # Reduced from 3000 for faster loading
        
        # Helper function to extract images from currently loaded DOM
        # Get ALL images from each post (including ALL carousel images, not just cover)
        async def extract_images_now():
            """Extract actual image URLs from currently loaded DOM - ALL images from each post including carousel."""
            return await page.evaluate("""
                () => {
                    const images = new Set();
                    
                    // Strategy: Extract ALL images from each article/post (includes ALL carousel images)
                    const articles = document.querySelectorAll('article');
                    articles.forEach(article => {
                        // Get ALL img elements within this article (including hidden carousel images)
                        // This includes: visible images, hidden carousel images, lazy-loaded images
                        const allImgs = article.querySelectorAll('img');
                        
                        allImgs.forEach(img => {
                            // Try multiple sources - visible, lazy-loaded, srcset
                            let src = img.src || 
                                     img.getAttribute('srcset') || 
                                     img.getAttribute('data-src') || 
                                     img.getAttribute('srcSet') || 
                                     img.getAttribute('data-lazy-src') ||
                                     img.getAttribute('data-srcset');
                            
                            // Also check parent div for background-image (carousel images sometimes use this)
                            if (!src && img.parentElement) {
                                try {
                                    const computedStyle = window.getComputedStyle(img.parentElement);
                                    const bgImage = computedStyle.backgroundImage;
                                    if (bgImage && bgImage !== 'none' && bgImage.includes('url(')) {
                                        const match = bgImage.match(/url\\(['"]?([^'"]+)['"]?\\)/);
                                        if (match) src = match[1];
                                    }
                                } catch(e) {}
                            }
                            
                            if (src) {
                                // Handle srcset (multiple sizes - get highest quality)
                                if (src.includes(',')) {
                                    const parts = src.split(',');
                                    // Get the largest/highest quality version (usually last)
                                    src = parts[parts.length - 1].trim().split(' ')[0];
                                }
                                // Normalize URL - remove query parameters to avoid duplicates
                                src = src.split('?')[0].split('#')[0];
                                
                                // Only include actual post images from Instagram CDN
                                if (src && (src.includes('cdninstagram.com') || src.includes('fbcdn.net') || src.includes('instagram.com'))) {
                                    // Must be from scontent (post images) or /v/t51./t39./t41. (post images)
                                    // Exclude profile pictures, icons, thumbnails, but include ALL carousel images
                                    if ((src.includes('scontent') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) && 
                                        !src.includes('/t51.2885-19/') &&  // Profile picture pattern
                                        !src.includes('/t39.2885-19/') &&
                                        !src.includes('icon') && 
                                        !src.includes('static') && 
                                        !src.includes('logo') &&
                                        !src.includes('avatar') &&
                                        !src.includes('profile') &&
                                        !src.includes('thumbnail') &&
                                        !src.includes('emoji') &&
                                        !src.includes('badge')) {
                                        // Accept if it has size pattern OR is from /v/t51./t39./t41. (post images)
                                        if (src.includes('/s') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) {
                                            images.add(src);
                                        }
                                    }
                                }
                            }
                        });
                    });
                    
                    // Also try post links as backup (for carousel posts)
                    const postLinks = document.querySelectorAll('a[href*="/p/"], a[href*="/reel/"]');
                    postLinks.forEach(link => {
                        const linkImgs = link.querySelectorAll('img');
                        linkImgs.forEach(img => {
                            let src = img.src || img.getAttribute('srcset') || img.getAttribute('data-src') || 
                                     img.getAttribute('srcSet') || img.getAttribute('data-lazy-src');
                            if (src) {
                                if (src.includes(',')) {
                                    const parts = src.split(',');
                                    src = parts[parts.length - 1].trim().split(' ')[0];
                                }
                                src = src.split('?')[0].split('#')[0];
                                if (src && (src.includes('cdninstagram.com') || src.includes('fbcdn.net') || src.includes('instagram.com'))) {
                                    if ((src.includes('scontent') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) && 
                                        !src.includes('/t51.2885-19/') && !src.includes('/t39.2885-19/') &&
                                        !src.includes('icon') && !src.includes('static') && !src.includes('logo') &&
                                        !src.includes('avatar') && !src.includes('profile') && !src.includes('thumbnail') &&
                                        !src.includes('emoji') && !src.includes('badge')) {
                                        if (src.includes('/s') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) {
                                            images.add(src);
                                        }
                                    }
                                }
                            }
                        });
                    });
                    
                    return Array.from(images);
                }
            """)
        
        # Scroll down and extract images incrementally (collect as we scroll, not just at end)
        print(f"  Scrolling and extracting images incrementally (max {MAX_SCROLL_ATTEMPTS} scrolls, memory-aware)...")
        scroll_count = 0
        last_height = 0
        stable_count = 0
        max_scroll_attempts = MAX_SCROLL_ATTEMPTS  # Reduced to prevent memory issues
        
        # Initial extraction before scrolling
        initial_batch = await extract_images_now()
        image_urls.update(initial_batch)
        print(f"    Initial load: extracted {len(initial_batch)} images")
        
        # Debug: Check what's on the page
        if len(initial_batch) == 0:
            debug_info = await page.evaluate("""
                () => {
                    return {
                        articles: document.querySelectorAll('article').length,
                        postLinks: document.querySelectorAll('a[href*="/p/"]').length,
                        reelLinks: document.querySelectorAll('a[href*="/reel/"]').length,
                        allImages: document.querySelectorAll('img').length,
                        pageText: document.body.innerText.substring(0, 200)
                    };
                }
            """)
            print(f"    Debug: {debug_info['articles']} articles, {debug_info['postLinks']} post links, {debug_info['reelLinks']} reel links, {debug_info['allImages']} total images")
            if 'private' in debug_info['pageText'].lower() or 'follow' in debug_info['pageText'].lower():
                print(f"    ⚠️  Profile may be private or require login")
        
        # Yield initial batch for immediate download
        if initial_batch:
            cleaned = []
            for url in initial_batch:
                if url and isinstance(url, str):
                    if '/t51.2885-19/' not in url and 'profile' not in url.lower() and 'icon' not in url.lower() and 'static' not in url.lower() and 'logo' not in url.lower():
                        if 'scontent' in url or 'fbcdn.net' in url:
                            cleaned.append(url)
            if cleaned:
                yield cleaned
        
        for scroll_attempt in range(max_scroll_attempts):
            try:
                # Check memory usage periodically
                if scroll_attempt > 0 and scroll_attempt % MEMORY_CHECK_INTERVAL == 0:
                    if check_memory_usage():
                        print(f"  ⚠️  Memory usage high, performing cleanup...")
                        # Force garbage collection
                        gc.collect()
                        # Clean DOM by removing old elements (keep only recent posts visible)
                        try:
                            await page.evaluate("""
                                () => {
                                    // Remove old article elements that are far from viewport
                                    const articles = document.querySelectorAll('article');
                                    const viewportHeight = window.innerHeight;
                                    let removed = 0;
                                    articles.forEach(article => {
                                        const rect = article.getBoundingClientRect();
                                        // Remove articles that are more than 10 viewports above current position
                                        if (rect.top < -viewportHeight * 10) {
                                            article.remove();
                                            removed++;
                                        }
                                    });
                                    return removed;
                                }
                            """)
                        except Exception:
                            pass
                        await asyncio.sleep(1)  # Reduced pause for faster recovery
                    
                    # Continue - no image limit, memory management handles resource limits
                
                # Clean DOM periodically to prevent accumulation
                if scroll_attempt > 0 and scroll_attempt % DOM_CLEANUP_INTERVAL == 0:
                    try:
                        await page.evaluate("""
                            () => {
                                // Remove old images that are far above viewport to free memory
                                const images = document.querySelectorAll('img');
                                const viewportHeight = window.innerHeight;
                                let removed = 0;
                                images.forEach(img => {
                                    const rect = img.getBoundingClientRect();
                                    // Remove images that are more than 5 viewports above
                                    if (rect.top < -viewportHeight * 5) {
                                        const parent = img.closest('article, div');
                                        if (parent && parent.querySelectorAll('img').length > 1) {
                                            img.remove();
                                            removed++;
                                        }
                                    }
                                });
                                // Force garbage collection hint
                                if (window.gc) window.gc();
                                return removed;
                            }
                        """)
                        gc.collect()  # Python garbage collection
                    except Exception:
                        pass
                
                # Scroll down (faster scrolling)
                current_height = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)  # Reduced from 2000 for faster scrolling
                
                # Small additional scroll to trigger more loading
                await page.evaluate("window.scrollBy(0, 500)")
                await page.wait_for_timeout(500)  # Reduced from 1000 for faster scrolling
                
                # Extract images NOW (critical - do this during scrolling!)
                # Try both methods - the article-based method works better
                new_batch = await extract_images_now()
                
                # Also try the article-based method that gets ALL carousel images
                article_batch = await page.evaluate("""
                    () => {
                        const images = new Set();
                        
                        // Get ALL images from articles (including all carousel images)
                        const articles = document.querySelectorAll('article');
                        articles.forEach(article => {
                            // Find all images in this article/post (including all carousel images)
                            const allImgs = article.querySelectorAll('img, div[role="img"] img');
                            allImgs.forEach(img => {
                                let src = img.src || img.getAttribute('srcset') || img.getAttribute('data-src') || 
                                         img.getAttribute('data-lazy-src') || img.getAttribute('data-srcset');
                                
                                // Also check for background-image
                                if (!src && img.parentElement) {
                                    const style = img.parentElement.getAttribute('style') || window.getComputedStyle(img.parentElement).backgroundImage;
                                    if (style && style.includes('url(')) {
                                        const match = style.match(/url\\(['"]?([^'"]+)['"]?\\)/);
                                        if (match) src = match[1];
                                    }
                                }
                                
                                if (src) {
                                    if (src.includes(',')) {
                                        const parts = src.split(',');
                                        src = parts[parts.length - 1].trim().split(' ')[0];
                                    }
                                    src = src.split('?')[0].split('#')[0];
                                    if (src && (src.includes('cdninstagram.com') || src.includes('fbcdn.net') || src.includes('instagram.com'))) {
                                        if ((src.includes('scontent') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) && 
                                            !src.includes('/t51.2885-19/') && !src.includes('/t39.2885-19/') &&
                                            !src.includes('icon') && !src.includes('static') && !src.includes('logo') && 
                                            !src.includes('avatar') && !src.includes('profile') && !src.includes('thumbnail') &&
                                            !src.includes('emoji') && !src.includes('badge')) {
                                            if (src.includes('/s') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) {
                                                images.add(src);
                                            }
                                        }
                                    }
                                }
                            });
                        });
                        
                        return Array.from(images);
                    }
                """)
                
                # Combine both methods
                new_batch = list(set(new_batch + article_batch))
                
                new_height = await page.evaluate("document.body.scrollHeight")
                
                # Filter new images - normalize URLs to avoid duplicates
                new_urls = []
                for url in new_batch:
                    if url and isinstance(url, str):
                        # Normalize URL - remove query parameters and fragments to avoid duplicates
                        normalized_url = url.split('?')[0].split('#')[0]
                        if normalized_url not in image_urls:
                            # Additional filtering - only actual post images
                            if '/t51.2885-19/' not in normalized_url and '/t39.2885-19/' not in normalized_url and 'profile' not in normalized_url.lower() and 'icon' not in normalized_url.lower() and 'static' not in normalized_url.lower() and 'logo' not in normalized_url.lower() and 'avatar' not in normalized_url.lower():
                                if 'scontent' in normalized_url or 'fbcdn.net' in normalized_url or 'cdninstagram.com' in normalized_url:
                                    new_urls.append(normalized_url)
                
                # Now update image_urls with new URLs
                before_count = len(image_urls)
                image_urls.update(new_urls)
                after_count = len(image_urls)
                newly_found = after_count - before_count
                
                # Yield new images immediately for download
                if new_urls:
                    yield new_urls  # Yield immediately - don't wait
                    
                    # Continue scrolling - no image limit
                
                # Check if we're done (faster detection)
                if new_height == last_height and newly_found == 0:
                    stable_count += 1
                    if stable_count >= 3:  # Faster exit (reduced from 4)
                        print(f"    ✓ Reached end of page (collected {len(image_urls)} total images, no new content after {stable_count} scrolls)")
                        break
                else:
                    stable_count = 0
                    if scroll_attempt % 5 == 0:  # Less verbose output
                        memory_percent = get_memory_usage()
                        print(f"    Scroll {scroll_attempt + 1}: {len(image_urls)} images found... (Memory: {memory_percent:.1f}%)")
                
                last_height = new_height
                scroll_count += 1
                
                # Progress update
                if scroll_attempt > 0 and scroll_attempt % 20 == 0:
                    post_count = await page.evaluate("document.querySelectorAll('article').length")
                    print(f"    Progress: {scroll_attempt + 1} scrolls, {len(image_urls)} images, ~{post_count} posts...")
                    
            except Exception as e:
                print(f"    ⚠️  Scroll error: {e}, continuing...")
                await page.wait_for_timeout(2000)
        
        print(f"  Finished scrolling after {scroll_count} attempts")
        print(f"  Total images collected during scrolling: {len(image_urls)}")
        
        # Final comprehensive extraction pass - get any remaining images
        print(f"  Performing final comprehensive extraction pass...")
        final_batch = await extract_images_now()
        before_final = len(image_urls)
        image_urls.update(final_batch)
        after_final = len(image_urls)
        if after_final > before_final:
            print(f"    Final pass found {after_final - before_final} additional images")
            # Yield final batch
            final_urls = []
            for url in final_batch:
                if url and isinstance(url, str) and url not in image_urls:
                    if '/t51.2885-19/' not in url and 'profile' not in url.lower() and 'icon' not in url.lower() and 'static' not in url.lower() and 'logo' not in url.lower():
                        if 'scontent' in url or 'fbcdn.net' in url:
                            final_urls.append(url)
            if final_urls:
                yield final_urls
        
        # Also try extracting from article containers as a backup (this method actually works!)
        article_images = await page.evaluate("""
            () => {
                const images = new Set();
                // Try multiple selector strategies
                const selectors = [
                    'article img',
                    'a[href*="/p/"] img',
                    'a[href*="/reel/"] img',
                    '[role="article"] img'
                ];
                
                selectors.forEach(selector => {
                    const imgs = document.querySelectorAll(selector);
                    imgs.forEach(img => {
                        let src = img.src || img.getAttribute('srcset') || img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
                        if (src) {
                            // Handle srcset
                            if (src.includes(',')) {
                                const parts = src.split(',');
                                src = parts[parts.length - 1].trim().split(' ')[0];
                            }
                            // Normalize URL
                            src = src.split('?')[0].split('#')[0];
                            
                            // Include Instagram CDN images
                            if (src && (src.includes('cdninstagram.com') || src.includes('fbcdn.net') || src.includes('instagram.com'))) {
                                if ((src.includes('scontent') || src.includes('/v/t51.') || src.includes('/v/t39.') || src.includes('/v/t41.')) && 
                                    !src.includes('/t51.2885-19/') && !src.includes('/t39.2885-19/') &&
                                    !src.includes('icon') && !src.includes('static') && !src.includes('logo') && 
                                    !src.includes('avatar') && !src.includes('profile') && !src.includes('thumbnail')) {
                                    images.add(src);
                                }
                            }
                        }
                    });
                });
                return Array.from(images);
            }
        """)
        before_articles = len(image_urls)
        article_new = [url for url in article_images if url not in image_urls]
        if article_new:
            image_urls.update(article_new)
            print(f"    Article extraction found {len(article_new)} additional images")
            # Yield article batch
            article_urls = []
            for url in article_new:
                if url and isinstance(url, str):
                    if '/t51.2885-19/' not in url and 'profile' not in url.lower() and 'icon' not in url.lower() and 'static' not in url.lower() and 'logo' not in url.lower():
                        if 'scontent' in url or 'fbcdn.net' in url:
                            article_urls.append(url)
            if article_urls:
                yield article_urls
        
        print(f"  Found {len(image_urls)} total unique image URLs")
        
    except Exception as e:
        print(f"  ERROR: Error extracting images: {e}")
        import traceback
        traceback.print_exc()
        return  # Can't return value from generator


async def download_image(session, url, save_path, index):
    """Download a single image with retry logic."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Use async request
            response = await session.get(url, timeout=30000)
            
            if response.status != 200:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                    continue
                else:
                    print(f"    ✗ Image {index}: Failed with status {response.status}")
                    return False
            
            # Determine file extension from URL or Content-Type
            content_type = response.headers.get('content-type', '')
            url_path = url.split('?')[0]
            
            if '.jpg' in url_path or '.jpeg' in url_path or 'jpeg' in content_type or 'jpg' in content_type:
                ext = '.jpg'
            elif '.png' in url_path or 'png' in content_type:
                ext = '.png'
            elif '.webp' in url_path or 'webp' in content_type:
                ext = '.webp'
            else:
                ext = '.jpg'  # Default
            
            # Create filename with index for ordering
            filename = save_path / f"{index:04d}{ext}"
            
            # Write file
            content = await response.body()
            with open(filename, 'wb') as f:
                f.write(content)
            
            return True
            
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(2)
                continue
            else:
                print(f"    ✗ Image {index}: {str(e)[:50]}")
                return False
    
    return False


async def create_logged_in_context(p, session_storage_path=None, headless_after_login=False):
    """Create a browser context, optionally loading saved session."""
    # Launch browser - non-headless for initial login, then can switch to headless
    print("  Launching browser (this may take a moment)...")
    browser = await p.chromium.launch(
        headless=False,  # Show browser for login, can switch to headless later
        slow_mo=0,  # No slow motion - full speed
        args=[
            '--disable-blink-features=AutomationControlled',
            '--disable-dev-shm-usage',  # Prevent /dev/shm exhaustion
            '--disable-gpu',  # Disable GPU to save resources
            '--no-sandbox',  # Reduce resource overhead (use with caution)
            '--disable-setuid-sandbox',
            '--disable-extensions',
            '--disable-plugins',
            # Note: We don't disable images because we need to detect them in the DOM
            # Memory is managed through DOM cleanup instead
        ]
    )
    
    # If we have a saved session, try to load it
    if session_storage_path and Path(session_storage_path).exists():
        print("  Loading saved Instagram session...")
        try:
            context = await browser.new_context(
                storage_state=session_storage_path,
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
            )
            # Test if session is still valid - use a simpler check
            page = await context.new_page()
            try:
                await page.goto('https://www.instagram.com/', timeout=30000, wait_until='domcontentloaded')
                await page.wait_for_timeout(3000)
                
                # Check if logged in by looking at the URL
                current_url = page.url
                await page.close()
                
                # If we're redirected to login, session is expired
                if 'accounts/login' not in current_url and 'login' not in current_url:
                    print("  ✓ Using existing logged-in session")
                    print("  ✓ Browser will stay visible for monitoring (helper instance)")
                    return browser, context, False  # browser, context, needs_login
                else:
                    print("  Session expired, will need to log in again")
                    await context.close()
                    # Keep browser open for login
            except Exception as e:
                print(f"  Error checking session: {e}, will need to log in")
                await page.close()
                await context.close()
        except Exception as e:
            print(f"  Could not load session: {e}")
            # Continue to create new context
    
    # Create new context for login
    print("  Creating new browser context...")
    context = await browser.new_context(
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        viewport={'width': 1920, 'height': 1080},
        locale='en-US',
        timezone_id='America/New_York',
    )
    return browser, context, True  # browser, context, needs_login


async def login_to_instagram(context, session_storage_path=None):
    """Login to Instagram manually and save session."""
    page = await context.new_page()
    print("\n" + "="*60)
    print("LOGIN REQUIRED")
    print("="*60)
    print("A browser window will open. Please log in to Instagram manually.")
    print("The browser will stay open until you press ENTER here.")
    print("After logging in, press ENTER in this terminal to continue...")
    print("="*60)
    
    try:
        # Navigate to login page
        print("\nOpening Instagram login page...")
        print("(Browser window will open and stay open until you press ENTER)")
        
        # Use domcontentloaded instead of networkidle to avoid timing issues
        await page.goto('https://www.instagram.com/accounts/login/', timeout=60000, wait_until='domcontentloaded')
        
        # Wait for page to fully load
        await page.wait_for_timeout(5000)
        
        print("\n✓ Browser window is open!")
        print("  Please log in to Instagram in the browser window.")
        print("  Take your time - the browser will stay open.")
        print("\n  When you're logged in and see your Instagram feed,")
        print("  come back here and press ENTER...")
        
        # Wait for user to log in - this blocks until ENTER is pressed
        # The browser will stay open during this time
        try:
            user_input = input("\n>>> Press ENTER after you have logged in to Instagram: ")
        except (EOFError, KeyboardInterrupt):
            print("\nLogin cancelled.")
            await page.close()
            return False
        
        # Wait a moment for any final page loads
        await page.wait_for_timeout(2000)
        
        # Check current URL to verify login
        current_url = page.url
        print(f"\nCurrent URL after login: {current_url}")
        
        # Verify login by checking if we're on a logged-in page
        if 'accounts/login' in current_url or '/login' in current_url:
            print("\n⚠️  WARNING: It looks like you might not be logged in.")
            print("   Current URL suggests you're still on the login page.")
            retry = input("   Try again? (y/n): ").strip().lower()
            if retry == 'y':
                await page.close()
                return await login_to_instagram(context, session_storage_path)
            else:
                print("ERROR: Login not completed.")
                await page.close()
                return False
        
        print("✓ Login appears successful!")
        
        # Try to verify by checking for common logged-in elements
        try:
            # Check if we can see the home feed or profile
            await page.wait_for_timeout(2000)
            page_content = await page.content()
            if 'home' in current_url.lower() or 'explore' in current_url.lower() or 'accounts/edit' in current_url:
                print("✓ Verified logged-in state")
        except:
            pass
        
        # Save session state
        if session_storage_path:
            try:
                await context.storage_state(path=session_storage_path)
                print(f"✓ Session saved to {session_storage_path}")
            except Exception as e:
                print(f"⚠️  Warning: Could not save session: {e}")
        
        # Keep page open briefly to show confirmation, then close
        print("\n✓ Login verified! Closing browser window and continuing...")
        await page.wait_for_timeout(2000)
        await page.close()
        return True
        
    except Exception as e:
        print(f"ERROR during login: {e}")
        import traceback
        traceback.print_exc()
        await page.close()
        return False


async def scrape_instagram_profile_async(username, base_dir, context, delay_between_requests=5):
    """
    Scrape images from an Instagram profile using Playwright.
    
    Args:
        username: Instagram username to scrape
        base_dir: Base directory for Instagram folders
        context: Browser context (with logged-in session)
        delay_between_requests: Delay in seconds between requests
    
    Returns:
        True if successful, False otherwise
    """
    print(f"\n{'='*60}")
    print(f"Processing: @{username}")
    print(f"{'='*60}")
    
    # Create base directory if it doesn't exist
    base_path = Path(base_dir)
    base_path.mkdir(parents=True, exist_ok=True)
    
    # Sanitize username for folder name
    safe_username = sanitize_folder_name(username)
    profile_folder = base_path / safe_username
    
    # Create folder with username immediately
    profile_folder.mkdir(parents=True, exist_ok=True)
    print(f"Created folder: {profile_folder}")
    
    try:
        page = await context.new_page()
        
        # Add minimal delay before first request
        await asyncio.sleep(1)  # Minimal delay - reduced from delay_between_requests
            
        try:
            # Extract and download images incrementally as we scroll
            print(f"  Extracting and downloading images incrementally...")
            
            # Download images with rate limiting
            downloaded = 0
            failed = 0
            failed_urls = []
            image_counter = 1
            
            # Create a download page (still uses the logged-in context)
            download_page = await context.new_page()
            
            # Process batches as they come from the generator
            batch_count = 0
            async for image_batch in extract_images_from_page(page, username, max_posts=2000):
                batch_count += 1
                if not image_batch:
                    continue
                
                print(f"  Downloading batch {batch_count} ({len(image_batch)} images)...")
                
                # Download each image in the batch (no limit - download all)
                for img_url in image_batch:
                    try:
                        response = await download_page.request.get(img_url, timeout=30000)
                        if response.status == 200:
                            # Determine extension
                            url_path = img_url.split('?')[0]
                            if '.jpg' in url_path or '.jpeg' in url_path:
                                ext = '.jpg'
                            elif '.png' in url_path:
                                ext = '.png'
                            elif '.webp' in url_path:
                                ext = '.webp'
                            else:
                                ext = '.jpg'
                            
                            filename = profile_folder / f"{image_counter:04d}{ext}"
                            content = await response.body()
                            with open(filename, 'wb') as f:
                                f.write(content)
                            downloaded += 1
                            image_counter += 1
                            
                            if downloaded % 10 == 0 or downloaded == 1:
                                print(f"    Downloaded {downloaded} images so far...")
                        else:
                            failed += 1
                            failed_urls.append(img_url)
                            if failed <= 3:
                                print(f"    ✗ Image {image_counter}: HTTP {response.status}")
                    except Exception as e:
                        failed += 1
                        failed_urls.append(img_url)
                        if failed <= 3:
                            print(f"    ✗ Image {image_counter}: {str(e)[:60]}")
                    
                    # Minimal delay - reduced for faster downloads
                    await asyncio.sleep(0.05)  # Reduced from 0.1 for faster downloads
            
            await download_page.close()
            await page.close()  # Close page after all processing
            
            # Force garbage collection after closing pages
            gc.collect()
            
            print(f"\n  Download complete: {downloaded} images downloaded, {failed} failed")
            
            if downloaded == 0:
                print(f"  ERROR: No images were successfully downloaded")
                if failed_urls:
                    print(f"  Failed URLs (first 3):")
                    for url in failed_urls[:3]:
                        print(f"    - {url[:80]}...")
                # Keep folder even if empty (don't delete)
                return False
            
        except Exception as e:
            print(f"  ERROR: Browser error: {e}")
            import traceback
            traceback.print_exc()
            # Keep folder on error (don't delete - user may want to inspect)
            if profile_folder.exists():
                files = list(profile_folder.glob('*'))
                print(f"  Keeping folder {profile_folder} with {len(files)} files for inspection")
            return False
        
        # Success! Folder already has the correct name
        print(f"\n✓ Successfully completed: @{username}")
        print(f"  Folder: {profile_folder}")
        print(f"  Images saved: {downloaded}")
        return True
        
    except KeyboardInterrupt:
        print("\n\nINTERRUPTED by user.")
        print(f"  Folder {profile_folder} may be incomplete - keeping for inspection.")
        return False
        
    except Exception as e:
        print(f"  ERROR: Fatal error for @{username}: {e}")
        import traceback
        traceback.print_exc()
        # Keep folder on fatal error (don't delete - user may want to inspect)
        if profile_folder.exists():
            try:
                files = list(profile_folder.glob('*'))
                print(f"  Keeping {profile_folder} with {len(files)} files")
            except:
                pass
        return False


async def scrape_all_profiles(usernames, base_dir, delay_between_profiles=20, delay_between_requests=3, html_file=None):
    """Scrape all profiles using a single logged-in browser session."""
    session_storage_path = Path.home() / '.instagram_session.json'
    base_path = Path(base_dir)
    
    # Check for existing folders and remove completed profiles from bookmarks
    profiles_to_process = []
    skipped_count = 0
    
    print("\nChecking for already-completed profiles...")
    for username in usernames:
        safe_username = sanitize_folder_name(username)
        profile_folder = base_path / safe_username
        
        if profile_folder.exists():
            # Check if folder has images - skip if it has any images (only process empty folders)
            existing_files = list(profile_folder.glob('*.jpg')) + list(profile_folder.glob('*.png')) + list(profile_folder.glob('*.webp'))
            if existing_files and len(existing_files) > 0:
                print(f"  ✓ @{username} folder exists with {len(existing_files)} images - skipping (only processing empty folders)")
                skipped_count += 1
                # Remove from bookmarks if HTML file provided
                if html_file:
                    remove_username_from_bookmarks(html_file, username)
                continue
            else:
                print(f"  → @{username} folder exists but is empty - will process")
        
        profiles_to_process.append(username)
    
    if skipped_count > 0:
        print(f"\nSkipped {skipped_count} already-complete profiles")
        print(f"Processing {len(profiles_to_process)} remaining profiles\n")
    
    if not profiles_to_process:
        print("All profiles are already complete!")
        return len(usernames), 0, []  # All successful (skipped), none failed
    
    async with async_playwright() as p:
        # Create or load logged-in context
        browser, context, needs_login = await create_logged_in_context(p, str(session_storage_path))
        
        if needs_login:
            # Login once
            logged_in = await login_to_instagram(context, str(session_storage_path))
            if not logged_in:
                await browser.close()
                return skipped_count, len(profiles_to_process), profiles_to_process  # successful (skipped), failed, failed_profiles
            
            # Keep browser visible for monitoring (helper instance)
            print("\n  ✓ Browser will stay visible for monitoring (helper instance)")
        
        # Now scrape all profiles using the same logged-in context
        successful = skipped_count  # Start with skipped count
        failed = 0
        failed_profiles = []
        
        for i, username in enumerate(profiles_to_process, 1):
            import sys
            sys.stdout.flush()
            print(f"\n{'#'*60}")
            print(f"Progress: {i}/{len(profiles_to_process)} (Total: {i + skipped_count}/{len(usernames)})")
            print(f"Processing: @{username}")
            print(f"{'#'*60}")
            sys.stdout.flush()
            
            success = await scrape_instagram_profile_async(
                username, 
                base_dir, 
                context,  # Reuse the same logged-in context
                delay_between_requests=delay_between_requests
            )
            
            if success:
                successful += 1
            else:
                failed += 1
                failed_profiles.append(username)
            
            # Clean up memory between profiles
            gc.collect()
            
            # Check memory before continuing (only extend delay if really high)
            memory_percent = get_memory_usage()
            if memory_percent > 85:  # Only extend delay if critically high
                print(f"\n⚠️  High memory usage ({memory_percent:.1f}%) - extending delay...")
                await asyncio.sleep(delay_between_profiles * 2)  # Double the delay
            else:
                # Wait before processing next profile (except for the last one)
                if i < len(profiles_to_process):
                    print(f"\nWaiting {delay_between_profiles} seconds before next profile...")
                    await asyncio.sleep(delay_between_profiles)
        
        await browser.close()
        
        return successful, failed, failed_profiles


def scrape_instagram_profile(username, base_dir, delay_between_requests=5):
    """Wrapper for single profile (legacy, but now requires context - use scrape_all_profiles instead)."""
    # This is deprecated - use scrape_all_profiles for the main workflow
    pass


def main():
    """Main function to orchestrate scraping."""
    # Configuration
    HTML_FILE = "/Users/quarki3/Desktop/bookmarks_1_10_26.html"
    EXTERNAL_DRIVE = "/Volumes/TOSHIBA EXT"
    INSTAGRAM_DIR = os.path.join(EXTERNAL_DRIVE, "Instagram")
    DELAY_BETWEEN_PROFILES = 10  # Reduced from 30 - faster processing between profiles
    DELAY_BETWEEN_REQUESTS = 0.05   # Reduced for faster downloads (memory management handles resource limits)
    
    # Check if external drive is mounted
    if not os.path.exists(EXTERNAL_DRIVE):
        print(f"ERROR: External drive not found at {EXTERNAL_DRIVE}")
        print("Please ensure 'TOSHIBA EXT' is mounted.")
        sys.exit(1)
    
    # Check if HTML file exists
    if not os.path.exists(HTML_FILE):
        print(f"ERROR: HTML file not found at {HTML_FILE}")
        sys.exit(1)
    
    # Extract Instagram usernames
    print("Extracting Instagram usernames from bookmarks file...")
    usernames = extract_instagram_usernames(HTML_FILE)
    
    if not usernames:
        print("ERROR: No Instagram usernames found in the HTML file.")
        sys.exit(1)
    
    print(f"\nFound {len(usernames)} unique Instagram profiles:")
    for i, username in enumerate(usernames[:10], 1):
        print(f"  {i}. @{username}")
    if len(usernames) > 10:
        print(f"  ... and {len(usernames) - 10} more")
    
    # Ask for confirmation
    print(f"\nWill process {len(usernames)} profiles")
    print(f"Save location: {INSTAGRAM_DIR}")
    print(f"Delay between profiles: {DELAY_BETWEEN_PROFILES} seconds")
    print(f"Delay between requests: {DELAY_BETWEEN_REQUESTS} seconds")
    print("\n⚠️  Using browser automation (Playwright) - this will be slower but more reliable.")
    print("Starting in 5 seconds... (Press Ctrl+C to cancel)")
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    
    # Process all profiles using a single logged-in session
    print("\nStarting scraping with persistent login session...")
    print("You will need to log in ONCE at the beginning, then all profiles will use that session.")
    
    try:
        successful, failed, failed_profiles = asyncio.run(
            scrape_all_profiles(
                usernames,
                INSTAGRAM_DIR,
                delay_between_profiles=DELAY_BETWEEN_PROFILES,
                delay_between_requests=DELAY_BETWEEN_REQUESTS,
                html_file=HTML_FILE
            )
        )
    except KeyboardInterrupt:
        print("\n\nScraping interrupted by user.")
        successful = 0
        failed = len(usernames)
        failed_profiles = usernames
    
    # Print summary
    print(f"\n{'='*60}")
    print("SCRAPING COMPLETE")
    print(f"{'='*60}")
    print(f"Successful: {successful}/{len(usernames)}")
    print(f"Failed: {failed}/{len(usernames)}")
    
    if failed_profiles:
        print(f"\nFailed profiles:")
        for profile in failed_profiles[:20]:  # Show first 20
            print(f"  - @{profile}")
        if len(failed_profiles) > 20:
            print(f"  ... and {len(failed_profiles) - 20} more")
    
    print(f"\nAll files saved to: {INSTAGRAM_DIR}")
    print(f"Session saved for future use (won't need to log in again next time)")


if __name__ == "__main__":
    main()
