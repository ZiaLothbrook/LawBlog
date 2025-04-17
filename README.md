# NLBM Blog Drafting Tool

A Flask-based web application that helps attorneys quickly generate customized blog posts from curated legal articles, with AI-powered rewriting and image generation.

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Prepare the Documents

Place the legal DOCX files in the `articles/` directory.

## Running the Application

Start the development server:

```bash
python app.py
```

The application will be available at `http://localhost:5000`

## Usage Guide

### 1. Login
- Default credentials:
  - Username: `admin`
  - Password: `password123`

### 2. Select an Article
- Choose from available DOCX files in `articles/` folder

### 3. Customize the Post
- Select a tone (or specify custom tone)
- Add relevant keywords
- Enter the firm name and location
- Click "Generate Blog Post"

### 4. Review & Edit
- The AI-generated post will appear with a preview
- Make any necessary edits in the text area
- An automatically generated image will be displayed

### 5. Finalize & Download
- Review the final version
- Download the text content as a TXT file
- The image is automatically saved in `static/generated/`
