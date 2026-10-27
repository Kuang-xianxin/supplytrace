from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

TOOLS = ('inventory', 'orders', 'shipments', 'supplier')

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class CheckRequest(Strict):
    sku: str = Field(pattern=r'^[A-Z0-9][A-Z0-9_-]{0,39}$')
    quantity: int = Field(strict=True, ge=1, le=100000)
    as_of: date
    promised_date: date
    question: str = Field(default='', max_length=1500)
    mode: Literal['rules', 'ai'] = 'rules'

    @model_validator(mode='after')
    def dates(self):
        if not 0 <= (self.promised_date-self.as_of).days <= 365:
            raise ValueError('promised_date must be within 365 days after as_of')
        return self

class ConflictReview(Strict):
    conflict_index: int = Field(strict=True, ge=0)
    expected_revision: int = Field(strict=True, ge=0)
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    disposition: Literal['unresolved', 'retain_both']
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=1500)

    @model_validator(mode='after')
    def meaningful_text(self):
        if not self.reviewer.strip() or not self.note.strip():
            raise ValueError('Reviewer and rationale must contain non-whitespace text')
        return self

class ToolCall(Strict):
    tool: Literal['inventory', 'orders', 'shipments', 'supplier']
    sku: str

class Plan(Strict):
    rationale: str = Field(min_length=20, max_length=600, description='Explain which evidence to inspect first and why. No placeholder text and no delivery verdict.')
    queries: list[ToolCall] = Field(min_length=4, max_length=4)

    def authorize(self, request: CheckRequest):
        if {q.tool for q in self.queries} != set(TOOLS) or any(q.sku != request.sku for q in self.queries):
            raise ValueError('Plan must read each required source once for the requested SKU only')

class Row(Strict):
    id: str = Field(min_length=1,max_length=80)
    sku: str
    updated_at: date

class Inventory(Row):
    on_hand: int = Field(strict=True,ge=0)
    damaged: int = Field(strict=True,ge=0)
    warehouse: str

    @model_validator(mode='after')
    def quantities(self):
        if self.damaged > self.on_hand: raise ValueError('damaged exceeds on_hand')
        return self

class Order(Row):
    quantity: int = Field(strict=True,gt=0)
    due_date: date
    status: Literal['open','shipped','cancelled']

class Shipment(Row):
    quantity: int = Field(strict=True,gt=0)
    eta: date
    status: Literal['confirmed','unconfirmed','received','cancelled']
    supplier_id: str

class Supplier(Row):
    supplier_id: str
    safety_stock: int = Field(strict=True,ge=0)
    handling_days: int = Field(strict=True,ge=0,le=30)
    buffer_days: int = Field(strict=True,ge=0,le=30)
    lead_days: int = Field(strict=True,ge=1,le=365)

ROW_MODELS = {'inventory':Inventory,'orders':Order,'shipments':Shipment,'supplier':Supplier}

class WorkflowError(Exception):
    def __init__(self,code: str,status: int=503,details=None):
        self.code,self.status,self.details=code,status,details
        super().__init__(code)
