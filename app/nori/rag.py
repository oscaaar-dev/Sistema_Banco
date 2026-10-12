"""Recuperación documental local de Nori (TF-IDF). **No genera texto**: elige y cita fragmentos de documentos autorizados.

* Índice en memoria, construido una vez por proceso con ``scikit-learn`` (ya es dependencia de Norte) a partir de
  ``knowledge.build_chunks()``. No descarga modelos, no usa embeddings ni llamadas de red y no guarda nada en disco.
* El índice solo contiene documentos (de solo lectura); jamás texto de conversaciones, así que no filtra datos entre usuarios.
* Relevancia mínima configurable (``NORI_RAG_MIN_SCORE``) y al menos dos términos de la pregunta presentes en el fragmento
  cuando la pregunta tiene varios; por debajo de eso Nori reconoce que no tiene información suficiente.
"""
import re
import threading
import time
from dataclasses import dataclass

from flask import current_app

from . import knowledge
from .safety import normalize

_STOPWORDS = frozenset('''
a al algo algun alguna algunas alguno algunos ante antes aqui asi aun aunque bien cada casi como con contra cual cuales cuando
cuanto de del desde donde dos el ella ellas ello ellos en entre era eran es esa esas ese eso esos esta estan estar este esto
estos fue ha hace hacer han hasta hay la las le les lo los mas me mi mis mucho muy nada ni no nos nosotros o os otra otro para
pero poco por porque puede pueden puedo que quien quienes se sea ser si sin sobre son su sus tambien tan tanto te tengo ti
todo todos tu tus un una uno unos usted ustedes va ver vez voy y ya yo
norte nori quiero quisiera necesito saber dime explicame
'''.split())


_SUFFIXES = ('aciones', 'acion', 'amiento', 'ando', 'iendo', 'ados', 'adas', 'ado', 'ada', 'ar', 'er', 'ir', 'an', 'en',
             'as', 'es', 'os', 'a', 'o', 's')


def stem(token):
    """Raíz tosca en español: une singular/plural y formas de un mismo verbo («pago» ~ «pagar», «agrego» ~ «agregar»)."""
    if len(token) <= 4:
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[:-len(suffix)]
            break
    return token[:5]


def tokenize(text):
    return [stem(token) for token in normalize(text).split() if len(token) > 1 and token not in _STOPWORDS]


@dataclass(frozen=True)
class Hit:
    chunk: knowledge.Chunk
    score: float
    matched: int


class Index:
    def __init__(self, chunks):
        from sklearn.feature_extraction.text import TfidfVectorizer
        self.chunks = list(chunks)
        # el título y el encabezado pesan más: se repiten al construir el texto indexado
        documents = [f'{c.title}. {c.heading}. {c.heading}. {c.text}' for c in self.chunks]
        self._vectorizer = TfidfVectorizer(tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2),
                                           sublinear_tf=True)
        self._matrix = self._vectorizer.fit_transform(documents)
        self._terms = [set(tokenize(document)) for document in documents]
        self.built_in = 0.0

    def search(self, query, limit=3, min_score=0.0):
        query_tokens = set(tokenize(query))
        if not query_tokens or not self.chunks:
            return []
        scores = (self._matrix @ self._vectorizer.transform([query]).T).toarray().ravel()
        needed = min(2, len(query_tokens))
        # una consulta de una sola palabra es ambigua: exige una relevancia mayor
        floor = min_score if len(query_tokens) > 1 else max(min_score, 0.3)
        hits = []
        for position in scores.argsort()[::-1][:limit * 3]:
            score = float(scores[position])
            matched = len(query_tokens & self._terms[position])
            if score >= floor and matched >= needed:
                hits.append(Hit(self.chunks[position], score, matched))
        return hits[:limit]


_index = None
_failed = False
_lock = threading.Lock()


def next_in_document(chunk):
    """Fragmento siguiente del mismo documento o página (por orden de aparición); ``None`` si ya era el último."""
    index = get_index()
    if index is None:
        return None
    chunks = index.chunks
    for position, candidate in enumerate(chunks):
        if candidate.id == chunk.id:
            for following in chunks[position + 1:]:
                if following.title == chunk.title and following.heading != chunk.heading:
                    return following
            return None
    return None


def reset_index():
    """Descarta el índice (se reconstruye en la siguiente consulta). Útil tras editar documentos y en las pruebas."""
    global _index, _failed
    with _lock:
        _index, _failed = None, False


def get_index():
    """Índice de este proceso; ``None`` si no se pudo construir (Nori sigue respondiendo por reglas)."""
    global _index, _failed
    if _index is not None or _failed:
        return _index
    with _lock:
        if _index is None and not _failed:
            started = time.perf_counter()
            try:
                built = Index(knowledge.build_chunks())
                built.built_in = time.perf_counter() - started
                _index = built
            except Exception:
                _failed = True
                current_app.logger.warning('Nori: no se pudo construir el índice documental; se responde solo con reglas.')
    return _index


def search(query):
    """Fragmentos relevantes para la pregunta, de mayor a menor relevancia, según la configuración."""
    config = current_app.config
    if not config['NORI_RAG_ENABLED']:
        return []
    index = get_index()
    if index is None:
        return []
    return index.search(query[:config['NORI_RAG_QUERY_CHARS']], limit=config['NORI_RAG_MAX_SOURCES'] + 2,
                        min_score=config['NORI_RAG_MIN_SCORE'])


def _sentences(text):
    return [part.strip() for part in re.split(r'(?<=[.!?])\s+', text) if part.strip()]


def excerpt(chunk, query, limit, skip=None):
    """Las oraciones del fragmento más ligadas a la pregunta (en su orden original), sin pasar de ``limit`` caracteres.

    ``skip`` es un texto ya mostrado: sus oraciones no se repiten (sirve para «más información»)."""
    wanted = set(tokenize(query))
    sentences = [sentence for sentence in _sentences(chunk.text) if not skip or sentence not in skip]
    ranked = sorted(range(len(sentences)), key=lambda i: (-len(wanted & set(tokenize(sentences[i]))), i))
    chosen, used = [], 0
    for position in ranked:
        sentence = sentences[position]
        if used + len(sentence) + 1 > limit and chosen:
            continue
        chosen.append(position)
        used += len(sentence) + 1
        if used >= limit:
            break
    text = ' '.join(sentences[i] for i in sorted(chosen))
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'
