import os
import requests
from youtubesearchpython import VideosSearch
import yt_dlp
import time
from PIL import Image, UnidentifiedImageError
from datetime import datetime, timedelta
import concurrent.futures
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def format_duration(seconds):
    """Formats duration from seconds to HH:MM:SS or MM:SS."""
    if seconds is None:
        return "N/A"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02}:{minutes:02}:{secs:02}"
    else:
        return f"{minutes:02}:{secs:02}"

def download_image(url, base_save_dir, filename):
    """Downloads an image, saves it, and returns its relative path for static serving."""
    if not os.path.exists(base_save_dir):
        os.makedirs(base_save_dir)
    
    save_path = os.path.join(base_save_dir, filename)
    # Path for url_for should be relative to the 'static' folder.
    # Assuming base_save_dir is 'flask_youtube_app/static/thumbnails',
    # then relative_path will be 'thumbnails/filename.jpg'
    relative_path = os.path.join(os.path.basename(base_save_dir), filename)

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        img = Image.open(requests.get(url, stream=True).raw)
        
        # Verify image (basic check)
        if img.format.lower() not in ['jpeg', 'png', 'gif', 'webp']: # Added webp
            logging.warning(f"Downloaded image from {url} is not in a standard web format (JPEG, PNG, GIF, WEBP): format is {img.format}")
            # Still try to save it if it's a valid image format Pillow supports
            # but log a warning.

        # Ensure the image is in RGB mode for saving as JPEG, if it's not animated
        if img.format.lower() != 'gif': # Don't convert GIF to RGB if we want to preserve animation
             img = img.convert("RGB")
        
        img.save(save_path, "JPEG" if img.format.lower() != 'gif' else "GIF") # Save as JPEG or GIF
        logging.info(f"Image successfully downloaded and saved to {save_path}")
        return relative_path
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed for {url}: {e}")
    except UnidentifiedImageError:
        logging.error(f"Cannot identify image file from {url}. It might be corrupted or not an image.")
    except (ValueError, IOError) as e: # Broader exception for image processing/saving issues
        logging.error(f"Image processing or saving failed for {url}: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred while downloading image {url}: {e}")
    
    # Cleanup if save_path was created but failed
    if os.path.exists(save_path):
        try:
            os.remove(save_path)
        except OSError as e_remove:
            logging.error(f"Error trying to remove partially downloaded file {save_path}: {e_remove}")
            
    return None


def get_video_details(video_url):
    """Fetches detailed information for a single YouTube video using yt-dlp."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'format': 'best',
        'forcejson': True, # Not a standard yt-dlp option, but we parse the json output
        'dump_single_json': True, # Gets info as a single JSON line
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            
            # Basic check if info_dict is what we expect
            if not info_dict:
                logging.error(f"yt-dlp returned empty info for {video_url}")
                return None

            upload_date_str = info_dict.get('upload_date')
            upload_date_yt = info_dict.get('upload_date') # Format YYYYMMDD
            formatted_date = "N/A"
            upload_datetime_obj = None # This will be our datetime object for upload_timestamp

            if upload_date_yt:
                try:
                    upload_datetime_obj = datetime.strptime(upload_date_yt, "%Y%m%d")
                    formatted_date = upload_datetime_obj.strftime("%d/%m/%Y")
                except (ValueError, TypeError) as e:
                    logging.warning(f"Could not parse upload date '{upload_date_yt}' for {video_url}: {e}. Setting to N/A.")
                    # upload_datetime_obj remains None, formatted_date remains "N/A"
            
            views = info_dict.get('view_count', 0) or 0
            likes = info_dict.get('like_count', 0) or 0
            comments = info_dict.get('comment_count', 0) or 0
            
            # Ensure they are integers
            try:
                views = int(views)
            except (ValueError, TypeError):
                views = 0
            try:
                likes = int(likes)
            except (ValueError, TypeError):
                likes = 0
            try:
                comments = int(comments)
            except (ValueError, TypeError):
                comments = 0

            engagement_score = views + (likes * 2) + (comments * 3)

            return {
                "title": info_dict.get('title', "N/A"),
                "url": video_url, # Use the passed video_url directly
                "channel_name": info_dict.get('uploader', "N/A"),
                "views": views,
                "duration_seconds": info_dict.get('duration'), 
                "likes": likes,
                "comments": comments,
                "date": formatted_date, 
                "upload_timestamp": upload_datetime_obj, # datetime object or None
                "thumbnail": info_dict.get('thumbnail', "N/A"), # URL of the thumbnail
                "engagement_score": engagement_score
            }
    except yt_dlp.utils.DownloadError as e:
        # Specific yt-dlp download errors (e.g., video unavailable)
        logging.error(f"yt-dlp DownloadError for {video_url}: {e}")
        return None
    except Exception as e:
        # Catch any other exceptions during video detail fetching
        logging.error(f"Unexpected error fetching details for {video_url} with yt-dlp: {e}")
        return None

def is_within_last_24_hours(upload_timestamp):
    """Checks if the upload timestamp is within the last 24 hours."""
    if upload_timestamp is None:
        return False
    return (datetime.now() - datetime.fromtimestamp(upload_timestamp)) < timedelta(hours=24)

def search_youtube(keywords, max_results=20):
    """
    Searches YouTube for videos based on keywords and fetches their details.
    Downloads thumbnails to a static directory.
    """
    if isinstance(keywords, str): # Ensure keywords is a list
        keywords_list = [k.strip() for k in keywords.split(",") if k.strip()]
    elif isinstance(keywords, list):
        keywords_list = keywords
    else:
        logging.error("Keywords must be a string or a list of strings.")
        return []

    if not keywords_list:
        logging.warning("Search initiated with no keywords.")
        return []
        
    query = " OR ".join(f'"{k}"' for k in keywords_list) # Search for exact phrases or combine
    logging.info(f"Searching YouTube with query: {query}, max_results: {max_results}")

    videos_data = []
    try:
        # Perform the search
        search = VideosSearch(query, limit=max_results * 2) # Fetch more to filter later
        results = search.result()['result']
        
        if not results:
            logging.info(f"No direct search results for query: {query}")
            return []

    except Exception as e:
        logging.error(f"Error during YouTube search with youtubesearchpython: {e}")
        return [] # Return empty list on search failure

    # Prepare for fetching details and thumbnails
    # Ensure the static thumbnails directory exists
    # Corrected path to be relative to the application root for os.makedirs
    # but download_image will use it to construct static-relative paths for return
    base_app_dir = os.path.dirname(os.path.abspath(__file__)) # flask_youtube_app
    static_thumbnails_dir = os.path.join(base_app_dir, 'static', 'thumbnails')

    if not os.path.exists(static_thumbnails_dir):
        try:
            os.makedirs(static_thumbnails_dir)
            logging.info(f"Created directory: {static_thumbnails_dir}")
        except OSError as e:
            logging.error(f"Failed to create directory {static_thumbnails_dir}: {e}")
            # If directory creation fails, we might not be able to save thumbnails
            # but we can still proceed and try to return video data without them.

    detailed_videos = []
    # Fetch details for each video using yt-dlp (can be slow, consider concurrent execution)
    # For now, sequential for simplicity in this step
    for video_res in results:
        if len(detailed_videos) >= max_results:
            break
        video_url = video_res.get('link')
        if video_url:
            details = get_video_details(video_url)
            if details:
                detailed_videos.append(details)
            else:
                logging.warning(f"Could not fetch details for {video_url}")
        else:
            logging.warning(f"Search result missing 'link': {video_res.get('title', 'N/A')}")


    if not detailed_videos:
        logging.info(f"No video details could be fetched for the search query: {query}")
        return []
        
    # Sort by upload date (newest first) and then by view count
    # Sort by upload_timestamp (actual datetime objects, None will be handled by sort)
    # and then by engagement_score. Datetime objects can be compared directly.
    # To handle None for upload_timestamp (e.g. make them appear older), we can use a lambda
    # that returns a very old date for None, or filter them out/handle separately if needed.
    # For simplicity, Python's sort is stable with Nones if they are comparable or
    # if we provide a key that handles it.
    # Sorting by datetime object (upload_timestamp) descending, then engagement_score descending.
    # Using a large datetime for None in timestamp to push them to the end if sorting ascending,
    # or a very small (old) datetime if sorting descending for "newest first".
    # min_datetime can be datetime.min, but that might not be timezone-aware if others are.
    # Since we want newest first, None should be treated as "oldest".
    min_dummy_date = datetime.min
    detailed_videos.sort(key=lambda x: (x.get('upload_timestamp', min_dummy_date) or min_dummy_date, x.get('engagement_score', 0) or 0), reverse=True)
    top_videos = detailed_videos[:max_results]


    # Download thumbnails concurrently
    # The download_image function now expects base_save_dir and a unique filename
    # and returns a path relative to 'static/' for use in url_for
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_video_idx = {}
        for idx, video_detail in enumerate(top_videos):
            # Use 'thumbnail' (which now holds the URL) for downloading
            if video_detail.get('thumbnail') and video_detail['thumbnail'] != "N/A":
                original_thumbnail_url = video_detail['thumbnail'] # This is the URL
                video_id_part = video_detail['url'].split('v=')[-1].split('&')[0] # Basic way to get ID
                thumbnail_filename = f"thumbnail_{video_id_part}_{idx}.jpg"
                
                # Submit to executor
                future = executor.submit(download_image, original_thumbnail_url, static_thumbnails_dir, thumbnail_filename)
                future_to_video_idx[future] = idx
            else:
                # If no original URL, set the 'thumbnail' field (path) to None
                top_videos[idx]['thumbnail'] = None 
                logging.warning(f"Video '{video_detail['title']}' has no original thumbnail URL to download.")

        for future in concurrent.futures.as_completed(future_to_video_idx):
            idx = future_to_video_idx[future]
            try:
                relative_thumbnail_path = future.result() # This is 'thumbnails/filename.jpg' or None
                # Overwrite the 'thumbnail' field (which was the URL) with the local path
                top_videos[idx]['thumbnail'] = relative_thumbnail_path 
                if relative_thumbnail_path:
                    logging.info(f"Thumbnail for '{top_videos[idx]['title']}' processed. Path: {relative_thumbnail_path}")
                else:
                    logging.warning(f"Thumbnail download failed for '{top_videos[idx]['title']}'. Original URL was {top_videos[idx].get('thumbnail')}") # Log original URL if download failed
            except Exception as exc:
                logging.error(f"Thumbnail processing generated an exception for video '{top_videos[idx]['title']}': {exc}")
                top_videos[idx]['thumbnail'] = None # Ensure 'thumbnail' is None on error
    
    logging.info(f"Returning {len(top_videos)} videos after processing for query: {query}")
    return top_videos
