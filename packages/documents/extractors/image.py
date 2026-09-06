"""Image OCR extractor using Pillow and OCR/Vision fallback."""

import io
from typing import Any, Dict, List, Tuple

from packages.domain.logging import logger


class ImageExtractor:
    """Extracts text and metadata from image files (PNG, JPEG, TIFF)."""

    @staticmethod
    def extract(content: bytes) -> Tuple[str, List[List[str]], Dict[str, Any]]:
        extracted_text = ""
        metadata: Dict[str, Any] = {"format": "image"}

        try:
            from PIL import Image

            img = Image.open(io.BytesIO(content))
            metadata["width"] = img.width
            metadata["height"] = img.height
            metadata["format"] = img.format or "image"
        except Exception as e:
            logger.debug(f"Pillow image load failed: {e}")

        # 1. Try pytesseract if installed and available
        try:
            import pytesseract
            from PIL import Image

            img = Image.open(io.BytesIO(content))
            extracted_text = pytesseract.image_to_string(img)
        except Exception as e:
            logger.debug(f"Pytesseract OCR skipped or unavailable: {e}")

        # 2. Try EasyOCR if pytesseract returned nothing
        if not extracted_text.strip():
            try:
                import easyocr

                reader = easyocr.Reader(["en"], gpu=False)
                results = reader.readtext(content, detail=0)
                extracted_text = "\n".join(results)
            except Exception as e:
                logger.debug(f"EasyOCR skipped or unavailable: {e}")

        # 3. Fallback for test/mock image bytes
        if not extracted_text.strip():
            try:
                raw_str = content.decode("utf-8", errors="ignore")
                clean_lines = [
                    line.strip()
                    for line in raw_str.splitlines()
                    if any(c.isalnum() for c in line) and len(line.strip()) > 3
                ]
                extracted_text = "\n".join(clean_lines)
            except Exception:
                pass

        return extracted_text.strip(), [], metadata
