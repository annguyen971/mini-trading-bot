import os
import json
import logging
import time
from datetime import date, datetime, timedelta
from typing import List, Dict, Any, Optional
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

class GoogleNewsSource:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("Gemini API Key not found. GoogleNewsSource will fail if used.")
        
        # Initialize client with new google-genai SDK
        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None
            
        # Use gemini-2.0-flash-exp for best search grounding performance
        self.model_name = 'gemini-2.0-flash-exp' 

    def fetch_news_chunk(self, symbol: str, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """
        Fetch news for a specific symbol within a date range using Google Search Grounding.
        """
        if not self.client:
             raise ValueError("Gemini API Key is required for fetching news.")

        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        query = f"{symbol} tin tức chứng khoán"
        
        # Build prompt using types.Content
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=f"Tìm kiếm tin tức về {symbol} từ {start_str} đến {end_str}")
                ]
            )
        ]
        
        # Configure tools (Google Search)
        tools = [
            types.Tool(googleSearch=types.GoogleSearch())
        ]
        
        # System instruction
        system_instruction = [
            types.Part.from_text(text=f"""Tìm kiếm các bài báo chứng khoán/tài chính về "{query}" được đăng từ ngày {start_str} đến ngày {end_str}.
    
YÊU CẦU QUAN TRỌNG:
- BẮT BUỘC dùng Google Search tool.
- Sử dụng toán tử tìm kiếm: "{query} after:{start_str} before:{end_str}".
- Chỉ lấy tin tức từ các nguồn uy tín VN (CafeF, Vietstock, FireAnt, VnEconomy...).
- Trả về danh sách JSON các bài viết tìm thấy.

JSON SCHEMA:
[
  {{"title": "...", "url": "...", "snippet": "...", "date": "YYYY-MM-DD"}}
]

CHỈ TRẢ VỀ JSON, KHÔNG KÈM TEXT GIẢI THÍCH.""")
        ]
        
        generate_content_config = types.GenerateContentConfig(
            tools=tools,
            system_instruction=system_instruction,
            response_mime_type="application/json",
            temperature=0.1
        )

        try:
            # Generate content with search grounding
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=generate_content_config
            )
            
            if not response.text:
                logger.warning(f"Empty response from Gemini for {symbol} ({start_date} - {end_date})")
                return []

            try:
                # Clean response text and extract JSON array
                text = response.text.strip()
                
                start_idx = text.find('[')
                if start_idx != -1:
                    # Simple bracket counting to find the matching closing bracket
                    count = 0
                    for i in range(start_idx, len(text)):
                        if text[i] == '[':
                            count += 1
                        elif text[i] == ']':
                            count -= 1
                            if count == 0:
                                json_str = text[start_idx:i+1]
                                try:
                                    news_items = json.loads(json_str)
                                    if isinstance(news_items, list):
                                        valid_items = []
                                        for item in news_items:
                                            if item.get('title') and item.get('url'):
                                                valid_items.append(item)
                                        return valid_items
                                except json.JSONDecodeError:
                                    continue
                
                logger.warning(f"No valid JSON array found in response: {text[:200]}...")
                return []
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON from Gemini response: {response.text[:200]}... Error: {e}")
                return []

        except Exception as e:
            # Handle rate limits and other errors
            if "429" in str(e):
                logger.warning("Gemini Quota Exceeded (429).")
                raise e 
            
            logger.error(f"Error calling Gemini API: {e}")
            return []
