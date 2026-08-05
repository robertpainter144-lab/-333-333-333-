#!/usr/bin/env python3
"""
Complete Empty Instagram Folders
Simply downloads all images from profiles that have empty folders.
"""

import os
import asyncio
import gc
from pathlib import Path
import sys

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: playwright not installed. Please install it with:")
    print("  pip3 install playwright --break-system-packages --user")
    print("  python3 -m playwright install chromium")
    sys.exit(1)

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# Resource management constants
MAX_MEMORY_PERCENT = 85
MAX_SCROLL_ATTEMPTS = 200
MEMORY_CHECK_INTERVAL = 10
DOM_CLEANUP_INTERVAL = 15


def get_memory_usage():
    """Get current memory usage percentage."""
    if not PSUTIL_AVAILABLE:
        return 0
    try:
        system_memory = psutil.virtual_memory()
        return system_memory.percent
    except Exception:
        return 0


def check_memory_usage():
    """Check if memory usage is too high."""
    if not PSUTIL_AVAILABLE:
        return False
    memory_percent = get_memory_usage()
    if memory_percent > MAX_MEMORY_PERCENT:
        print(f"  ⚠️  High memory usage detected: {memory_percent:.1f}%")
        return True
    return False


def sanitize_folder_name(username):
    """Create a safe folder name from username."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        username = username.replace(char, '_')
    return username


async def extract_all_images_from_page(page, username):
    """Extract ALL image URLs from Instagram profile page."""
    image_urls = set()
    
    try:
        print(f"  Loading profile page for @{username}...")
        profile_url = f"https://www.instagram.com/{username}/"
        
        try:
            await page.goto(profile_url, wait_until='domcontentloaded', timeout=30000)
            print(f"  ✓ Page loaded")
        except Exception as e:
            print(f"  ⚠️  Page load timeout/error: {e}, continuing anyway...")
        
        await page.wait_for_timeout(3000)
        
        # Check if profile exists or is private
        page_content = await page.content()
        if 'Sorry, this page' in page_content or 'Page Not Found' in page_content:
            print(f"  ERROR: Profile @{username} does not exist")
            return image_urls
        
        if 'This Account is Private' in page_content or 'This account is private' in page_content:
            print(f"  WARNING: Profile @{username} is private")
            return image_urls
        
        # Helper function to extract images from currently loaded DOM
        async def extract_images_now():
            return await page.evaluate("""
                () => {
                    const images = new Set();
                    // Only look for images inside article containers (actual posts)
                    const articles = document.querySelectorAll('article');
                    articles.forEach(article => {
                        // Get ALL images from each post (for carousel posts with multiple images)
                        const imgs = article.querySelectorAll('img');
                        imgs.forEach(img => {
                            let src = img.src || img.getAttribute('srcset') || img.getAttribute('data-src') || img.getAttribute('srcSet');
                            if (src) {
                                // Handle srcset (multiple sizes)
                                if (src.includes(',')) {
                                    const parts = src.split(',');
                                    src = parts[parts.length - 1].trim().split(' ')[0];
                                }
                                // Normalize URL - remove query parameters and fragments to avoid duplicates
                                src = src.split('?')[0].split('#')[0];
                                
                                // Only include actual post images
                                if (src && (src.includes('cdninstagram.com') || src.includes('fbcdn.net'))) {
                                    if ((src.includes('scontent') || src.includes('/v/t51.')) && 
                                        !src.includes('/t51.2885-19/') && 
                                        !src.includes('icon') && 
                                        !src.includes('static') && 
                                        !src.includes('logo') &&
                                        !src.includes('avatar') &&
                                        !src.includes('profile') &&
                                        !src.includes('thumbnail')) {
                                        if (src.includes('/s') || src.includes('/v/t51.')) {
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
        
        # Scroll and extract all images
        print(f"  Scrolling and extracting all images (max {MAX_SCROLL_ATTEMPTS} scrolls)...")
        scroll_count = 0
        last_height = 0
        stable_count = 0
        
        # Initial extraction
        initial_batch = await extract_images_now()
        image_urls.update(initial_batch)
        print(f"    Initial load: {len(initial_batch)} images")
        
        for scroll_attempt in range(MAX_SCROLL_ATTEMPTS):
            try:
                # Memory check
                if scroll_attempt > 0 and scroll_attempt % MEMORY_CHECK_INTERVAL == 0:
                    if check_memory_usage():
                        gc.collect()
                        try:
                            await page.evaluate("""
                                () => {
                                    const articles = document.querySelectorAll('article');
                                    const viewportHeight = window.innerHeight;
                                    articles.forEach(article => {
                                        const rect = article.getBoundingClientRect();
                                        if (rect.top < -viewportHeight * 10) {
                                            article.remove();
                                        }
                                    });
                                }
                            """)
                        except Exception:
                            pass
                        await asyncio.sleep(1)
                
                # DOM cleanup
                if scroll_attempt > 0 and scroll_attempt % DOM_CLEANUP_INTERVAL == 0:
                    try:
                        await page.evaluate("""
                            () => {
                                const images = document.querySelectorAll('img');
                                const viewportHeight = window.innerHeight;
                                images.forEach(img => {
                                    const rect = img.getBoundingClientRect();
                                    if (rect.top < -viewportHeight * 5) {
                                        const parent = img.closest('article, div');
                                        if (parent && parent.querySelectorAll('img').length > 1) {
                                            img.remove();
                                        }
                                    }
                                });
                            }
                        """)
                        gc.collect()
                    except Exception:
                        pass
                
                # Scroll
                current_height = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)
                await page.evaluate("window.scrollBy(0, 500)")
                await page.wait_for_timeout(500)
                
                # Extract new images
                new_batch = await extract_images_now()
                new_height = await page.evaluate("document.body.scrollHeight")
                
                # Filter new images - normalize URLs to avoid duplicates
                new_urls = []
                for url in new_batch:
                    if url and isinstance(url, str):
                        normalized_url = url.split('?')[0].split('#')[0]
                        if normalized_url not in image_urls:
                            if '/t51.2885-19/' not in normalized_url and 'profile' not in normalized_url.lower() and 'icon' not in normalized_url.lower() and 'static' not in normalized_url.lower() and 'logo' not in normalized_url.lower() and 'avatar' not in normalized_url.lower():
                                if 'scontent' in normalized_url or 'fbcdn.net' in normalized_url:
                                    new_urls.append(normalized_url)
                
                before_count = len(image_urls)
                image_urls.update(new_urls)
                after_count = len(image_urls)
                newly_found = after_count - before_count
                
                # Check if done
                if new_height == last_height and newly_found == 0:
                    stable_count += 1
                    if stable_count >= 3:
                        print(f"    ✓ Reached end of page ({len(image_urls)} total images)")
                        break
                else:
                    stable_count = 0
                    if scroll_attempt % 10 == 0:
                        memory_percent = get_memory_usage()
                        print(f"    Scroll {scroll_attempt + 1}: {len(image_urls)} images found... (Memory: {memory_percent:.1f}%)")
                
                last_height = new_height
                scroll_count += 1
                
            except Exception as e:
                print(f"    ⚠️  Scroll error: {e}, continuing...")
                await page.wait_for_timeout(2000)
        
        # Final extraction pass
        print(f"  Performing final extraction pass...")
        final_batch = await extract_images_now()
        image_urls.update(final_batch)
        
        print(f"  Found {len(image_urls)} total unique image URLs from Instagram")
        
    except Exception as e:
        print(f"  ERROR: Error extracting images: {e}")
        import traceback
        traceback.print_exc()
    
    return image_urls


async def download_images(context, image_urls, profile_folder):
    """Download all images to the profile folder."""
    if not image_urls:
        return 0
    
    print(f"  Downloading {len(image_urls)} images...")
    download_page = await context.new_page()
    downloaded = 0
    failed = 0
    image_counter = 1
    
    for img_url in image_urls:
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
                
                if downloaded % 10 == 0:
                    print(f"    Downloaded {downloaded}/{len(image_urls)} images...")
            else:
                failed += 1
        except Exception as e:
            failed += 1
            if failed <= 3:
                print(f"    ✗ Failed to download image: {str(e)[:60]}")
        
        await asyncio.sleep(0.05)
    
    await download_page.close()
    return downloaded


async def complete_profile(username, base_dir, context):
    """Complete a single profile folder."""
    print(f"\n{'='*60}")
    print(f"Completing: @{username}")
    print(f"{'='*60}")
    
    base_path = Path(base_dir)
    safe_username = sanitize_folder_name(username)
    profile_folder = base_path / safe_username
    
    if not profile_folder.exists():
        profile_folder.mkdir(parents=True, exist_ok=True)
        print(f"  Created folder: {profile_folder}")
    
    # Extract all images from Instagram
    page = await context.new_page()
    try:
        image_urls = await extract_all_images_from_page(page, username)
    finally:
        await page.close()
    
    if not image_urls:
        print(f"  ⚠️  No images found on Instagram (may be private or empty)")
        return 0
    
    # Download all images
    downloaded = await download_images(context, list(image_urls), profile_folder)
    print(f"  ✓ Completed: {downloaded} images downloaded")
    return downloaded


async def login_to_instagram(context, session_storage_path=None):
    """Login to Instagram manually and save session."""
    page = await context.new_page()
    print("\n" + "="*60)
    print("LOGIN REQUIRED")
    print("="*60)
    print("A browser window will open. Please log in to Instagram manually.")
    print("After logging in, press ENTER in this terminal to continue...")
    print("="*60)
    
    try:
        print("\nOpening Instagram login page...")
        await page.goto('https://www.instagram.com/accounts/login/', timeout=60000, wait_until='domcontentloaded')
        await page.wait_for_timeout(5000)
        
        print("\n✓ Browser window is open!")
        print("  Please log in to Instagram in the browser window.")
        print("  When you're logged in, come back here and press ENTER...")
        
        try:
            user_input = input("\n>>> Press ENTER after you have logged in to Instagram: ")
        except (EOFError, KeyboardInterrupt):
            print("\nLogin cancelled.")
            await page.close()
            return False
        
        await page.wait_for_timeout(2000)
        current_url = page.url
        
        if 'accounts/login' in current_url or '/login' in current_url:
            print("\n⚠️  WARNING: It looks like you might not be logged in.")
            retry = input("   Try again? (y/n): ").strip().lower()
            if retry == 'y':
                await page.close()
                return await login_to_instagram(context, session_storage_path)
            else:
                await page.close()
                return False
        
        print("✓ Login appears successful!")
        
        # Save session state
        if session_storage_path:
            try:
                await context.storage_state(path=session_storage_path)
                print(f"✓ Session saved to {session_storage_path}")
            except Exception as e:
                print(f"⚠️  Warning: Could not save session: {e}")
        
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


async def complete_all_empty_folders(base_dir):
    """Complete all empty folders."""
    session_storage_path = Path.home() / '.instagram_session.json'
    base_path = Path(base_dir)
    
    # Find all empty folders
    empty_folders = []
    all_folders = [d for d in base_path.iterdir() if d.is_dir()]
    
    for folder in all_folders:
        existing_files = list(folder.glob('*.jpg')) + list(folder.glob('*.png')) + list(folder.glob('*.webp'))
        if len(existing_files) == 0:
            empty_folders.append(folder.name)
    
    if not empty_folders:
        print("No empty folders found!")
        return
    
    print(f"\nFound {len(empty_folders)} empty folders to complete")
    print(f"Base directory: {base_dir}\n")
    
    async with async_playwright() as p:
        # Create browser - show window for login if needed
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=0,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-extensions',
                '--disable-plugins',
            ]
        )
        
        needs_login = False
        
        # Load session if available
        if session_storage_path.exists():
            try:
                context = await browser.new_context(
                    storage_state=str(session_storage_path),
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='en-US',
                    timezone_id='America/New_York',
                )
                # Test if session is still valid
                page = await context.new_page()
                try:
                    await page.goto('https://www.instagram.com/', timeout=30000, wait_until='domcontentloaded')
                    await page.wait_for_timeout(3000)
                    current_url = page.url
                    await page.close()
                    
                    if 'accounts/login' in current_url or 'login' in current_url:
                        print("  Session expired, will need to log in again")
                        await context.close()
                        needs_login = True
                    else:
                        print("✓ Using existing logged-in session")
                        # Close visible browser and reopen in headless mode
                        await browser.close()
                        browser = await p.chromium.launch(
                            headless=True,
                            slow_mo=0,
                            args=[
                                '--disable-blink-features=AutomationControlled',
                                '--disable-dev-shm-usage',
                                '--disable-gpu',
                                '--no-sandbox',
                                '--disable-setuid-sandbox',
                                '--disable-extensions',
                                '--disable-plugins',
                            ]
                        )
                        context = await browser.new_context(
                            storage_state=str(session_storage_path),
                            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                            viewport={'width': 1920, 'height': 1080},
                            locale='en-US',
                            timezone_id='America/New_York',
                        )
                        print("  ✓ Browser now running in headless mode")
                except Exception as e:
                    print(f"  Error checking session: {e}, will need to log in")
                    await page.close()
                    await context.close()
                    needs_login = True
            except Exception as e:
                print(f"  Could not load session: {e}")
                needs_login = True
        else:
            print("⚠️  No saved session found")
            needs_login = True
        
        # Login if needed
        if needs_login:
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
            )
            logged_in = await login_to_instagram(context, str(session_storage_path))
            if not logged_in:
                await browser.close()
                print("ERROR: Login failed. Cannot continue.")
                return
            
            # After login, close visible browser and reopen in headless mode
            print("\n  Closing browser window and switching to headless mode...")
            await browser.close()
            browser = await p.chromium.launch(
                headless=True,
                slow_mo=0,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-extensions',
                    '--disable-plugins',
                ]
            )
            context = await browser.new_context(
                storage_state=str(session_storage_path),
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                timezone_id='America/New_York',
            )
            print("  ✓ Browser now running in headless mode")
        
        # Complete each empty folder
        total_downloaded = 0
        completed = 0
        
        for i, username in enumerate(empty_folders, 1):
            print(f"\n{'#'*60}")
            print(f"Progress: {i}/{len(empty_folders)}")
            print(f"{'#'*60}")
            
            downloaded = await complete_profile(username, base_dir, context)
            total_downloaded += downloaded
            if downloaded > 0:
                completed += 1
            
            # Cleanup between profiles
            gc.collect()
            
            # Delay between profiles
            if i < len(empty_folders):
                memory_percent = get_memory_usage()
                if memory_percent > 85:
                    delay = 30
                else:
                    delay = 10
                print(f"\nWaiting {delay} seconds before next profile...")
                await asyncio.sleep(delay)
        
        await browser.close()
        
        # Summary
        print(f"\n{'='*60}")
        print("COMPLETION FINISHED")
        print(f"{'='*60}")
        print(f"Folders completed: {completed}/{len(empty_folders)}")
        print(f"Total images downloaded: {total_downloaded}")
        print(f"\nAll files saved to: {base_dir}")


def main():
    """Main function."""
    EXTERNAL_DRIVE = "/Volumes/TOSHIBA EXT"
    INSTAGRAM_DIR = os.path.join(EXTERNAL_DRIVE, "Instagram")
    
    if not os.path.exists(EXTERNAL_DRIVE):
        print(f"ERROR: External drive not found at {EXTERNAL_DRIVE}")
        sys.exit(1)
    
    print("="*60)
    print("COMPLETE EMPTY INSTAGRAM FOLDERS")
    print("="*60)
    print("\nThis script will:")
    print("  1. Find all empty folders")
    print("  2. Download all images from each profile")
    print("  3. Save images to complete each folder")
    print("\nStarting in 3 seconds... (Press Ctrl+C to cancel)")
    try:
        import time
        time.sleep(3)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)
    
    try:
        asyncio.run(complete_all_empty_folders(INSTAGRAM_DIR))
    except KeyboardInterrupt:
        print("\n\nCompletion interrupted by user.")


if __name__ == "__main__":
    main()
