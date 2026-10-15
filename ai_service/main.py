import os
import json
import asyncio
import logging
from functools import wraps
from importlib.metadata import version as package_version
import re
import uuid
import tempfile
from dotenv import load_dotenv
from typing import Optional
from urllib.parse import quote

import httpx
import fitz  # PyMuPDF

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, ScoredPoint

load_dotenv()

APP_VERSION = "0.6.9-v9.10.2-guideline-chunks"
logger = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="UniSubmit AI Service",
    version=APP_VERSION
)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
DEFAULT_LLM_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:35b")
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
QDRANT_URL = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")

VECTOR_SIZE = 1024

embedding_model = None
embedding_status = "not_loaded"
embedding_error = None

qdrant_client = None
qdrant_status = "not_loaded"
qdrant_error = None


# -----------------------------
# Startup
# -----------------------------

@app.on_event("startup")
async def startup_event():
    global embedding_model, embedding_status, embedding_error
    global qdrant_client, qdrant_status, qdrant_error

    try:
        from FlagEmbedding import BGEM3FlagModel

        embedding_status = "loading"
        embedding_model = BGEM3FlagModel(
            EMBEDDING_MODEL_NAME,
            use_fp16=True
        )
        embedding_status = "ready"
        embedding_error = None

    except Exception as e:
        embedding_status = "error"
        embedding_error = str(e)
        logger.exception("Embedding startup failed")

    try:
        qdrant_status = "connecting"
        qdrant_client = QdrantClient(url=QDRANT_URL, api_key=os.getenv("QDRANT_API_KEY") or None)
        logger.info("Qdrant client=%s server=%s", package_version("qdrant-client"), qdrant_client.info().version)
        qdrant_client.get_collections()
        qdrant_status = "ready"
        qdrant_error = None

    except Exception as e:
        qdrant_status = "error"
        qdrant_error = str(e)
        logger.exception("Qdrant startup failed")


# -----------------------------
# Models
# -----------------------------

class EmbeddingRequest(BaseModel):
    texts: list[str]


class SearchRequest(BaseModel):
    department: str
    program: str
    submission_type: str
    template_version: str
    query: str
    limit: int = Field(default=5, ge=1, le=100)


class ValidateRAGRequest(BaseModel):
    department: str
    program: str
    submission_type: str
    template_version: str
    student_text: str
    query: str = "What requirements are relevant to this part of the submission?"
    base_model: str = DEFAULT_LLM_MODEL
    limit: int = Field(default=6, ge=1, le=100)
    explicit_requirements: list[dict] = Field(default_factory=list, max_length=80)


# -----------------------------
# Helpers
# -----------------------------

def slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def make_collection_name(
    department: str,
    program: str,
    submission_type: str,
    template_version: str,
) -> str:
    return "_".join([
        slugify(department),
        slugify(program),
        slugify(submission_type),
        slugify(template_version),
    ])


def extract_pdf_pages(pdf_path: str) -> list[dict]:
    doc = fitz.open(pdf_path)
    pages = []

    try:
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if text:
                pages.append({
                    "page": i + 1,
                    "text": text
                })
    finally:
        doc.close()

    return pages


def chunk_text(
    text: str,
    chunk_size: int = 1200,
    overlap: int = 200
) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            cut = text.rfind(". ", start, end)
            if cut > start + int(chunk_size * 0.55):
                end = cut + 1

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        start = max(end - overlap, start + 1)

    return chunks


def embed_texts(texts: list[str]) -> list[list[float]]:
    if embedding_model is None or embedding_status != "ready":
        raise RuntimeError(
            f"Embedding model unavailable: {embedding_error}"
        )

    result = embedding_model.encode(
        texts,
        batch_size=4,
        max_length=1024
    )

    return result["dense_vecs"].tolist()


def ensure_collection(collection_name: str, recreate: bool = False):
    if qdrant_client is None or qdrant_status != "ready":
        raise RuntimeError(
            f"Qdrant unavailable: {qdrant_error}"
        )

    existing = {
        c.name
        for c in qdrant_client.get_collections().collections
    }

    if recreate and collection_name in existing:
        qdrant_client.delete_collection(collection_name)
        existing.remove(collection_name)

    if collection_name not in existing:
        qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=VECTOR_SIZE,
                distance=Distance.COSINE
            )
        )


def retrieve_chunks(
    collection_name: str,
    query: str,
    limit: int = 5
) -> list[dict]:
    if qdrant_client is None or qdrant_status != "ready":
        raise RuntimeError("Qdrant is not ready.")

    vector = embed_texts([query])[0]

    # Keep compatibility checks enabled. Older servers lack /points/query.
    def legacy_search():
        if callable(getattr(qdrant_client, "search", None)):
            return qdrant_client.search(collection_name=collection_name,
                query_vector=vector, limit=limit, with_payload=True)
        # New clients also removed the generated legacy search model/API.
        headers = {}
        if os.getenv("QDRANT_API_KEY"):
            headers["api-key"] = os.environ["QDRANT_API_KEY"]
        response = httpx.post(
            QDRANT_URL.rstrip("/") + "/collections/" + quote(collection_name, safe="") + "/points/search",
            json={"vector": vector, "limit": limit, "with_payload": True},
            headers=headers, timeout=30)
        response.raise_for_status()
        return [ScoredPoint.model_validate(point) for point in response.json()["result"]]

    if not callable(getattr(qdrant_client, "query_points", None)):
        points = legacy_search()
    else:
        try:
            result = qdrant_client.query_points(collection_name=collection_name,
                query=vector, limit=limit, with_payload=True)
            points = result.points
        except Exception as exc:
            if getattr(exc, "status_code", None) not in {404, 405, 501}:
                raise
            # A missing collection also produces 404. Never mask it as an API mismatch.
            qdrant_client.get_collection(collection_name=collection_name)
            logger.warning("Qdrant query endpoint unavailable; using legacy dense search")
            points = legacy_search()

    hits = []

    for point in points:
        payload = point.payload or {}

        hits.append({
            "score": point.score,
            "text": payload.get("text", ""),
            "page": payload.get("page"),
            "chunk_index": payload.get("chunk_index"),
            "department": payload.get("department"),
            "program": payload.get("program"),
            "submission_type": payload.get("submission_type"),
            "template_version": payload.get("template_version"),
        })

    return hits


class IncompleteOllamaResponse(ValueError):
    pass


async def call_ollama(model: str, prompt: str, json_mode: bool = False) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0,
                    "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "16384")),
                    "num_predict": int(os.getenv("OLLAMA_NUM_PREDICT", "4096"))},
    }
    if json_mode:
        # Ollama JSON mode substantially reduces malformed Qwen output.
        payload["format"] = "json"
        # Avoid spending the structured-output budget on Qwen reasoning.
        payload["think"] = os.getenv("OLLAMA_JSON_THINK", "false").lower() == "true"
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.post(
            OLLAMA_URL.rstrip("/") + "/api/generate",
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get("response"), str):
            raise ValueError("Ollama returned no textual response")
        if body.get("done") is False or body.get("done_reason") == "length":
            logger.warning("Ollama output truncated model=%s reason=%s eval_count=%s prompt_eval_count=%s",
                           model, body.get("done_reason"), body.get("eval_count"), body.get("prompt_eval_count"))
            raise IncompleteOllamaResponse("Ollama response was incomplete; increase the model context/output budget")
        return body["response"].strip()


# -----------------------------
# General endpoints
# -----------------------------

@app.get("/")
async def root():
    return {
        "service": "UniSubmit AI Service",
        "status": "online",
        "version": APP_VERSION,
    }


@app.get("/health")
async def health():
    ollama_status = "error"
    models = []
    ollama_error = None

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                OLLAMA_URL.rstrip("/") + "/api/tags"
            )
            response.raise_for_status()

            models = [
                model.get("name", "")
                for model in response.json().get("models", [])
            ]

            ollama_status = "ready"

    except Exception as e:
        ollama_error = str(e)

    rag_ready = (
        ollama_status == "ready"
        and embedding_status == "ready"
        and qdrant_status == "ready"
    )

    return {
        "status": "online",
        "version": APP_VERSION,

        "ollama": {
            "url": OLLAMA_URL,
            "status": ollama_status,
            "models": models,
            "default_model": DEFAULT_LLM_MODEL,
            "error": ollama_error,
        },

        "embedding": {
            "model": EMBEDDING_MODEL_NAME,
            "status": embedding_status,
            "error": embedding_error,
        },

        "qdrant": {
            "url": QDRANT_URL,
            "status": qdrant_status,
            "error": qdrant_error,
        },

        "rag": "ready" if rag_ready else "not_ready",
    }


@app.post("/embed")
async def embed(request: EmbeddingRequest):
    try:
        vectors = embed_texts(request.texts)

        return {
            "status": "success",
            "model": EMBEDDING_MODEL_NAME,
            "count": len(vectors),
            "dimension": len(vectors[0]) if vectors else 0,
            "vectors": vectors,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# -----------------------------
# RAG collection management
# -----------------------------

@app.get("/rag/collections")
async def rag_collections():
    if qdrant_client is None or qdrant_status != "ready":
        raise HTTPException(
            status_code=503,
            detail=f"Qdrant unavailable: {qdrant_error}"
        )

    result = qdrant_client.get_collections()

    return {
        "status": "success",
        "count": len(result.collections),
        "collections": [c.name for c in result.collections],
    }


@app.delete("/rag/collection/{collection_name}")
async def delete_rag_collection(collection_name: str):
    if qdrant_client is None or qdrant_status != "ready":
        raise HTTPException(
            status_code=503,
            detail="Qdrant is not ready."
        )

    try:
        qdrant_client.delete_collection(collection_name)

        return {
            "status": "success",
            "deleted": collection_name
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# -----------------------------
# Template PDF indexing
# -----------------------------

@app.post("/rag/index-template")
async def index_template(
    department: str = Form(...),
    program: str = Form(...),
    submission_type: str = Form(...),
    template_version: str = Form(...),
    recreate: bool = Form(True),
    pdf: UploadFile = File(...),
):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF template files are accepted."
        )

    collection_name = make_collection_name(
        department,
        program,
        submission_type,
        template_version
    )

    temp_path = None

    try:
        suffix = ".pdf"

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix
        ) as temp:
            temp.write(await pdf.read())
            temp_path = temp.name

        pages = extract_pdf_pages(temp_path)

        if not pages:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No machine-readable text was found in the PDF. "
                    "Use a searchable PDF or add OCR support."
                )
            )

        ensure_collection(
            collection_name,
            recreate=recreate
        )

        payloads = []

        for page_item in pages:
            page_no = page_item["page"]
            chunks = chunk_text(page_item["text"])

            for idx, chunk in enumerate(chunks):
                payloads.append({
                    "text": chunk,
                    "page": page_no,
                    "chunk_index": idx,
                    "department": department,
                    "program": program,
                    "submission_type": submission_type,
                    "template_version": template_version,
                    "source_file": pdf.filename,
                })

        if not payloads:
            raise HTTPException(
                status_code=400,
                detail="No chunks could be generated from the PDF."
            )

        texts = [p["text"] for p in payloads]
        vectors = embed_texts(texts)

        points = []

        for payload, vector in zip(payloads, vectors):
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload=payload
                )
            )

        batch_size = 64

        for i in range(0, len(points), batch_size):
            qdrant_client.upsert(
                collection_name=collection_name,
                points=points[i:i + batch_size],
                wait=True
            )

        return {
            "status": "success",
            "collection": collection_name,
            "source_file": pdf.filename,
            "pages_with_text": len(pages),
            "chunks_indexed": len(points),
            "embedding_model": EMBEDDING_MODEL_NAME,
            "vector_size": VECTOR_SIZE,
        }

    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# -----------------------------
# Semantic search
# -----------------------------

@app.post("/rag/search")
async def rag_search(request: SearchRequest):
    collection_name = make_collection_name(
        request.department,
        request.program,
        request.submission_type,
        request.template_version
    )

    try:
        hits = retrieve_chunks(
            collection_name,
            request.query,
            request.limit
        )

        return {
            "status": "success",
            "collection": collection_name,
            "query": request.query,
            "count": len(hits),
            "results": hits,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


def _semantic_norm(text: str) -> str:
    s=(text or '').strip().lower()
    s=re.sub(r'[^a-z0-9\s]+',' ',s)
    s=re.sub(r'\s+',' ',s).strip()
    s=re.sub(r'\b(should|shall|must|required to|needs to|need to)\b','must',s)
    return re.sub(r'\s+',' ',s).strip()


def _dedupe_semantic_rules(rules: list[dict]) -> list[dict]:
    merged=[]; by_key={}
    for raw in rules or []:
        if not isinstance(raw,dict):
            continue
        r=dict(raw)
        req=str(r.get('requirement') or '').strip()
        if not req:
            continue
        section=str(r.get('target_section') or r.get('source_section') or '').strip()
        key=(_semantic_norm(req),_semantic_norm(section))
        if key not in by_key:
            pages=[]
            for x in _source_pages(r.get('source_pages')):
                try: pages.append(int(x))
                except Exception: pass
            try:
                if r.get('source_page') is not None: pages.append(int(r.get('source_page')))
            except Exception: pass
            r['source_pages']=sorted(set(pages))
            if r['source_pages']: r['source_page']=r['source_pages'][0]
            r['target_section']=section
            by_key[key]=r; merged.append(r)
        else:
            base=by_key[key]
            pages=set(base.get('source_pages') or [])
            for x in _source_pages(r.get('source_pages')):
                try: pages.add(int(x))
                except Exception: pass
            try:
                if r.get('source_page') is not None: pages.add(int(r.get('source_page')))
            except Exception: pass
            base['source_pages']=sorted(pages)
            if base['source_pages']: base['source_page']=base['source_pages'][0]
            evid=[]
            for x in [base.get('evidence'),r.get('evidence')]:
                x=str(x or '').strip()
                if x and x not in evid: evid.append(x)
            if evid: base['evidence']=' | '.join(evid)[:1600]
    return merged


def _extract_target_section(student_text: str, target_section: str) -> str:
    """Best-effort section extraction from plain PDF text.

    Falls back to the full student document when a target section cannot be safely
    isolated. This reduces NOT VERIFIED results without pretending a missing section
    was found.
    """
    text=student_text or ''
    target=(target_section or '').strip()
    if not target:
        return text[:30000]
    lines=text.splitlines()
    norm_target=_semantic_norm(target)
    start=None
    heading_re=re.compile(r'^\s*(?:chapter\s+\d+\s*[:.\-]?\s*)?(.+?)\s*$',re.I)
    for i,line in enumerate(lines):
        raw=line.strip()
        if not raw: continue
        m=heading_re.match(raw)
        candidate=_semantic_norm(m.group(1) if m else raw)
        if candidate==norm_target or candidate.startswith(norm_target+' ') or norm_target.startswith(candidate+' '):
            start=i; break
    if start is None:
        return ''
    out=[]
    # stop at next strong all-caps/chapter heading after some content
    for j in range(start,min(len(lines),start+350)):
        raw=lines[j].strip()
        if j>start+1 and raw:
            letters=''.join(ch for ch in raw if ch.isalpha())
            is_caps=bool(letters) and letters==letters.upper() and len(raw)<120
            is_chapter=bool(re.match(r'^CHAPTER\s+\d+',raw,re.I))
            if (is_caps or is_chapter) and len(out)>3:
                break
        out.append(lines[j])
    return '\n'.join(out).strip()[:18000]


# -----------------------------
# Grounded RAG validation
# -----------------------------

def _source_pages(value):
    if value is None:
        return []
    return value if isinstance(value, (list, tuple, set)) else [value]


def _validation_payload(text):
    payload = _parse_json_object(text)
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    results = payload.get("semantic_results")
    if not isinstance(results, list) or any(not isinstance(x, dict) for x in results):
        raise ValueError("semantic_results must be an array of objects")
    suggestions = payload.get("improvement_suggestions", [])
    if not isinstance(suggestions, list) or any(not isinstance(x, str) for x in suggestions):
        raise ValueError("improvement_suggestions must be an array of strings")
    if not isinstance(payload.get("grounding_note", ""), str):
        raise ValueError("grounding_note must be a string")
    return payload


def _log_validation_errors(func):
    @wraps(func)
    async def wrapped(request):
        try:
            return await func(request)
        except HTTPException:
            raise
        except Exception as exc:
            error_id = uuid.uuid4().hex[:12]
            logger.exception("Unexpected RAG validation failure error_id=%s", error_id)
            raise HTTPException(status_code=500,
                detail=f"Unexpected validation failure. See AI service log: {error_id}") from exc
    return wrapped


@app.post("/rag/validate")
@_log_validation_errors
async def rag_validate(request: ValidateRAGRequest):
    collection_name = make_collection_name(request.department, request.program, request.submission_type, request.template_version)
    explicit=[]
    for idx, raw in enumerate((request.explicit_requirements or [])[:80], start=1):
        if not isinstance(raw,dict): continue
        req=dict(raw)
        req['semantic_id']=str(req.get('semantic_id') or req.get('rule_id') or f'SEM-{idx:03d}')
        req['requirement']=str(req.get('requirement') or '').strip()
        req['target_section']=str(req.get('target_section') or req.get('source_section') or '').strip()
        severity = str(req.get('final_severity') or ('Warning' if str(req.get('mandatory', True)).lower() in {'false', '0'} else 'Mandatory')).strip().title()
        if severity == 'Ignore' or req.get('enabled') is False:
            continue
        if severity not in {'Mandatory', 'Warning', 'Recommendation'}:
            raise HTTPException(status_code=422, detail=f'Invalid semantic severity: {severity}')
        req['final_severity'] = severity
        if req['requirement']: explicit.append(req)
    explicit=_dedupe_semantic_rules(explicit)
    # Ensure stable IDs after deduplication.
    used_ids = set()
    for idx,req in enumerate(explicit,start=1):
        semantic_id = str(req.get('semantic_id') or f'SEM-{idx:03d}')
        while semantic_id in used_ids:
            semantic_id += f'-{idx}'
        req['semantic_id'] = semantic_id
        used_ids.add(semantic_id)
        req['student_section_text']=_extract_target_section(request.student_text,req.get('target_section',''))
    try:
        hits=retrieve_chunks(collection_name,request.query,request.limit)
        seen={(h.get('page'),h.get('text')) for h in hits}
        for req in explicit[:40]:
            for h in retrieve_chunks(collection_name,req['requirement'],3):
                k=(h.get('page'),h.get('text'))
                if k not in seen: seen.add(k); hits.append(h)
        hits=sorted(hits,key=lambda x:x.get('score',0),reverse=True)[:40]
    except Exception as e:
        logger.exception('RAG retrieval failed collection=%s', collection_name)
        code = 404 if getattr(e, 'status_code', None) == 404 else 503
        raise HTTPException(status_code=code,detail=f'RAG retrieval failed for {collection_name}: {e}') from e
    if not hits: raise HTTPException(status_code=404,detail='No relevant guideline context was retrieved for this submission.')
    context='\n\n'.join(f"[SOURCE {i} | Page {h['page']} | Score {h['score']:.4f}]\n{h['text']}" for i,h in enumerate(hits,1))
    prompt=f'''You are UniSubmit's academic pre-submission validation assistant.
Use ONLY the approved guideline context below. Never invent a requirement. Evaluate EVERY parsed semantic requirement exactly once.
For each rule, inspect its student_section_text first. If that targeted section is non-empty, make a PASS/FAIL/WARNING decision from that evidence rather than defaulting to NOT VERIFIED. Use NOT VERIFIED only when the target section is genuinely missing/unreadable or the available student evidence is too ambiguous for a defensible decision. For manual semantic rules, use expected_evidence when provided as a cue for what evidence to look for; do not treat it as student evidence.
Status rules: satisfied=PASS; unmet Mandatory=FAIL; unmet Warning/Recommendation=WARNING; insufficient evidence=NOT VERIFIED. Only Mandatory+FAIL blocks.
Return VALID JSON ONLY:
{{"semantic_results":[{{"semantic_id":"SEM-001","requirement":"...","severity":"Mandatory|Warning|Recommendation","status":"PASS|FAIL|WARNING|NOT VERIFIED","message":"...","detected_evidence":"...","correction":"...","source_page":1,"source_section":"...","source_evidence":"...","confidence":0.0}}],"improvement_suggestions":["..."],"grounding_note":"..."}}
PARSED SEMANTIC REQUIREMENTS WITH TARGETED STUDENT EVIDENCE:\n{json.dumps(explicit, ensure_ascii=False)}\n\nAPPROVED GUIDELINE CONTEXT:\n{context}\n\nFULL STUDENT CONTENT (fallback only):\n{request.student_text[:18000]}'''
    try:
        raw=await call_ollama(request.base_model,prompt,json_mode=True)
    except Exception as e:
        logger.exception('Ollama semantic generation failed model=%s', request.base_model)
        raise HTTPException(status_code=503,detail=f'Local LLM unavailable: {e}') from e
    try:
        payload = _validation_payload(raw)
    except ValueError as initial_error:
        # Log shape/length only; student text and model output are private.
        logger.warning("Qwen semantic JSON invalid (%s); response_chars=%s; repairing once",
                       initial_error, len(raw))
        repair = ('Repair into valid JSON only. Return an object with semantic_results '
                  '(array of objects with semantic_id, status, message, detected_evidence, '
                  'correction), improvement_suggestions (array of strings), grounding_note '
                  '(string). Do not invent content.\n\n' + raw[:24000])
        try:
            repaired = await call_ollama(request.base_model, repair, json_mode=True)
        except Exception as exc:
            logger.exception("Ollama JSON repair request failed model=%s", request.base_model)
            raise HTTPException(status_code=503, detail=f'Local LLM repair unavailable: {exc}') from exc
        try:
            payload = _validation_payload(repaired)
        except ValueError as exc:
            logger.exception("Qwen semantic JSON invalid after repair model=%s response_chars=%s",
                             request.base_model, len(repaired))
            raise HTTPException(status_code=502,
                detail=f'Semantic validation JSON invalid after one repair: {exc}') from exc
    returned={str(x.get('semantic_id')):x for x in (payload.get('semantic_results') or []) if isinstance(x,dict) and x.get('semantic_id')}
    semantic=[]
    for req in explicit:
        item=returned.get(req['semantic_id'],{})
        sev=req.get('final_severity') or 'Mandatory'
        st=str(item.get('status') or 'NOT VERIFIED').upper().replace('_',' ')
        if st not in {'PASS','FAIL','WARNING','NOT VERIFIED'}: st='NOT VERIFIED'
        if st=='FAIL' and sev!='Mandatory': st='WARNING'
        semantic.append({'category':'Semantic Compliance','semantic_id':req['semantic_id'],'name':req['requirement'],'requirement':req['requirement'],'severity':sev,'status':st,'message':str(item.get('message') or ('Requirement satisfied.' if st=='PASS' else 'The requirement could not be fully verified.')).strip(),'detected':str(item.get('detected_evidence') or ('Satisfied' if st=='PASS' else 'Insufficient evidence')).strip(),'correction':str(item.get('correction') or '').strip(),'source_page':item.get('source_page') or req.get('source_page'),'source_pages':req.get('source_pages') or ([req.get('source_page')] if req.get('source_page') else []),'source_section':str(item.get('source_section') or req.get('source_section') or '').strip(),'source_text':str(item.get('source_evidence') or req.get('evidence') or '').strip()[:800],'confidence':item.get('confidence'),'source_type':req.get('source_type') or ('Manual Semantic Rule' if req.get('manual') else 'Guideline-derived'),'affected_pages':[],'occurrences':[]})
    suggestions=[str(x).strip() for x in (payload.get('improvement_suggestions') or []) if str(x).strip()]
    grounding=str(payload.get('grounding_note') or '').strip()
    overall='PARTIAL' if not semantic else 'FAIL' if any(r['severity']=='Mandatory' and r['status']=='FAIL' for r in semantic) else ('PARTIAL' if any(r['status']=='NOT VERIFIED' for r in semantic) else 'PASS')
    comp=[f'- Status: {overall}']; corrections=[]
    for r in semantic:
        src=f"Page {r['source_page']}" if r.get('source_page') else 'approved guideline'
        comp.append(f"- **{r['status']} - {r['name']}**: {r['message']} [{src}]")
        if r['severity']=='Mandatory' and r['status']=='FAIL' and r.get('correction'): corrections.append(f"- {r['correction']} [{src}]")
    if not corrections: corrections=['- No guideline-supported mandatory semantic correction was reported.']
    if not suggestions: suggestions=['No additional improvement suggestion was generated.']
    response='## COMPLIANCE ASSESSMENT\n'+'\n'.join(comp)+'\n\n## REQUIRED CORRECTIONS\n'+'\n'.join(corrections)+'\n\n## IMPROVEMENT SUGGESTIONS\n'+'\n'.join('- '+x for x in suggestions)+'\n\n## GROUNDING NOTE\n- '+(grounding or 'Retrieved context was used for the rule-by-rule semantic review.')
    return {'status':'success','run_id':'RAG-'+uuid.uuid4().hex[:10].upper(),'collection':collection_name,'model':request.base_model,'retrieved_sources':hits,'response':response,'semantic_results':semantic,'semantic_overall_status':overall}

# -----------------------------
# Explicit formatting profile extraction
# -----------------------------
class FormatProfileRequest(BaseModel):
    department: str
    program: str
    submission_type: str
    template_version: str
    base_model: str = DEFAULT_LLM_MODEL
    limit: int = 10


def _normalize_rule_profile(profile: dict) -> dict:
    """Keep only safe, explicit rule shapes. Unknown rules remain semantic."""
    if not isinstance(profile, dict):
        return {"deterministic_rules": [], "semantic_requirements": [], "cover_page": {}}
    out = {
        "deterministic_rules": [],
        "semantic_requirements": [],
        "cover_page": profile.get("cover_page") if isinstance(profile.get("cover_page"), dict) else {},
    }
    allowed = {
        "page_size", "body_font", "body_font_size_pt", "margin_left_mm",
        "margin_right_mm", "margin_top_mm", "margin_bottom_mm", "line_spacing",
        "min_pages", "max_pages", "required_section", "section_max_words",
        "section_min_words", "page_numbering_required", "heading_alignment",
        "heading_font_size_pt", "heading_bold_required", "heading_case",
        "body_alignment", "body_regular_required", "figure_caption_position",
        "table_caption_position", "section_forbidden_element"
    }
    for r in profile.get("deterministic_rules", []) or []:
        if not isinstance(r, dict):
            continue
        rr = {
            "type": str(r.get("type", "")).strip(),
            "target": str(r.get("target", "")).strip(),
            "value": r.get("value"),
            "mandatory": bool(r.get("mandatory", True)),
            "source_page": r.get("source_page"),
            "source_section": str(r.get("source_section", "")).strip(),
            "evidence": str(r.get("evidence", "")).strip()[:500],
        }
        if rr["type"] in allowed:
            out["deterministic_rules"].append(rr)
        else:
            out["semantic_requirements"].append({
                "requirement": rr["evidence"] or f"{rr['type']} {rr['target']} {rr['value']}".strip(),
                "mandatory": rr["mandatory"],
                "source_page": rr["source_page"],
                "source_section": rr["source_section"],
            })
    for r in profile.get("semantic_requirements", []) or []:
        if isinstance(r, dict) and str(r.get("requirement", "")).strip():
            out["semantic_requirements"].append({
                "requirement": str(r.get("requirement")).strip()[:1000],
                "mandatory": bool(r.get("mandatory", True)),
                "source_page": r.get("source_page"),
                "source_section": str(r.get("source_section", "")).strip(),
            })
    return out


def _extract_cover_candidate(doc: fitz.Document) -> dict:
    """Infer a cover/title-page sample even when the page is not explicitly labelled.

    The guideline is expected to contain a standard textual template. We first look
    for explicit labels, then for a strong constellation of cover-page anchors.
    """
    candidate = None
    reason = ""
    explicit_markers = [
        "sample cover page", "sample title page", "cover page format",
        "title page format", "specimen title page"
    ]
    anchor_patterns = [
        r"prepared by", r"student id", r"supervised by",
        r"department of", r"university", r"month[, ]+year",
        r"partial fulfillment", r"title of the thesis"
    ]
    for idx, page in enumerate(doc):
        txt = (page.get_text("text") or "").lower()
        if any(k in txt for k in explicit_markers):
            candidate = idx
            reason = "explicit_label"
            break
        hits = sum(bool(re.search(p, txt, re.I)) for p in anchor_patterns)
        if hits >= 5:
            candidate = idx
            reason = f"template_pattern:{hits}_anchors"
            break
    if candidate is None:
        return {"detected": False, "detection_reason": "no_cover_pattern"}
    page = doc[candidate]
    rect = page.rect
    blocks=[]
    for b in page.get_text("dict").get("blocks", []):
        for line in b.get("lines", []):
            spans=line.get("spans", [])
            text=" ".join((sp.get("text") or "").strip() for sp in spans).strip()
            if not text:
                continue
            x0,y0,x1,y1=line.get("bbox", (0,0,0,0))
            blocks.append({
                "text": text[:240],
                "x0": round(x0/rect.width,4), "y0": round(y0/rect.height,4),
                "x1": round(x1/rect.width,4), "y1": round(y1/rect.height,4),
                "font_size": round(max([float(sp.get("size",0)) for sp in spans] or [0]),1),
                "bold": any("bold" in str(sp.get("font","")).lower() for sp in spans),
            })
    page_text=(page.get_text("text") or "").lower()
    # A template embedded in setup instructions is useful for required cover fields
    # and ordering, but its page coordinates are not a true cover-page geometry sample.
    inline_markers=("ms word setup", "font settings", "custom margins", "page break")
    is_inline_template=any(m in page_text for m in inline_markers)
    geometry_mode="structure_only" if is_inline_template else "visual_layout"
    return {
        "detected": True, "guideline_page": candidate+1, "blocks": blocks[:120],
        "detection_reason": reason, "geometry_mode": geometry_mode
    }

def _parse_json_object(text: str):
    import json, ast
    raw=(text or '').strip()
    raw=re.sub(r'<think>.*?</think>', '', raw, flags=re.I|re.S).strip()
    raw=re.sub(r'^```(?:json)?\s*','',raw,flags=re.I)
    raw=re.sub(r'\s*```$','',raw)
    decoder = json.JSONDecoder()
    try:
        whole = json.loads(raw)
    except ValueError:
        whole = None
    else:
        return whole if isinstance(whole, dict) else None
    # Do not reinterpret a valid top-level array as its first nested object.
    if raw.startswith('['):
        return None
    for match in re.finditer(r'\{', raw):
        try:
            obj, _ = decoder.raw_decode(raw[match.start():])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    candidates=[raw]
    first=raw.find('{'); last=raw.rfind('}')
    if first >= 0 and last > first:
        candidates.append(raw[first:last+1])
    for cand in candidates:
        for variant in [cand, re.sub(r',\s*([}\]])', r'\1', cand)]:
            try:
                obj=json.loads(variant)
                if isinstance(obj,dict): return obj
            except Exception:
                pass
            try:
                obj=ast.literal_eval(variant)
                if isinstance(obj,dict): return obj
            except Exception:
                pass
    return None

def _rule_key(rule: dict):
    return (str(rule.get('type','')).strip().lower(), str(rule.get('target','')).strip().lower())


def _add_rule_unique(rules: list[dict], rule: dict):
    key=_rule_key(rule)
    if not key[0]:
        return
    for existing in rules:
        if _rule_key(existing)==key:
            return
    rules.append(rule)


def _standard_text_rule_fallback(page_items: list[dict]) -> dict:
    """Rule-oriented parser for standard textual university guidelines.

    Parses line/bullet style requirements and preserves page-level evidence. It is
    deliberately conservative: only explicit wording and obvious template headings
    are turned into rules.
    """
    rules=[]; semantics=[]
    heading_names=set()
    def add(rule): _add_rule_unique(rules, rule)
    def clean(x): return re.sub(r'\s+',' ',x or '').strip()
    def mm(v,u):
        v=float(v); u=(u or '').lower()
        return round(v*25.4 if u.startswith('in') else v,2)
    for item in page_items:
        page=int(item.get('page') or 0) or None
        raw=item.get('text','') or ''
        lines=[clean(x) for x in raw.splitlines() if clean(x)]
        text=' '.join(lines)
        # page size
        for m in re.finditer(r'(?i)(?:page|paper)\s*size\s*[:\-]?[^\n.;]{0,80}?\b(A4|letter)\b', text):
            add({'type':'page_size','target':'document','value':m.group(1).upper(),'mandatory':True,'source_page':page,'source_section':'Page Setup','evidence':m.group(0)[:500]})
        # margins, supports "Left: 1.5 in" and "Right, Top, Bottom: 1.0 in"
        for ln in lines:
            ml=re.search(r'(?i)\bleft\s*:\s*(\d+(?:\.\d+)?)\s*(in(?:ch(?:es)?)?|mm)\b',ln)
            if ml: add({'type':'margin_left_mm','target':'document','value':mm(ml.group(1),ml.group(2)),'mandatory':True,'source_page':page,'source_section':'Margins','evidence':ln[:500]})
            multi=re.search(r'(?i)\b(right|top|bottom)(?:\s*,\s*(right|top|bottom))*[^:]*:\s*(\d+(?:\.\d+)?)\s*(in(?:ch(?:es)?)?|mm)\b',ln)
            if multi:
                value=mm(multi.group(3),multi.group(4))
                names=[x for x in re.findall(r'(?i)\b(left|right|top|bottom)\b',ln.split(':',1)[0])]
                for side in names: add({'type':f'margin_{side.lower()}_mm','target':'document','value':value,'mandatory':True,'source_page':page,'source_section':'Margins','evidence':ln[:500]})
            for side in ['left','right','top','bottom']:
                m=re.search(rf'(?i)\b{side}\s+margin\b[^.;]{{0,50}}?(\d+(?:\.\d+)?)\s*(in(?:ch(?:es)?)?|mm)\b',ln)
                if m: add({'type':f'margin_{side}_mm','target':'document','value':mm(m.group(1),m.group(2)),'mandatory':True,'source_page':page,'source_section':'Margins','evidence':ln[:500]})
        # typography bullets
        for ln in lines:
            m=re.search(r'(?i)\b(chapter\s+title|heading\s*1|heading\s*2|body\s+text)(?:\s*\([^)]*\))?\s*:\s*(.+)$',ln)
            if not m: continue
            label=m.group(1).strip(); spec=m.group(2).strip(); low=spec.lower()
            target={'chapter title':'Chapter Title','heading 1':'Heading 1','heading 2':'Heading 2','body text':'body_text'}[label.lower()]
            size=re.search(r'(\d+(?:\.\d+)?)\s*(?:pt|point)',spec,re.I)
            font=re.search(r'(?i)\b(Times\s+New\s+Roman|Arial|Calibri|Cambria)\b',spec)
            if label.lower()=='body text':
                if size: add({'type':'body_font_size_pt','target':'body_text','value':float(size.group(1)) if '.' in size.group(1) else int(size.group(1)),'mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                if font: add({'type':'body_font','target':'body_text','value':clean(font.group(1)),'mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                if 'justified' in low or 'justify' in low: add({'type':'body_alignment','target':'body_text','value':'justified','mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                if 'regular' in low: add({'type':'body_regular_required','target':'body_text','value':True,'mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                ls=re.search(r'(\d(?:\.\d+)?)\s*line\s*spacing',spec,re.I)
                if ls: add({'type':'line_spacing','target':'body_text','value':float(ls.group(1)),'mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
            else:
                if size: add({'type':'heading_font_size_pt','target':target,'value':float(size.group(1)) if '.' in size.group(1) else int(size.group(1)),'mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                if 'bold' in low: add({'type':'heading_bold_required','target':target,'value':True,'mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                if 'center' in low: add({'type':'heading_alignment','target':target,'value':'center','mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                elif re.search(r'\bleft\b',low): add({'type':'heading_alignment','target':target,'value':'left','mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                if 'all caps' in low: add({'type':'heading_case','target':target,'value':'ALL CAPS','mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
                elif 'title case' in low: add({'type':'heading_case','target':target,'value':'Title Case','mandatory':True,'source_page':page,'source_section':'Font Settings','evidence':ln[:500]})
        # abstract word range and content / exclusions
        if re.search(r'(?i)\babstract\b', text):
            m=re.search(r'(?i)abstract.{0,500}?(?:between\s+)?(\d+)\s*(?:[\u2013\u2014\u2212-]|to|and)\s*(\d+)\s+words?\b', text, re.S)
            if m:
                add({'type':'section_min_words','target':'Abstract','value':int(m.group(1)),'mandatory':True,'source_page':page,'source_section':'Abstract','evidence':m.group(0)[:500]})
                add({'type':'section_max_words','target':'Abstract','value':int(m.group(2)),'mandatory':True,'source_page':page,'source_section':'Abstract','evidence':m.group(0)[:500]})
            for phrase in ['background and motivation','core problem statement','methodology used','key experimental results','major conclusions']:
                if phrase in text.lower(): semantics.append({'requirement':f'Abstract must include {phrase}.','mandatory':True,'source_page':page,'source_section':'Abstract'})
            for elem in ['figures','tables','citations']:
                if re.search(rf'(?i)do\s+not\s+include[^.]*\b{elem}\b', text): add({'type':'section_forbidden_element','target':'Abstract','value':elem,'mandatory':True,'source_page':page,'source_section':'Abstract','evidence':f'Do not include {elem} in the abstract.'})
        # explicit template headings imply required document structure
        for ln in lines:
            if re.fullmatch(r'(?i)(ABSTRACT|ACKNOWLEDGMENTS?|TABLE OF CONTENTS|REFERENCES)',ln): heading_names.add(ln.title())
            if re.match(r'(?i)^CHAPTER\s+\d+\s*:\s*.+',ln): heading_names.add(re.sub(r'(?i)^CHAPTER\s+\d+\s*:\s*','',ln).strip().title())
        # figure/table caption placement instructions
        if re.search(r'(?i)(figure|image|diagram).{0,100}caption.{0,100}(below|under)', text): add({'type':'figure_caption_position','target':'Figure captions','value':'below','mandatory':True,'source_page':page,'source_section':'Figures','evidence':'Figure/image caption must be below the figure.'})
        if re.search(r'(?i)table.{0,100}caption.{0,100}(above|over|top)', text): add({'type':'table_caption_position','target':'Table captions','value':'above','mandatory':True,'source_page':page,'source_section':'Tables','evidence':'Table caption must be above the table.'})
        # semantic instructional statements
        semantic_patterns=[
            ('methodology', r'(?i)methodology[^.]{0,220}(?:describe|explain|include)[^.]+'),
            ('literature review', r'(?i)(?:literature review|related works)[^.]{0,220}(?:review|discuss|highlight)[^.]+'),
            ('research gap', r'(?i)research gap[^.]{0,220}(?:explain|summarize)[^.]+'),
            ('discussion', r'(?i)discuss[^.]{0,220}(?:implications|findings|results)[^.]+'),
            ('conclusion', r'(?i)conclusion[^.]{0,220}(?:summarize|contributions)[^.]+'),
        ]
        for _,pat in semantic_patterns:
            for m in re.finditer(pat,text):
                req=clean(m.group(0))[:900]
                if req and not any(x.get('requirement')==req for x in semantics): semantics.append({'requirement':req,'mandatory':True,'source_page':page,'source_section':''})
    for sec in sorted(heading_names):
        add({'type':'required_section','target':sec,'value':True,'mandatory':True,'source_page':1,'source_section':'Template Structure','evidence':f'The approved template contains a required {sec} section.'})
    return {'deterministic_rules':rules,'semantic_requirements':_dedupe_semantic_rules(semantics)}

def _merge_profiles(primary: dict, fallback: dict) -> dict:
    merged=_normalize_rule_profile(primary or {})
    fb=_normalize_rule_profile(fallback or {})
    for r in fb.get('deterministic_rules',[]):
        _add_rule_unique(merged['deterministic_rules'],r)
    merged['semantic_requirements']=_dedupe_semantic_rules((merged.get('semantic_requirements') or [])+(fb.get('semantic_requirements') or []))
    return merged



def _program_guideline_consistency(program: str, page_items: list[dict]) -> dict:
    text=' '.join(x.get('text','') for x in page_items).lower()
    program_low=(program or '').lower()
    detected=[]
    patterns=[
        ('Bachelor of Science', r'bachelor\s+of\s+science'),
        ('Master of Science', r'master\s+of\s+science'),
        ('M.Sc.', r'\bm\.?\s*sc\.?\b'),
        ('B.Sc.', r'\bb\.?\s*sc\.?\b'),
    ]
    for label,pat in patterns:
        if re.search(pat,text,re.I): detected.append(label)
    mismatch=False; note=''
    if ('m.sc' in program_low or 'master' in program_low) and any(x.startswith('Bachelor') or x.startswith('B.Sc') for x in detected):
        mismatch=True; note='Configured program appears to be a master-level program, while the guideline text contains a bachelor-level degree statement.'
    if ('b.sc' in program_low or 'bachelor' in program_low) and any(x.startswith('Master') or x.startswith('M.Sc') for x in detected):
        mismatch=True; note='Configured program appears to be a bachelor-level program, while the guideline text contains a master-level degree statement.'
    return {'mismatch':mismatch,'detected_degree_markers':detected,'note':note}


def _coverage_audit(page_items: list[dict], profile: dict) -> dict:
    text=' '.join(x.get('text','') for x in page_items).lower()
    types={r.get('type') for r in profile.get('deterministic_rules',[])}
    checks=[
        ('margins', 'margin' in text, {'margin_left_mm','margin_right_mm','margin_top_mm','margin_bottom_mm'} & types),
        ('line spacing', 'line spacing' in text, {'line_spacing'} & types),
        ('chapter/heading styles', ('heading 1' in text or 'chapter title' in text), {'heading_font_size_pt','heading_alignment','heading_bold_required'} & types),
        ('abstract word limit', ('abstract' in text and 'word' in text), {'section_min_words','section_max_words'} & types),
        ('figure caption', ('figure' in text and 'caption' in text), {'figure_caption_position'} & types),
        ('table caption', ('table' in text and 'caption' in text), {'table_caption_position'} & types),
    ]
    missing=[name for name,present,found in checks if present and not found]
    return {'missing_categories':missing,'coverage_ok':not missing,'checked_categories':[name for name,present,_ in checks if present]}
def _guideline_payload(answer):
    obj = _parse_json_object(answer)
    if not isinstance(obj, dict):
        raise ValueError("Guideline response must be a JSON object")
    for key in ('deterministic_rules', 'semantic_requirements'):
        if not isinstance(obj.get(key), list) or any(not isinstance(r, dict) for r in obj[key]):
            raise ValueError(f"{key} must be an array of objects")
    return obj


async def _parse_guideline_chunks(page_items, model, prompt_template):
    combined = {'deterministic_rules': [], 'semantic_requirements': []}
    diagnostics = {'total': 0, 'succeeded': 0, 'errors': []}

    async def parse_piece(page, text, depth=0):
        prompt = prompt_template.replace('{full}', f'[GUIDELINE PAGE {page}]\n{text}')
        try:
            answer = await call_ollama(model, prompt, json_mode=True)
            try:
                parsed = _guideline_payload(answer)
            except ValueError:
                repaired = await call_ollama(model,
                    'Repair the supplied output into JSON with deterministic_rules and '
                    'semantic_requirements arrays. Preserve content, do not add requirements.\n' + answer,
                    json_mode=True)
                parsed = _guideline_payload(repaired)
            # Page provenance is supplied by the PDF, not guessed by the model.
            for key in ('deterministic_rules', 'semantic_requirements'):
                for rule in parsed[key]:
                    rule['source_page'] = page
            return parsed
        except (IncompleteOllamaResponse, ValueError):
            if depth >= 2 or len(text) < 800:
                raise
            # Retry shorter source pieces instead of repairing truncated JSON.
            midpoint = len(text) // 2
            cut = text.rfind(' ', 0, midpoint)
            if cut < midpoint // 2:
                cut = midpoint
            left = await parse_piece(page, text[:cut], depth + 1)
            right = await parse_piece(page, text[cut:], depth + 1)
            return _merge_profiles(left, right)

    for item in page_items:
        for piece in chunk_text(item['text'], chunk_size=3000, overlap=250):
            diagnostics['total'] += 1
            try:
                parsed = await parse_piece(item['page'], piece)
                combined = _merge_profiles(combined, parsed)
                diagnostics['succeeded'] += 1
            except Exception as exc:
                logger.exception('Guideline chunk failed page=%s chunk=%s', item['page'], diagnostics['total'])
                diagnostics['errors'].append(f"Page {item['page']}, chunk {diagnostics['total']}: {type(exc).__name__}: {exc}")
    return combined, diagnostics


@app.post('/guideline/profile')
async def extract_guideline_profile(
    department: str = Form(...),
    program: str = Form(...),
    submission_type: str = Form(...),
    template_version: str = Form(...),
    base_model: str = Form(DEFAULT_LLM_MODEL),
    pdf: UploadFile = File(...),
):
    """Parse the approved guideline PDF first, then derive validation rules from its explicit text."""
    if not pdf.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail='Only PDF guideline files are accepted.')
    temp_path=None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp:
            temp.write(await pdf.read()); temp_path=temp.name
        doc=fitz.open(temp_path)
        if doc.needs_pass:
            raise HTTPException(status_code=400, detail='Password-protected guideline PDFs are not supported.')
        page_text=[]
        page_items=[]
        for i,page in enumerate(doc):
            t=(page.get_text('text') or '').strip()
            if t:
                page_items.append({'page':i+1,'text':t})
                page_text.append(f'[GUIDELINE PAGE {i+1}]\n{t}')
        if not page_text:
            raise HTTPException(status_code=400, detail='No machine-readable text found in the guideline PDF.')
        cover=_extract_cover_candidate(doc)
        doc.close()
        prompt_template="""
You are a university guideline parser. Read the APPROVED GUIDELINE text and extract ONLY requirements that are explicitly stated.
Never invent defaults, common academic conventions, or inferred requirements. The PDF is the single source of truth.

Classify each explicit requirement into one of two groups:
1) deterministic_rules: measurable/checkable directly from a student PDF
2) semantic_requirements: content/quality requirements that require semantic review

Supported deterministic rule types:
- page_size (e.g. A4)
- body_font
- body_font_size_pt
- margin_left_mm, margin_right_mm, margin_top_mm, margin_bottom_mm
- line_spacing
- min_pages, max_pages
- required_section (target is section name, value=true)
- section_max_words, section_min_words (target is section name)
- page_numbering_required (value=true/false)
- heading_alignment (target can be Chapter Heading/Section Heading)
- heading_font_size_pt
- heading_bold_required
- heading_case (e.g. ALL CAPS, Title Case)
- body_alignment (e.g. justified)
- body_regular_required (value=true)
- figure_caption_position (value=above/below)
- table_caption_position (value=above/below)
- section_forbidden_element (target=section, value=figures/tables/citations)

Convert inches to mm using 25.4 mm per inch. Preserve the source page and source section when visible.
If wording says shall/must/required, mandatory=true. If it says should/recommended/preferred, mandatory=false.
Do not treat examples as requirements unless the guideline text clearly says they are required.
Split compound formatting statements into atomic rules. For example, "14 pt, Bold, Title Case, Left" must become four separate deterministic rules.
Template headings and prescribed report sections shown as part of the official template may be extracted as required_section rules.

Return JSON only, exactly in this schema:
{
  "deterministic_rules": [
    {"type":"...","target":"","value":null,"mandatory":true,"source_page":1,"source_section":"","evidence":"short faithful paraphrase"}
  ],
  "semantic_requirements": [
    {"requirement":"...","mandatory":true,"source_page":1,"source_section":""}
  ]
}

APPROVED GUIDELINE TEXT:
{full}
"""
        # One bounded piece at a time: no silent 60k-character truncation.
        try:
            raw, chunk_diagnostics = await asyncio.wait_for(
                _parse_guideline_chunks(page_items, base_model, prompt_template),
                timeout=540)
        except asyncio.TimeoutError:
            raw = {}
            chunk_diagnostics = {'total': 0, 'succeeded': 0,
                'errors': ['Guideline parsing exceeded 540 seconds; fallback rules require review.']}
        qwen_error = '; '.join(chunk_diagnostics['errors'])
        fallback=_standard_text_rule_fallback(page_items)
        profile=_merge_profiles(raw,fallback)
        consistency=_program_guideline_consistency(program,page_items)
        coverage=_coverage_audit(page_items,profile)
        profile['cover_page']=cover
        profile['consistency_check']=consistency
        profile['coverage_audit']=coverage
        profile['source_file']=pdf.filename
        profile['model']=base_model
        profile['department']=department
        profile['program']=program
        profile['submission_type']=submission_type
        profile['template_version']=template_version
        profile['parser_diagnostics']={
            'qwen_json_parsed': chunk_diagnostics['total'] > 0 and chunk_diagnostics['succeeded'] == chunk_diagnostics['total'],
            'qwen_chunks_total': chunk_diagnostics['total'],
            'qwen_chunks_succeeded': chunk_diagnostics['succeeded'],
            'qwen_partial': bool(chunk_diagnostics['succeeded']) and bool(chunk_diagnostics['errors']),
            'qwen_error': qwen_error,
            'fallback_rules_added': len(fallback.get('deterministic_rules',[])),
            'pages_with_text': len(page_items),
            'coverage_ok': coverage.get('coverage_ok',False),
            'missing_categories': coverage.get('missing_categories',[]),
            'program_guideline_mismatch': consistency.get('mismatch',False),
            'consistency_note': consistency.get('note',''),
            'activation_blocked': bool(consistency.get('mismatch')),
        }
        return {
            'status':'success',
            'profile':profile,
            'counts':{
                'deterministic_rules':len(profile['deterministic_rules']),
                'semantic_requirements':len(profile['semantic_requirements']),
                'cover_sample_detected':bool(cover.get('detected'))
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f'Guideline parsing failed: {e}')
    finally:
        if temp_path:
            try: os.unlink(temp_path)
            except OSError: pass


@app.post('/format/profile')
async def extract_format_profile(request: FormatProfileRequest):
    collection_name = make_collection_name(
        request.department, request.program, request.submission_type, request.template_version
    )
    queries=[
        'formatting requirements font font size typeface margins page size line spacing page numbering',
        'document format layout A4 margin Times New Roman Arial spacing formatting style',
        'thesis project report formatting instructions page setup typography'
    ]
    merged=[]; seen=set()
    for q in queries:
        try:
            for h in retrieve_chunks(collection_name,q,request.limit):
                key=(h.get('page'),h.get('text'))
                if key not in seen:
                    seen.add(key); merged.append(h)
        except Exception:
            pass
    merged=sorted(merged,key=lambda x:x.get('score',0),reverse=True)[:18]
    context='\n\n'.join([f"[SOURCE {i+1} | Page {h.get('page')}]\n{h.get('text','')}" for i,h in enumerate(merged)])
    prompt=f'''
You extract ONLY explicit formatting requirements from an approved university guideline.
Do not infer or invent rules. If a requirement is not explicitly stated, omit it.
Return JSON only, no markdown.

Allowed rule types:
- page_size (value example: "A4")
- body_font (value example: "Times New Roman")
- body_font_size_pt (numeric)
- margin_left_mm (numeric)
- margin_right_mm (numeric)
- margin_top_mm (numeric)
- margin_bottom_mm (numeric)
- line_spacing (numeric, e.g. 1.5)

Every rule must contain:
{{"type":"...","value":...,"mandatory":true,"source_page":<page number>,"evidence":"short evidence paraphrase"}}

If inches are used, convert them to millimetres using 1 inch = 25.4 mm.
Do not include heading font sizes as body_font_size_pt.
Do not include rules unless supported by the supplied context.

Return exactly:
{{"rules":[...]}}

APPROVED GUIDELINE CONTEXT:
{context}
'''
    try:
        answer=await call_ollama(request.base_model,prompt)
        profile=_parse_json_object(answer) or {'rules':[]}
    except Exception as e:
        raise HTTPException(status_code=503,detail=f'Formatting profile extraction failed: {e}')
    profile['collection']=collection_name
    profile['model']=request.base_model
    return {'status':'success','profile':profile,'retrieved_sources':merged}
