from pydantic import BaseModel, HttpUrl, model_validator
from typing import Optional, List, Dict, Any

class ScanRequest(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    scan_scope: Optional[str] = None
    parent_module: Optional[str] = None
    selected_module: Optional[str] = None

    @model_validator(mode='after')
    def check_module_params(self) -> 'ScanRequest':
        if self.scan_scope in ("module", "MODULE", "MODULE_WISE"):
            if not self.parent_module or not self.parent_module.strip() or not self.selected_module or not self.selected_module.strip():
                raise ValueError("parent_module and selected_module are mandatory when scan_scope is 'module'")
        return self

class ScanResponse(BaseModel):
    id: str
    url: str
    description: Optional[str] = None
    status: str
    progress_log: List[str]
    app_map: Dict[str, Any]
    test_cases: List[Dict[str, Any]]
    excel_path: Optional[str] = None
    created_at: str
    perf_data: Optional[Dict[str, Any]] = None

class ScanHistoryItem(BaseModel):
    id: str
    url: str
    description: Optional[str] = None
    status: str
    excel_path: Optional[str] = None
    created_at: str

class ScopeContext(BaseModel):
    scan_scope: str
    parent_module: Optional[str] = None
    selected_module: Optional[str] = None
    target_urls: List[str] = []
    allowed_features: List[str] = []
