"""Recurring blind spot endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ...services.blind_spot_service import BlindSpotService
from ..deps import blind_spot_service
from ..schemas import BlindSpotDetailResponse, BlindSpotListResponse

router = APIRouter(prefix="/api/blind-spots", tags=["blind-spots"])


@router.get("", response_model=BlindSpotListResponse)
def list_blind_spots(
    service: BlindSpotService = Depends(blind_spot_service),
) -> BlindSpotListResponse:
    return BlindSpotListResponse(items=service.list_patterns())


@router.post("/recompute", response_model=BlindSpotListResponse)
def recompute_blind_spots(
    service: BlindSpotService = Depends(blind_spot_service),
) -> BlindSpotListResponse:
    """Re-derive every pattern from the stored analyses."""
    return BlindSpotListResponse(items=service.recompute())


@router.get("/{identifier}", response_model=BlindSpotDetailResponse)
def get_blind_spot(
    identifier: str, service: BlindSpotService = Depends(blind_spot_service)
) -> BlindSpotDetailResponse:
    payload = service.get_pattern(identifier)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Blind spot '{identifier}' not found."
        )
    return BlindSpotDetailResponse(**payload)
