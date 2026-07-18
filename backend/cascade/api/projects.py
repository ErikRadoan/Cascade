from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..repositories.db import get_db
from ..repositories.project_repository import ProjectRepository

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: str | None = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: str


@router.get("/", response_model=list[ProjectResponse])
async def list_projects(db: Session = Depends(get_db)) -> list[ProjectResponse]:
    return [ProjectResponse(**p.to_dict()) for p in ProjectRepository(db).list()]


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreateRequest, db: Session = Depends(get_db)) -> ProjectResponse:
    record = ProjectRepository(db).create(name=body.name, description=body.description)
    return ProjectResponse(**record.to_dict())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectResponse:
    record = ProjectRepository(db).get(project_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return ProjectResponse(**record.to_dict())


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: Session = Depends(get_db)) -> dict:
    deleted = ProjectRepository(db).delete(project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    return {"deleted": True, "id": project_id}