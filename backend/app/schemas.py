from pydantic import BaseModel, HttpUrl, model_validator
from typing import Optional, List, Dict, Any

class ScanRequest(BaseModel):
    url: str
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None
    # The user's own AI key (Anthropic "sk-ant-..." or OpenRouter "sk-or-..."), used only for this
    # scan and never persisted. openrouter_api_key is the legacy field name, still accepted.
    api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    scan_scope: Optional[str] = None
    parent_module: Optional[str] = None
    selected_module: Optional[str] = None

    @model_validator(mode='after')
    def merge_legacy_key_field(self) -> 'ScanRequest':
        key = (self.api_key or self.openrouter_api_key or "").strip()
        self.api_key = key or None
        self.openrouter_api_key = None
        return self

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
