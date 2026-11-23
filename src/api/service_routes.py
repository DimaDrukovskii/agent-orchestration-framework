"""Authenticated internal manager workflow and delivery backlog."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from src.api.auth import verify_manager_key
from src.services.delivery import DeliveryOutbox
from src.services.service_requests import RequestConflict, ServiceRequests

router = APIRouter(dependencies=[Depends(verify_manager_key)])


class ManagerAction(BaseModel):
    manager: str = Field(min_length=1, max_length=100)

    @field_validator('manager')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Укажите менеджера')
        return value.strip()


class Completion(ManagerAction):
    result: str = Field(min_length=1, max_length=2000)

    @field_validator('result')
    @classmethod
    def nonblank_result(cls, value):
        if not value.strip():
            raise ValueError('Укажите результат обработки')
        return value.strip()


def workflow():
    try:
        return ServiceRequests()
    except ValueError as exc:
        raise HTTPException(503, detail=str(exc))


@router.get('/service/deliveries')
async def deliveries():
    return await DeliveryOutbox().report()


@router.get('/service/requests')
async def requests(status: Literal['open', 'new', 'in_progress', 'completed'] = 'open', offset: int = Query(0, ge=0)):
    return await workflow().list(status, offset)


@router.get('/service/requests/{call_id}')
async def request_detail(call_id: str):
    try:
        return await workflow().detail(call_id)
    except KeyError:
        raise HTTPException(404, 'Заявка не найдена')


async def _transition(call_id, manager, result=''):
    try:
        return await workflow().transition(call_id, manager, result)
    except KeyError:
        raise HTTPException(404, 'Заявка не найдена')
    except RequestConflict as exc:
        raise HTTPException(409, str(exc))


@router.post('/service/requests/{call_id}/claim')
async def claim(call_id: str, action: ManagerAction):
    return await _transition(call_id, action.manager)


@router.post('/service/requests/{call_id}/complete')
async def complete(call_id: str, action: Completion):
    return await _transition(call_id, action.manager, action.result)
