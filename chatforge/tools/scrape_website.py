"""
Website Scraping Tool for Chatbot SaaS
======================================
Tool to scrape website content using BeautifulSoup for AI prompt generation.
"""

import frappe
import json
import requests
from bs4 import BeautifulSoup
from langchain.tools import tool
from urllib.parse import urlparse, urljoin
import re


@tool("scrape_website", return_direct=False)
def scrape_website_tool(website_url: str) -> str:
    """
    Scrape content from a website URL using BeautifulSoup.
    Extracts main text content, headings, and metadata.
    
    Args:
        website_url: The URL of the website to scrape (must start with http:// or https://)
    
    Returns:
        JSON string with scraped content and metadata
    """
    try:
        # Validate URL
        if not website_url or not website_url.strip():
            return json.dumps({
                "success": False,
                "error": "URL is required",
                "message": "Please provide a valid website URL"
            })
        
        website_url = website_url.strip()
        if not website_url.startswith(('http://', 'https://')):
            website_url = 'https://' + website_url
        
        # Validate URL format
        parsed = urlparse(website_url)
        if not parsed.netloc:
            return json.dumps({
                "success": False,
                "error": "Invalid URL format",
                "message": "Please provide a valid website URL (e.g., https://example.com)"
            })
        
        # Fetch the webpage
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        try:
            response = requests.get(website_url, headers=headers, timeout=10, allow_redirects=True)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            return json.dumps({
                "success": False,
                "error": f"Failed to fetch website: {str(e)}",
                "message": f"Could not access the website. Please check the URL and try again."
            })
        
        # Parse with BeautifulSoup
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Extract title
        title = soup.find('title')
        title_text = title.get_text().strip() if title else ""
        
        # Extract meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        description = meta_desc.get('content', '').strip() if meta_desc else ""
        
        # Extract main content
        # Try to find main content area
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|body', re.I))
        
        if main_content:
            content = main_content.get_text(separator=' ', strip=True)
        else:
            # Fallback to body
            body = soup.find('body')
            content = body.get_text(separator=' ', strip=True) if body else ""
        
        # Extract headings
        headings = []
        for heading in soup.find_all(['h1', 'h2', 'h3']):
            heading_text = heading.get_text().strip()
            if heading_text:
                headings.append(f"{heading.name.upper()}: {heading_text}")
        
        # Clean up content (remove extra whitespace)
        content = re.sub(r'\s+', ' ', content)
        content = content[:5000]  # Limit to 5000 characters
        
        # Build structured content
        structured_content = {
            "title": title_text,
            "description": description,
            "headings": headings[:10],  # Limit to 10 headings
            "content": content,
            "url": website_url
        }
        
        return json.dumps({
            "success": True,
            "content": structured_content,
            "message": f"Successfully scraped content from {website_url}"
        })
    
    except Exception as e:
        frappe.log_error(f"Website Scraping Error: {str(e)}", "Chatbot SaaS - Scrape Website Tool")
        return json.dumps({
            "success": False,
            "error": str(e),
            "message": "An error occurred while scraping the website. Please try again."
        })
