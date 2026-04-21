from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass

from api.services.ai_service import AIService

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageFilter, ImageOps
except ImportError:  # pragma: no cover - optional runtime dependency
    Image = None
    ImageFilter = None
    ImageOps = None


class PhotoSearchError(Exception):
    """Raised when no reliable query can be derived from a photo."""


@dataclass(slots=True)
class PhotoSearchResult:
    query: str
    description: str
    source: str
    recognized_text: list[str]


class PhotoSearchService:
    def __init__(self) -> None:
        self._ocr_engine = None
        self._ocr_unavailable = False

    async def identify_from_bytes(
        self,
        image_bytes: bytes,
        mime_type: str,
        ai_service: AIService,
    ) -> PhotoSearchResult:
        ai_errors: list[str] = []

        if ai_service.available:
            try:
                result = await ai_service.search_by_photo_from_bytes(image_bytes, mime_type)
                query = self._sanitize_query(result.get("query", ""))
                if query:
                    description = str(result.get("description", "")).strip()
                    return PhotoSearchResult(
                        query=query,
                        description=description or "Товар определён через AI.",
                        source="ai",
                        recognized_text=[],
                    )
            except Exception as exc:
                logger.warning("AI photo search failed, trying OCR fallback: %s", exc)
                ai_errors.append(str(exc))

        ocr_result = self._identify_with_ocr(image_bytes)
        if ocr_result is not None:
            return ocr_result

        if ai_errors:
            raise PhotoSearchError(
                "Не удалось определить товар на фото. "
                "Попробуйте более чёткое фото или кадр с названием модели."
            ) from None

        raise PhotoSearchError(
            "Поиск по фото недоступен: AI не настроен, а OCR fallback сейчас не готов."
        )

    def _identify_with_ocr(self, image_bytes: bytes) -> PhotoSearchResult | None:
        if not self._ocr_import_ready():
            return None

        engine = self._get_ocr_engine()
        if engine is None:
            return None

        image = self._load_image(image_bytes)
        if image is None:
            return None

        texts: list[str] = []
        for variant in self._build_variants(image):
            try:
                result, _elapsed = engine(variant)
            except Exception as exc:
                logger.warning("OCR variant failed: %s", exc)
                continue
            texts.extend(self._extract_texts(result))

        recognized = self._dedupe_texts(texts)
        query = self._build_query(recognized)
        if not query:
            return None

        preview = ", ".join(recognized[:3])
        description = (
            f"Запрос собран по тексту на фото: {preview}."
            if preview
            else "Запрос собран по тексту на фото."
        )
        return PhotoSearchResult(
            query=query,
            description=description,
            source="ocr",
            recognized_text=recognized[:5],
        )

    def _ocr_import_ready(self) -> bool:
        return Image is not None and ImageOps is not None and ImageFilter is not None

    def _get_ocr_engine(self):
        if self._ocr_unavailable:
            return None
        if self._ocr_engine is not None:
            return self._ocr_engine
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # pragma: no cover - import depends on runtime
            logger.warning("RapidOCR is unavailable: %s", exc)
            self._ocr_unavailable = True
            return None
        self._ocr_engine = RapidOCR()
        return self._ocr_engine

    def _load_image(self, image_bytes: bytes):
        if Image is None or ImageOps is None:
            return None
        try:
            image = Image.open(io.BytesIO(image_bytes))
            image = ImageOps.exif_transpose(image).convert("RGB")
        except Exception as exc:
            logger.warning("Failed to decode image for OCR: %s", exc)
            return None

        max_side = max(image.size)
        if max_side > 1800:
            scale = 1800 / max_side
            resized = (
                max(1, int(image.size[0] * scale)),
                max(1, int(image.size[1] * scale)),
            )
            image = image.resize(resized, Image.Resampling.LANCZOS)
        return image

    def _build_variants(self, image):
        try:
            import numpy as np
        except Exception as exc:  # pragma: no cover - optional runtime dependency
            logger.warning("NumPy is unavailable for OCR fallback: %s", exc)
            return []

        grayscale = ImageOps.autocontrast(ImageOps.grayscale(image))
        sharpened = grayscale.filter(ImageFilter.SHARPEN)
        return [np.array(image), np.array(grayscale), np.array(sharpened)]

    def _extract_texts(self, payload) -> list[str]:
        texts: list[str] = []
        for item in payload or []:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            text = str(item[1] or "").strip()
            if not text:
                continue
            score = 1.0
            if len(item) >= 3:
                try:
                    score = float(item[2])
                except (TypeError, ValueError):
                    score = 1.0
            if score >= 0.35:
                texts.append(text)
        return texts

    def _dedupe_texts(self, texts: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for text in texts:
            cleaned = re.sub(r"\s+", " ", text).strip()
            key = cleaned.casefold()
            if len(cleaned) < 2 or key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
        return result

    def _build_query(self, texts: list[str]) -> str:
        normalized = self._normalize_text(" ".join(texts))
        for extractor in (
            self._extract_iphone_query,
            self._extract_macbook_query,
            self._extract_airpods_query,
            self._extract_apple_watch_query,
            self._extract_samsung_query,
            self._extract_playstation_query,
            self._extract_xbox_query,
            self._extract_generic_query,
        ):
            query = extractor(normalized)
            if query:
                return self._sanitize_query(query)
        return ""

    def _extract_iphone_query(self, text: str) -> str:
        if not re.search(r"\b(iphone|айфон)\b", text):
            return ""
        parts = ["iphone"]
        model = self._search_first(text, r"\b(11|12|13|14|15|16)\b")
        if model:
            parts.append(model)
        suffix = self._search_first(text, r"\b(pro max|pro|plus|mini)\b")
        if suffix:
            parts.extend(suffix.split())
        storage = self._extract_storage(text)
        if storage:
            parts.append(storage)
        return " ".join(parts)

    def _extract_macbook_query(self, text: str) -> str:
        if "macbook" not in text:
            return ""
        parts = ["macbook"]
        model = self._search_first(text, r"\b(air|pro)\b")
        if model:
            parts.append(model)
        chip = self._search_first(text, r"\b(m1|m2|m3|m4)\b")
        if chip:
            parts.append(chip)
        size = self._search_first(text, r"\b(13|14|15|16)\b")
        if size:
            parts.append(size)
        return " ".join(parts)

    def _extract_airpods_query(self, text: str) -> str:
        if not re.search(r"\b(airpods|эйрподс)\b", text):
            return ""
        parts = ["airpods"]
        model = self._search_first(text, r"\b(pro|max|2|3|4)\b")
        if model:
            parts.append(model)
        return " ".join(parts)

    def _extract_apple_watch_query(self, text: str) -> str:
        if not re.search(r"\b(apple watch|watch)\b", text):
            return ""
        parts = ["apple", "watch"]
        model = self._search_first(text, r"\b(ultra|se|series)\b")
        if model:
            parts.append(model)
        number = self._search_first(text, r"\b(7|8|9|10)\b")
        if number:
            parts.append(number)
        return " ".join(parts)

    def _extract_samsung_query(self, text: str) -> str:
        if not re.search(r"\b(samsung|galaxy)\b", text):
            return ""
        parts = ["samsung"]
        if "galaxy" in text:
            parts.append("galaxy")
        model = self._search_first(
            text,
            r"\b(s\d{2}|a\d{2}|note\s?\d{1,2}|z\s?flip\s?\d|z\s?fold\s?\d)\b",
        )
        if model:
            parts.append(model.replace(" ", ""))
        suffix = self._search_first(text, r"\b(ultra|plus|fe)\b")
        if suffix:
            parts.append(suffix)
        storage = self._extract_storage(text)
        if storage:
            parts.append(storage)
        return " ".join(parts)

    def _extract_playstation_query(self, text: str) -> str:
        if not re.search(r"\b(playstation|ps5|ps4)\b", text):
            return ""
        if "ps5" in text:
            return "ps5"
        if "ps4" in text:
            return "ps4"
        return "playstation"

    def _extract_xbox_query(self, text: str) -> str:
        if "xbox" not in text:
            return ""
        parts = ["xbox"]
        model = self._search_first(text, r"\b(series x|series s|one)\b")
        if model:
            parts.extend(model.split())
        return " ".join(parts)

    def _extract_generic_query(self, text: str) -> str:
        stopwords = {
            "model",
            "serial",
            "imei",
            "storage",
            "memory",
            "phone",
            "gb",
            "tb",
            "apple",
            "samsung",
            "galaxy",
            "watch",
            "series",
            "new",
            "used",
            "sale",
            "smartphone",
            "mobile",
        }
        words = re.findall(r"[a-zа-я0-9+]{2,}", text)
        selected: list[str] = []
        for word in words:
            if word in stopwords:
                continue
            if word.isdigit() and len(word) < 3:
                continue
            if word not in selected:
                selected.append(word)
            if len(selected) == 4:
                break
        return " ".join(selected)

    def _extract_storage(self, text: str) -> str:
        match = re.search(r"\b(16|32|64|128|256|512|1024|1)\s?(gb|гб|tb|тб)\b", text)
        if not match:
            return ""
        amount, unit = match.groups()
        unit = "tb" if unit in {"tb", "тб"} else "gb"
        if amount == "1" and unit == "gb":
            return ""
        if amount == "1024":
            return "1tb"
        return f"{amount}{unit}"

    def _search_first(self, text: str, pattern: str) -> str:
        match = re.search(pattern, text)
        return match.group(1) if match else ""

    def _normalize_text(self, text: str) -> str:
        text = text.casefold()
        text = text.replace("ё", "е")
        text = text.replace("|", " ")
        text = re.sub(r"[^a-zа-я0-9+]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _sanitize_query(self, query: str) -> str:
        query = re.sub(r"\s+", " ", str(query or "").strip())
        return query[:200]


_photo_search_service: PhotoSearchService | None = None


def get_photo_search_service() -> PhotoSearchService:
    global _photo_search_service
    if _photo_search_service is None:
        _photo_search_service = PhotoSearchService()
    return _photo_search_service
