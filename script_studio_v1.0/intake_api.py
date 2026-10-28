"""HTTP boundary for the optional material enrichment workflow."""
import base64
import binascii
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from material_intake import IntakeService, read_workbook, LOCK, source_row
from pathlib import Path
import json
from feishu_writeback import WritebackError
from intake_batches import IntakeBatches


class WorkbookInput(BaseModel):
    filename: str = Field(max_length=255)
    content: str = Field(max_length=28000000)
    sheet: str = ''


class SourceInput(BaseModel):
    script: str = Field(min_length=1, max_length=50000)
    title: str = Field(default='', max_length=2000)
    product: str = Field(default='未知游戏', max_length=200)
    url: str = Field(default='', max_length=2000)
    duration: float | None = Field(default=None, gt=0, le=86400)
    rating: str = Field(default='', max_length=30)
    rank: str = Field(default='', max_length=30)
    notes: str = Field(default='', max_length=5000)
    mapping: str = Field(default='', max_length=5000)
    provenance: dict[str, Any] = Field(default_factory=dict)


class RevisionInput(BaseModel):
    revision: int
    model: str = ''
    reasoning_effort: str = ''


class AnalysisInput(RevisionInput):
    analysis: dict[str, Any]

class BatchInput(BaseModel):
    sources: list[SourceInput] = Field(min_length=1,max_length=500)
    model: str = ''
    reasoning_effort: str = ''

class BatchAction(BaseModel):
    action: str = 'analyze'


def build_intake_router(root, config, command, call_ai):
    router = APIRouter(prefix='/api/intake')
    service = IntakeService(root, config, command, call_ai)
    batches = IntakeBatches(service)

    def invoke(action, *args):
        try:
            return {'ok':True, 'job':action(*args)}
        except (ValueError, WritebackError) as error:
            raise HTTPException(status_code=409 if '其他页面更新' in str(error) else 422, detail=str(error))
        except (OSError, RuntimeError) as error:
            # Existing model error sanitization is reused; never serialize local config.
            raise HTTPException(status_code=502, detail=str(error).replace(config().get('model_service',{}).get('api_key') or '\x00','[密钥]'))

    @router.post('/files')
    def parse_file(body: WorkbookInput):
        try:
            content = base64.b64decode(body.content, validate=True)
            return {'ok':True, **read_workbook(content, body.filename, body.sheet)}
        except (ValueError, KeyError, binascii.Error, zipfile.BadZipFile, ET.ParseError, UnicodeDecodeError, IndexError):
            raise HTTPException(422, '表格无法读取，请确认是有效的.xlsx或.csv，包含“脚本原文”或“口播文案”列，且未超过大小限制')

    @router.get('/jobs')
    def jobs():
        return {'ok':True, 'jobs':service.list_jobs()}

    @router.get('/sources')
    def sources():
        path = Path(root) / 'data_cache' / 'rival_struct.json'
        cached = json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        result = []
        for index, record in enumerate(cached,1):
            source = source_row(record, '结构与风格库本地缓存', '已入库文案', index)
            if source:
                source['title'] = record.get('素材编号','') + ' · ' + source['title']
                result.append(source)
        return {'ok':True,'rows':result,'count':len(result)}

    @router.post('/jobs')
    def create(body: SourceInput):
        with LOCK:
            return invoke(service.create, body.model_dump())

    @router.get('/jobs/{job_id}')
    def get(job_id: str):
        return invoke(service.load, job_id)

    @router.post('/jobs/{job_id}/analyze')
    def analyze(job_id: str, body: RevisionInput):
        return invoke(service.analyze, job_id, body.revision, body.model, body.reasoning_effort)

    @router.put('/jobs/{job_id}')
    def edit(job_id: str, body: AnalysisInput):
        return invoke(service.edit, job_id, body.revision, body.analysis)

    @router.post('/jobs/{job_id}/write-structure')
    def write_structure(job_id: str, body: RevisionInput):
        return invoke(service.write, job_id, body.revision, 'structure')

    @router.post('/jobs/{job_id}/write-details')
    def write_details(job_id: str, body: RevisionInput):
        return invoke(service.write, job_id, body.revision, 'details')

    @router.post('/jobs/{job_id}/write-all')
    def write_all(job_id: str, body: RevisionInput):
        return invoke(service.write_all,job_id,body.revision)

    @router.get('/batches')
    def list_batches():
        return {'ok':True,'batches':batches.list()}

    @router.post('/batches')
    def create_batch(body: BatchInput):
        return invoke(batches.create,[s.model_dump() for s in body.sources],body.model,body.reasoning_effort)

    @router.get('/batches/{batch_id}')
    def get_batch(batch_id: str):
        return invoke(batches.get,batch_id)

    @router.post('/batches/{batch_id}/run')
    def run_batch(batch_id: str, body: BatchAction):
        return invoke(batches.start,batch_id,body.action)

    @router.post('/batches/{batch_id}/stop')
    def stop_batch(batch_id: str):
        return invoke(batches.stop,batch_id)

    return router
