from flask import Flask, render_template, request, send_file, redirect, url_for, flash
import logging
import tempfile
import os

from .youtube_utils import search_youtube, format_duration 
from .excel_utils import save_to_excel

app = Flask(__name__)
app.secret_key = os.urandom(24) # Needed for flash messages

# Configure basic logging for the app as well
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global variable to store the latest video search results for Excel export
latest_videos_data = []

@app.route('/')
def index():
    # Clear previous search results/errors if any, by not passing them
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search_results():
    global latest_videos_data # Declare intent to modify global variable
    if request.method == 'POST':
        keywords_str = request.form.get('keywords', '')
        try:
            max_results = int(request.form.get('max_results', 20))
        except ValueError:
            max_results = 20
        
        if not keywords_str.strip():
            flash("Please enter at least one keyword.", "error")
            return render_template('index.html', keywords=keywords_str, max_results=max_results)

        keywords_list = [k.strip() for k in keywords_str.split(",") if k.strip()]
        if not keywords_list:
            flash("Please enter valid keywords.", "error")
            return render_template('index.html', keywords=keywords_str, max_results=max_results)

        max_results = max(5, min(max_results, 20))
        
        app.logger.info(f"Search initiated for keywords: '{keywords_str}' with max_results: {max_results}")

        try:
            videos = search_youtube(keywords_list, max_results=max_results)
            latest_videos_data = videos # Store results for potential Excel export
            
            if not videos:
                app.logger.info(f"No videos found for keywords: {keywords_str}")
                return render_template('results.html', 
                                       message="No videos found for your query. Try different keywords.", 
                                       keywords=keywords_str, 
                                       max_results=max_results, 
                                       videos=[])
            
            app.logger.info(f"Found {len(videos)} videos for keywords: {keywords_str}")
            return render_template('results.html', 
                                   videos=videos, 
                                   keywords=keywords_str, 
                                   max_results=max_results, 
                                   format_duration=format_duration)
        except Exception as e:
            app.logger.error(f"An error occurred during search for keywords '{keywords_str}': {str(e)}", exc_info=True)
            flash(f"An unexpected error occurred during the search. Please try again later.", "error")
            latest_videos_data = [] # Clear data on error
            return render_template('index.html', 
                                   keywords=keywords_str,
                                   max_results=max_results)

@app.route('/download_excel')
def download_excel_report():
    global latest_videos_data
    if not latest_videos_data:
        flash("No data available to download. Please perform a search first.", "warning")
        return redirect(url_for('index'))

    temp_excel_file_path = None # Initialize to None
    try:
        # Create a temporary file to save the Excel output
        # delete=False is important because send_file needs to access it after this block
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmpfile:
            temp_excel_file_path = tmpfile.name
        
        # Call save_to_excel with the global data, temp file path, and format_duration function
        # save_to_excel from excel_utils.py will handle the actual Excel creation
        excel_file_path_returned = save_to_excel(latest_videos_data, temp_excel_file_path, format_duration_func=format_duration)

        if excel_file_path_returned:
            app.logger.info(f"Excel file generated at {excel_file_path_returned}, sending to user.")
            return send_file(
                excel_file_path_returned,
                as_attachment=True,
                download_name='trending_youtube_videos.xlsx',
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        else:
            app.logger.error("Error generating Excel file: save_to_excel returned None.")
            flash("Error generating Excel file.", "error")
            return redirect(url_for('search_results')) # Or back to index, or wherever appropriate
    except Exception as e:
        app.logger.error(f"An error occurred during Excel download: {str(e)}", exc_info=True)
        flash(f"An error occurred while preparing the Excel file: {str(e)}", "error")
        return redirect(url_for('index')) # Redirect to home on error
    finally:
        # Clean up the temporary file from the server
        if temp_excel_file_path and os.path.exists(temp_excel_file_path):
            try:
                os.remove(temp_excel_file_path)
                app.logger.info(f"Temporary Excel file {temp_excel_file_path} deleted.")
            except Exception as e_remove:
                app.logger.error(f"Error deleting temporary Excel file {temp_excel_file_path}: {e_remove}")

if __name__ == '__main__':
    app.run(debug=True)
