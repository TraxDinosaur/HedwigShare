"""
Hedwig Share - Magical File Sharing Service
Originally created by Cole Gawin (https://github.com/chroline/lightning-share)
Python Flask conversion by TraxDinosaur (https://traxdinosaur.github.io)
"""

from flask import Flask, request, render_template, jsonify, redirect, url_for, send_file, flash, session
import os
import secrets
import requests
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
import json
import sqlite3
from functools import wraps
import tempfile
import shutil

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()

    # Files table
    c.execute('''CREATE TABLE IF NOT EXISTS files
                 (word_code TEXT PRIMARY KEY,
                  filename TEXT NOT NULL,
                  original_filename TEXT NOT NULL,
                  filetype TEXT,
                  upload_date TIMESTAMP,
                  owner_id TEXT,
                  file_size INTEGER)''')

    # Users table (for session management)
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id TEXT PRIMARY KEY,
                  files TEXT)''')  # JSON array of word codes

    conn.commit()
    conn.close()

# Generate word code using words-aas API
def generate_word_code():
    try:
        response = requests.get("https://words-aas.vercel.app/api/a $adjective $noun")
        if response.status_code == 200:
            data = response.json()
            phrase = data.get('phrase', '').replace(' ', '-')

            # Check if word code already exists
            conn = sqlite3.connect('lightning_share.db')
            c = conn.cursor()
            c.execute("SELECT word_code FROM files WHERE word_code = ?", (phrase,))
            if c.fetchone():
                conn.close()
                return generate_word_code()  # Recursive call if exists
            conn.close()
            return phrase
        else:
            # Fallback word code generation
            return f"file-{secrets.token_hex(4)}"
    except:
        return f"file-{secrets.token_hex(4)}"

# Get or create user session
def get_user_id():
    if 'user_id' not in session:
        session['user_id'] = f"user-{secrets.token_hex(8)}"

        # Create user record
        conn = sqlite3.connect('lightning_share.db')
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id, files) VALUES (?, ?)", 
                  (session['user_id'], '[]'))
        conn.commit()
        conn.close()

    return session['user_id']

# Add file to user's list
def add_file_to_user(user_id, word_code):
    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()
    c.execute("SELECT files FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()

    if result:
        files = json.loads(result[0])
        if word_code not in files:
            files.append(word_code)
            c.execute("UPDATE users SET files = ? WHERE user_id = ?", 
                     (json.dumps(files), user_id))

    conn.commit()
    conn.close()

# Remove file from user's list
def remove_file_from_user(user_id, word_code):
    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()
    c.execute("SELECT files FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()

    if result:
        files = json.loads(result[0])
        if word_code in files:
            files.remove(word_code)
            c.execute("UPDATE users SET files = ? WHERE user_id = ?", 
                     (json.dumps(files), user_id))

    conn.commit()
    conn.close()

# Get user's files
def get_user_files(user_id):
    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()
    c.execute("SELECT files FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()

    if result:
        file_codes = json.loads(result[0])
        files = []

        conn = sqlite3.connect('lightning_share.db')
        c = conn.cursor()
        for code in file_codes:
            c.execute("SELECT * FROM files WHERE word_code = ?", (code,))
            file_data = c.fetchone()
            if file_data:
                files.append({
                    'word_code': file_data[0],
                    'filename': file_data[1],
                    'original_filename': file_data[2],
                    'filetype': file_data[3],
                    'upload_date': file_data[4],
                    'owner_id': file_data[5],
                    'file_size': file_data[6]
                })
        conn.close()
        return files

    return []

@app.route('/')
def index():
    user_id = get_user_id()
    user_files = get_user_files(user_id)
    return render_template('index.html', user_files=user_files)

@app.route('/upload', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected', 'error')
            return redirect(url_for('index'))

        file = request.files['file']
        if file.filename == '':
            flash('No file selected', 'error')
            return redirect(url_for('index'))

        if file:
            try:
                # Generate word code and secure filename
                word_code = generate_word_code()
                original_filename = file.filename
                filename = f"{word_code}_{secure_filename(original_filename)}"
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

                # Save file
                file.save(file_path)
                file_size = os.path.getsize(file_path)

                # Save to database
                conn = sqlite3.connect('lightning_share.db')
                c = conn.cursor()
                c.execute("""INSERT INTO files 
                           (word_code, filename, original_filename, filetype, upload_date, owner_id, file_size)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (word_code, filename, original_filename, file.content_type,
                          datetime.now(), get_user_id(), file_size))
                conn.commit()
                conn.close()

                # Add to user's files
                add_file_to_user(get_user_id(), word_code)

                flash(f'File uploaded successfully! Share code: {word_code}', 'success')
                return redirect(url_for('file_info', word_code=word_code))

            except RequestEntityTooLarge:
                flash('File too large. Maximum size is 100MB.', 'error')
            except Exception as e:
                flash(f'Error uploading file: {str(e)}', 'error')
                return redirect(url_for('index'))

    return render_template('upload.html')

@app.route('/download/<word_code>')
def download_file(word_code):
    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE word_code = ?", (word_code,))
    file_data = c.fetchone()
    conn.close()

    if not file_data:
        flash('File not found', 'error')
        return redirect(url_for('index'))

    filename = file_data[1]
    original_filename = file_data[2]
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    if not os.path.exists(file_path):
        flash('File not found on server', 'error')
        return redirect(url_for('index'))

    return send_file(file_path, as_attachment=True, download_name=original_filename)

@app.route('/file/<word_code>')
def file_info(word_code):
    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE word_code = ?", (word_code,))
    file_data = c.fetchone()
    conn.close()

    if not file_data:
        flash('File not found', 'error')
        return redirect(url_for('index'))

    file_info = {
        'word_code': file_data[0],
        'filename': file_data[1],
        'original_filename': file_data[2],
        'filetype': file_data[3],
        'upload_date': file_data[4],
        'owner_id': file_data[5],
        'file_size': file_data[6]
    }

    is_owner = file_info['owner_id'] == get_user_id()

    return render_template('file_info.html', file=file_info, is_owner=is_owner)

@app.route('/delete/<word_code>', methods=['POST'])
def delete_file(word_code):
    user_id = get_user_id()

    conn = sqlite3.connect('lightning_share.db')
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE word_code = ? AND owner_id = ?", (word_code, user_id))
    file_data = c.fetchone()

    if not file_data:
        flash('File not found or you do not have permission to delete it', 'error')
        return redirect(url_for('index'))

    filename = file_data[1]
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)

    # Delete file from filesystem
    if os.path.exists(file_path):
        os.remove(file_path)

    # Delete from database
    c.execute("DELETE FROM files WHERE word_code = ?", (word_code,))
    conn.commit()
    conn.close()

    # Remove from user's files
    remove_file_from_user(user_id, word_code)

    flash('File deleted successfully', 'success')
    return redirect(url_for('index'))

@app.route('/search', methods=['GET', 'POST'])
def search_file():
    if request.method == 'POST':
        word_code = request.form.get('word_code', '').strip()
        if word_code:
            conn = sqlite3.connect('lightning_share.db')
            c = conn.cursor()
            c.execute("SELECT word_code FROM files WHERE word_code = ?", (word_code,))
            if c.fetchone():
                conn.close()
                return redirect(url_for('file_info', word_code=word_code))
            else:
                conn.close()
                flash('File not found', 'error')

    return render_template('search.html')

@app.errorhandler(413)
def too_large(e):
    flash('File too large. Maximum size is 100MB.', 'error')
    return redirect(url_for('index'))

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)