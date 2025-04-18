from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory
from docx import Document
from openai import AzureOpenAI
from dotenv import load_dotenv
import os
import time
from datetime import datetime, timedelta
import requests
import markdown
from bs4 import BeautifulSoup
from flask_session import Session
import json

load_dotenv()

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv('FLASK_SECRET_KEY')
app.config['UPLOAD_FOLDER'] = 'static/generated'

# Session configuration
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
Session(app)

# Add context processor to inject current year into all templates
@app.context_processor
def inject_year():
    return {'now': datetime.now()}

class Config:
    ARTICLES_DIR = "articles"
    GENERATED_DIR = "generated"
    os.makedirs(ARTICLES_DIR, exist_ok=True)
    os.makedirs(GENERATED_DIR, exist_ok=True)

class AzureServices:
    def __init__(self):
        self.text_client = AzureOpenAI(
            api_key=os.getenv("AZURE_OPENAI_KEY"),
            api_version="2024-02-15-preview",
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT")
        )
        
        self.image_client = AzureOpenAI(
            api_key=os.getenv("AZURE_DALLE_KEY"),
            api_version="2024-02-01",
            azure_endpoint=os.getenv("AZURE_DALLE_ENDPOINT")
        )

        self.conversations = {}

    def rewrite_content(self, original_text, tone, keywords, firm_name, location):
        response = self.text_client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[
                {"role": "system", "content": f"""
                    You are a legal blog post rewriter. There should be At least 30% changes from original. Rewrite the article following these strict guidelines:
                    
                    DO's:
                    1. Use active voice
                    2. Structure with 5 sections: introduction, 3 subheadings, and conclusion with call-to-action
                    3. Keep length between 1000-1200 words
                    4. Use transition sentences between sections
                    5. Conclusion should be brief (1-2 sentences) with clear call-to-action
                    6. Include 1-2 bulleted lists in the entire article
                    7. Balance paragraphs and lists appropriately
                    8. Use {tone} tone
                    9. Include these keywords naturally: {keywords}
                    10. Mention {firm_name} in {location} where relevant
                    
                    DON'Ts:
                    1. Avoid legal jargon or complex language (keep it high-school level)
                    2. No passive voice
                    3. Don't use lists without context
                    4. Limit metaphors
                    5. Don't make conclusion too long
                    6. Don't include more than 5 sources
                    7. Don't exceed 1200 words
                    8. Don't use more than 3 lists
                    
                    Formatting Requirements:
                    # Main Title
                    ## Subheading 1
                    ### Sub-subheading (if needed)
                    **Bold important terms**
                    - Bullet points when appropriate
                    [Link text](URL) for references
                    
                    The article must be valuable, engaging, and optimized for both readers and search engines.
                """},
                {"role": "user", "content": original_text}
            ],
            temperature=0.7,
        )
        return response.choices[0].message.content

    def generate_image(self, text_prompt):
        """
        Generate an image based on the text prompt using Azure DALL-E
        Args:
            text_prompt: Description of the desired image
        Returns:
            Filename of the generated image
        """
        try:
            safe_prompt = self._get_safe_image_prompt(text_prompt)
            
            response = self.image_client.images.generate(
                model=os.getenv("AZURE_DALLE_DEPLOYMENT"),
                prompt=safe_prompt,
                size="1024x1024",
                quality="standard",
                n=1,
            )
            image_url = response.data[0].url
            os.makedirs(os.path.join(app.static_folder, 'generated'), exist_ok=True)
            
            timestamp = int(time.time())
            image_filename = f"image_{timestamp}.png"
            image_path = os.path.join(app.static_folder, 'generated', image_filename)
            
            response = requests.get(image_url)
            with open(image_path, 'wb') as f:
                f.write(response.content)
            
            return image_filename
            
        except Exception as e:
            print(f"Image generation failed: {e}")
            return None
        
    def _get_safe_image_prompt(self, text_prompt):
        """
        Generate a content-filter-safe prompt for image generation
        Args:
            text_prompt: Original text prompt
        Returns:
            Safe, filtered prompt suitable for image generation
        """
        response = self.text_client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=[
                {"role": "system", "content": """
                    You are a prompt engineer for legal blog images. Create a safe, professional image prompt that:
                    - Uses only abstract legal concepts
                    - Avoids any faces, people, or sensitive content
                    - Focuses on documents, scales of justice, legal symbols
                    - Maintains a professional, corporate style
                    - Will pass Azure content filters
                    - Is based on this blog content:
                """},
                {"role": "user", "content": text_prompt[:1000]}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content

    def edit_content(self, session_id, user_message, current_content=None):
        if session_id not in self.conversations:
            self.conversations[session_id] = [
                {"role": "system", "content": """
                    You are a legal blog post editor. When the user requests changes:
                    1. Make ONLY the requested changes
                    2. Return the COMPLETE updated blog (not just updated part) in markdown format
                    3. Don't include any commentary or explanations
                    4. Preserve all formatting and structure
                """}
            ]
        
        if current_content:
            self.conversations[session_id].append(
                {"role": "assistant", "content": current_content}
            )
        
        self.conversations[session_id].append(
            {"role": "user", "content": user_message}
        )
        
        response = self.text_client.chat.completions.create(
            model=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
            messages=self.conversations[session_id],
            temperature=0.5
        )
        
        ai_response = response.choices[0].message.content
        self.conversations[session_id].append(
            {"role": "assistant", "content": ai_response}
        )
        
        return ai_response
    
class FileManager:
    @staticmethod
    def list_articles():
        """
        List all DOCX files in the articles directory
        Returns:
            List of article filenames
        """
        articles = [f for f in os.listdir(Config.ARTICLES_DIR) if f.endswith('.docx')]
        print(f"Found articles: {articles}")
        return articles
    
    @staticmethod
    def get_article_metadata():
        """
        Read and parse the metadata.json file
        Returns:
            Dictionary of article metadata
        """
        metadata_path = os.path.join(Config.ARTICLES_DIR, 'metadata.json')
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                content = f.read()
                print(f"Read metadata content: {content}")
                metadata = json.loads(content)
                # Convert list to dictionary for easier lookup
                result = {article['filename']: article for article in metadata['articles']}
                print(f"Processed metadata: {result}")
                return result
        except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
            print(f"Error reading metadata: {str(e)}")
            return {}
    
    @staticmethod
    def read_docx(filename):
        """
        Read content from a DOCX file
        Args:
            filename: Name of the DOCX file
        Returns:
            Extracted text content
        """
        doc = Document(os.path.join(Config.ARTICLES_DIR, filename))
        return "\n".join([para.text for para in doc.paragraphs])
    
    @staticmethod
    def save_content(content):
        """
        Save generated content to a file
        Args:
            content: Content to save
        Returns:
            Filename of the saved content
        """
        filename = f"blog_{int(time.time())}.txt"
        path = os.path.join(Config.GENERATED_DIR, filename)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        return filename

class UserSession:
    USERS = {
        "admin": {
            "email": "admin@lawfirm.com",
            "password": "password123", 
            "firm": "Legal Partners", 
            "location": "New York",
            "custom_tones": []
        },
        "memberhub": {
            "email": "memberhub@newlawbusinessmodel.com",
            "password": "memberhub123",
            "firm": "New Law Business Model",
            "location": "Global",
            "custom_tones": []
        }
    }
    
    @staticmethod
    def login(email, password):
        # Find user by email
        user_data = next((data for data in UserSession.USERS.values() if data['email'] == email), None)
        
        if user_data and user_data['password'] == password:
            # Find the username for this email
            username = next((k for k,v in UserSession.USERS.items() if v['email'] == email), None)
            
            session['user'] = {
                'username': username,
                'email': email,
                'firm': user_data['firm'],
                'location': user_data['location'],
                'custom_tones': user_data.get('custom_tones', [])
            }
            return True
        return False
    
    @staticmethod
    def add_custom_tone(username, tone_name, tone_description):
        if username in UserSession.USERS:
            # Initialize if not exists
            if 'custom_tones' not in UserSession.USERS[username]:
                UserSession.USERS[username]['custom_tones'] = []
            
            # Check if tone with same name exists (case insensitive)
            if not any(t['name'].lower() == tone_name.lower() 
                      for t in UserSession.USERS[username]['custom_tones']):
                UserSession.USERS[username]['custom_tones'].append({
                    'name': tone_name.strip(),
                    'description': tone_description.strip()
                })
                # Update session
                if 'user' in session and session['user']['username'] == username:
                    session['user']['custom_tones'] = UserSession.USERS[username]['custom_tones']
                    session.modified = True
                return True
        return False
    
    @staticmethod
    def get_custom_tones(username):
        if username in UserSession.USERS:
            return UserSession.USERS[username].get('custom_tones', [])
        return []
    
    @staticmethod
    def get_current_user():
        return session.get('user')

azure_services = AzureServices()

@app.template_filter('markdown')
def markdown_filter(text):
    html = markdown.markdown(text)
    soup = BeautifulSoup(html, 'html.parser')
    return str(soup)

@app.route('/')
def home():
    if not UserSession.get_current_user():
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if UserSession.login(request.form['email'], request.form['password']):
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Invalid credentials")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    user = UserSession.get_current_user()
    if not user:
        return redirect(url_for('login'))
    
    # Get articles and their metadata
    articles = FileManager.list_articles()
    metadata = FileManager.get_article_metadata()
    
    print(f"Dashboard - Articles: {articles}")
    print(f"Dashboard - Metadata: {metadata}")
    
    # Combine standard tones with user's custom tones
    standard_tones = [
        ('Professional', 'Formal and business-like tone suitable for corporate audiences'),
        # ('Conversational', 'Casual and engaging tone that feels like a friendly discussion'),
        # ('Authoritative', 'Strong and confident tone that establishes expertise'),
        ('Friendly', 'Warm and approachable tone that builds rapport with readers'),
        # ('Technical', 'Detailed and precise tone focused on accuracy and technical details')
        ('Educational', 'Clear and informative tone designed to explain concepts and enhance understanding')
    ]
    
    # Add custom tones if they exist
    custom_tones = UserSession.get_custom_tones(user['username'])
    all_tones = standard_tones + [(t['name'], t['description']) for t in custom_tones]
    # Convert to the format expected by the template
    tone_options = [t[0] for t in all_tones]
    tone_descriptions = {t[0]: t[1] for t in all_tones}
    
    return render_template('dashboard.html', 
                         username=user['username'],
                         articles=articles,
                         metadata=metadata,
                         tone_options=tone_options,
                         tone_descriptions=tone_descriptions)

@app.route('/add_tone', methods=['POST'])
def add_tone():
    user = UserSession.get_current_user()
    if not user:
        return redirect(url_for('login'))
    
    tone_name = request.form.get('tone_name', '').strip()
    tone_description = request.form.get('tone_description', '').strip()
    
    if not tone_name:
        return {'success': False, 'error': 'Tone name is required'}, 400
    
    if UserSession.add_custom_tone(user['username'], tone_name, tone_description):
        return {'success': True}
    
    return {'success': False, 'error': 'Tone with this name already exists'}, 400

@app.route('/select/<article>', methods=['GET', 'POST'])
def select_article(article):
    if request.method == 'POST':
        tone = request.form.get('tone')
        custom_tone = request.form.get('custom_tone', '').strip()
        
        # If custom tone is selected and provided, use it
        if tone == 'custom' and custom_tone:
            tone = custom_tone
            
        keywords = request.form.get('keywords', '')
        firm = request.form.get('firm', '')
        location = request.form.get('location', '')
        
        # Generate the blog post with the selected tone
        blog_content = azure_services.rewrite_content(
            FileManager.read_docx(article),
            tone,
            keywords,
            firm,
            location
        )
        
        # Generate an image for the blog post
        image_filename = azure_services.generate_image(blog_content)
        
        # Save the generated content to a file
        filename = FileManager.save_content(blog_content)
        
        # Set up the session data for the review page
        session['current_post'] = {
            'original': article,
            'content': blog_content,
            'image': image_filename,
            'created': datetime.now().strftime("%Y-%m-%d %H:%M"),
            'tone': tone,
            'filename': filename
        }
        
        # Initialize chat history
        session['chat_history'] = [{
            'role': 'assistant',
            'content': blog_content,
            'content_is_blog': True,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        }]
        
        # Generate a unique session ID for the chat
        session['session_id'] = os.urandom(16).hex()
        
        return redirect(url_for('review'))
    
    # Define tone options and their descriptions
    tone_options = [
        'Professional',
        # 'Conversational',
        # 'Authoritative',
        'Friendly',
        # 'Technical'
        'Educational'
    ]
    
    tone_descriptions = {
        'Professional': 'Formal and business-like tone suitable for corporate audiences',
        # 'Conversational': 'Casual and engaging tone that feels like a friendly discussion',
        # 'Authoritative': 'Strong and confident tone that establishes expertise',
        'Friendly': 'Warm and approachable tone that builds rapport with readers',
        # 'Technical': 'Detailed and precise tone focused on accuracy and technical details'
        'Educational': 'Clear and informative tone designed to explain concepts and enhance understanding'
    }
    
    return render_template('select.html',
                         article_name=article,
                         tone_options=tone_options,
                         tone_descriptions=tone_descriptions,
                         firm='Your Firm Name',  # Default value
                         location='Your Location')  # Default value

@app.route('/use_version', methods=['POST'])
def use_version():
    if 'current_post' not in session:
        return redirect(url_for('dashboard'))
    
    selected_content = request.form['content']
    
    session['current_post']['content'] = selected_content
    session.modified = True
    
    return redirect(url_for('finalize'))

@app.route('/finalize')
def finalize():
    if 'current_post' not in session:
        return redirect(url_for('dashboard'))
    
    post = session['current_post']
    filename = FileManager.save_content(post['content'])
    image_url = url_for('static', filename=f'generated/{post["image"]}') if post.get('image') else None
    
    return render_template('finalize.html', 
                         post=post,
                         filename=filename,
                         image_url=image_url)

@app.route('/review', methods=['GET', 'POST'])
def review():
    # Check if we have a filename parameter but no current_post in session
    filename = request.args.get('filename')
    if filename and 'current_post' not in session:
        # Try to load the content from the file
        try:
            with open(os.path.join(Config.GENERATED_DIR, filename), 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Set up the session data
            session['current_post'] = {
                'content': content,
                'filename': filename,
                'created': datetime.now().strftime("%Y-%m-%d %H:%M")
            }
            
            # Initialize chat history
            session['chat_history'] = [{
                'role': 'assistant',
                'content': content,
                'content_is_blog': True,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            }]
            
            # Generate a unique session ID for the chat
            session['session_id'] = os.urandom(16).hex()
        except Exception as e:
            print(f"Error loading file: {e}")
            return redirect(url_for('dashboard'))
    
    # If we still don't have current_post in session, redirect to dashboard
    if 'current_post' not in session:
        return redirect(url_for('dashboard'))
    
    post = session['current_post']
    
    if 'session_id' not in session:
        session['session_id'] = os.urandom(16).hex()
    
    if 'chat_history' not in session:
        session['chat_history'] = [{
            'role': 'assistant',
            'content': post['content'],
            'content_is_blog': True,
            'timestamp': datetime.now().strftime("%H:%M:%S")
        }]
    
    if request.method == 'POST':
        if 'edit_message' in request.form:  # Chat-style editing
            user_message = request.form['edit_message']
            
            current_content = next(
                (msg['content'] for msg in reversed(session['chat_history']) 
                 if msg['content_is_blog']),
                post['content']
            )
            
            edited_content = azure_services.edit_content(
                session['session_id'],
                user_message,
                current_content
            )
            
            session['chat_history'].append({
                'role': 'user',
                'content': user_message,
                'content_is_blog': False,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            session['chat_history'].append({
                'role': 'assistant',
                'content': edited_content,
                'content_is_blog': True,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            
            post['content'] = edited_content
            session['current_post'] = post
            
        elif 'content' in request.form:  # Manual editing
            post['content'] = request.form['content']
            session['current_post'] = post
            session['chat_history'].append({
                'role': 'assistant',
                'content': post['content'],
                'content_is_blog': True,
                'timestamp': datetime.now().strftime("%H:%M:%S")
            })
            
        session.modified = True
        return redirect(url_for('review'))
    
    # Save the current content to a file and get the filename
    if 'filename' not in post:
        filename = FileManager.save_content(post['content'])
        post['filename'] = filename
        session['current_post'] = post
    
    image_url = url_for('static', filename=f'generated/{post["image"]}') if post.get('image') else None
    
    return render_template('review.html', 
                         post=post,
                         chat_history=session['chat_history'],
                         image_url=image_url)

@app.route('/save_changes', methods=['POST'])
def save_changes():
    if 'current_post' not in session:
        return redirect(url_for('dashboard'))
    
    edited_content = request.form.get('content', '')
    
    session['current_post']['content'] = edited_content
    
    if 'chat_history' not in session:
        session['chat_history'] = []
    
    session['chat_history'].append({
        'role': 'system',
        'content': 'User saved manual changes',
        'content_is_blog': False,
        'timestamp': datetime.now().strftime("%H:%M:%S")
    })
    
    session.modified = True
    return redirect(url_for('finalize'))

@app.route('/download/<filename>')
def download(filename):
    return send_from_directory(Config.GENERATED_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)