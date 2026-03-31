from modules.guardrails.content_safety_decorator import with_content_safety

GUARDRAILS_CONFIG = {'check_credentials_output': True,
 'check_jailbreak': True,
 'check_output': True,
 'check_pii_input': False,
 'check_toxic_code_output': True,
 'check_toxicity': True,
 'content_safety_enabled': True,
 'content_safety_severity_threshold': 3,
 'runtime_enabled': True,
 'sanitize_pii': False}


import os
import logging
import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, ValidationError, constr
from dotenv import load_dotenv
import requests
import msal
import pandas as pd
import numpy as np
import openai

# Load environment variables
load_dotenv()

# -------------------- Configuration --------------------

class Config:
    """Configuration management for API keys and endpoints."""
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    SHAREPOINT_CLIENT_ID: str = os.getenv("SHAREPOINT_CLIENT_ID", "")
    SHAREPOINT_CLIENT_SECRET: str = os.getenv("SHAREPOINT_CLIENT_SECRET", "")
    SHAREPOINT_TENANT_ID: str = os.getenv("SHAREPOINT_TENANT_ID", "")
    SHAREPOINT_RESOURCE: str = os.getenv("SHAREPOINT_RESOURCE", "https://graph.microsoft.com")
    AUDIT_LOG_PATH: str = os.getenv("AUDIT_LOG_PATH", "audit.log")
    MAX_TEXT_LENGTH: int = 50000

    @classmethod
    def validate(cls):
        missing = []
        if not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY")
        if not cls.SHAREPOINT_CLIENT_ID:
            missing.append("SHAREPOINT_CLIENT_ID")
        if not cls.SHAREPOINT_CLIENT_SECRET:
            missing.append("SHAREPOINT_CLIENT_SECRET")
        if not cls.SHAREPOINT_TENANT_ID:
            missing.append("SHAREPOINT_TENANT_ID")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

Config.validate()

# -------------------- Logging Configuration --------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(Config.AUDIT_LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ShiftAllowanceAgent")

# -------------------- Pydantic Models --------------------

class ShiftAllowanceRequest(BaseModel):
    sharepoint_url: constr(strip_whitespace=True, min_length=10, max_length=2048)
    excel_file_path: constr(strip_whitespace=True, min_length=5, max_length=1024)

    @field_validator("sharepoint_url")
    @classmethod
    def validate_sharepoint_url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("SharePoint URL must start with 'https://'")
        if "sharepoint.com" not in v:
            raise ValueError("SharePoint URL must be a valid SharePoint address.")
        return v.strip()

    @field_validator("excel_file_path")
    @classmethod
    def validate_excel_file_path(cls, v: str) -> str:
        if not v.lower().endswith(".xlsx"):
            raise ValueError("Excel file path must end with '.xlsx'")
        return v.strip()

class ShiftAllowanceResponse(BaseModel):
    success: bool
    result: Optional[Any] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    fixing_tips: Optional[str] = None

# -------------------- Base Service Classes --------------------

class BaseService:
    """Base class for services with logging."""
    def __init__(self):
        self.logger = logger

class PolicyEngine:
    """Base class for business rules engine."""
    pass

# -------------------- Supporting Classes --------------------

class InputHandler(BaseService):
    """Receives and validates user input."""
    def validate(self, data: Dict[str, Any]) -> Tuple[Optional[ShiftAllowanceRequest], Optional[Dict[str, Any]]]:
        try:
            req = ShiftAllowanceRequest(**data)
            return req, None
        except ValidationError as ve:
            errors = ve.errors()
            error_messages = "; ".join([f"{e['loc'][0]}: {e['msg']}" for e in errors])
            self.logger.error(f"Input validation failed: {error_messages}")
            return None, {
                "success": False,
                "error_type": "INPUT_VALIDATION_ERROR",
                "error_message": error_messages,
                "fixing_tips": "Ensure SharePoint URL is correct and Excel file path ends with '.xlsx'."
            }

class AuthenticationService(BaseService):
    """Manages OAuth2 authentication with SharePoint."""
    def __init__(self):
        super().__init__()
        self.token_cache = None

    def authenticate_user(self) -> Optional[str]:
        try:
            authority = f"https://login.microsoftonline.com/{Config.SHAREPOINT_TENANT_ID}"
            app = msal.ConfidentialClientApplication(
                Config.SHAREPOINT_CLIENT_ID,
                authority=authority,
                client_credential=Config.SHAREPOINT_CLIENT_SECRET
            )
            scopes = [f"{Config.SHAREPOINT_RESOURCE}/.default"]
            result = app.acquire_token_silent(scopes, account=None)
            if not result:
                result = app.acquire_token_for_client(scopes=scopes)
            if "access_token" in result:
                self.logger.info("SharePoint OAuth2 authentication successful.")
                return result["access_token"]
            else:
                self.logger.error(f"SharePoint OAuth2 authentication failed: {result.get('error_description')}")
                return None
        except Exception as e:
            self.logger.error(f"Authentication exception: {str(e)}")
            return None

class SharePointConnector(BaseService):
    """Securely retrieves Excel files from SharePoint."""
    def __init__(self, auth_service: AuthenticationService):
        super().__init__()
        self.auth_service = auth_service

    def retrieve_excel_file(self, sharepoint_url: str, excel_file_path: str) -> Tuple[Optional[bytes], Optional[Dict[str, Any]]]:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                access_token = self.auth_service.authenticate_user()
                if not access_token:
                    return None, {
                        "success": False,
                        "error_type": "AUTHENTICATION_ERROR",
                        "error_message": "Failed to authenticate with SharePoint.",
                        "fixing_tips": "Check SharePoint credentials and permissions."
                    }
                headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/octet-stream"
                }
                # Construct the download URL for SharePoint REST API
                # Example: https://company.sharepoint.com/sites/hr/_api/web/GetFileByServerRelativeUrl('/sites/hr/shift_data.xlsx')/$value
                if "/sites/" in sharepoint_url:
                    site_path = sharepoint_url.split("/sites/")[1]
                    base_url = sharepoint_url.split("/sites/")[0]
                    api_url = f"{base_url}/sites/{site_path}/_api/web/GetFileByServerRelativeUrl('{excel_file_path}')/$value"
                else:
                    api_url = f"{sharepoint_url}/_api/web/GetFileByServerRelativeUrl('{excel_file_path}')/$value"
                response = requests.get(api_url, headers=headers, timeout=20)
                if response.status_code == 200:
                    self.logger.info(f"Excel file retrieved from SharePoint: {excel_file_path}")
                    return response.content, None
                elif response.status_code == 404:
                    self.logger.error(f"Excel file not found: {excel_file_path}")
                    return None, {
                        "success": False,
                        "error_type": "FILE_NOT_FOUND",
                        "error_message": "Excel file not found on SharePoint.",
                        "fixing_tips": "Verify the file path and permissions."
                    }
                else:
                    self.logger.warning(f"SharePoint file retrieval failed (attempt {attempt}): {response.status_code} {response.text}")
            except Exception as e:
                self.logger.error(f"SharePoint file retrieval exception (attempt {attempt}): {str(e)}")
            if attempt < max_retries:
                asyncio.sleep(2 ** attempt)
        return None, {
            "success": False,
            "error_type": "FILE_NOT_FOUND",
            "error_message": "Failed to retrieve Excel file from SharePoint after multiple attempts.",
            "fixing_tips": "Check network connectivity, file path, and permissions."
        }

class ExcelParser(BaseService):
    """Parses Excel files (.xlsx) and extracts structured shift records."""
    def parse_excel(self, excel_file_content: bytes) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
        try:
            from io import BytesIO
            df = pd.read_excel(BytesIO(excel_file_content), engine="openpyxl")
            required_columns = ["EmployeeID", "ShiftDate", "ShiftType", "HoursWorked"]
            for col in required_columns:
                if col not in df.columns:
                    self.logger.error(f"Missing required column: {col}")
                    return None, {
                        "success": False,
                        "error_type": "INVALID_FORMAT",
                        "error_message": f"Missing required column: {col}",
                        "fixing_tips": f"Ensure the Excel file contains columns: {', '.join(required_columns)}."
                    }
            # Clean and transform fields as per mappings
            records = []
            for _, row in df.iterrows():
                try:
                    record = {
                        "employee_id": str(row["EmployeeID"]).strip().upper(),
                        "shift_date": pd.to_datetime(row["ShiftDate"]).strftime("%Y-%m-%d"),
                        "shift_type": str(row["ShiftType"]).strip(),
                        "hours_worked": float(row["HoursWorked"])
                    }
                    records.append(record)
                except Exception as e:
                    self.logger.warning(f"Row parsing error: {str(e)}")
            if not records:
                return None, {
                    "success": False,
                    "error_type": "INVALID_FORMAT",
                    "error_message": "No valid shift records found in Excel file.",
                    "fixing_tips": "Check the file content and formatting."
                }
            self.logger.info(f"Parsed {len(records)} shift records from Excel.")
            return records, None
        except Exception as e:
            self.logger.error(f"Excel parsing exception: {str(e)}")
            return None, {
                "success": False,
                "error_type": "INVALID_FORMAT",
                "error_message": "Failed to parse Excel file. Ensure it is a valid .xlsx file.",
                "fixing_tips": "Check file format and content. Only .xlsx files are supported."
            }

class BusinessRulesEngine(PolicyEngine, BaseService):
    """Enforces company policies, decision tables, and mappings for allowance calculation."""
    SHIFT_RATE_TABLE = {
        "Night": 25,
        "Evening": 20,
        "Day": 15
    }

    def apply_business_rules(self, shift_data: List[Dict[str, Any]]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
        processed = []
        for record in shift_data:
            try:
                shift_type = record.get("shift_type", "").capitalize()
                shift_rate = self.SHIFT_RATE_TABLE.get(shift_type)
                if shift_rate is None:
                    self.logger.warning(f"Unknown shift type: {shift_type}")
                    return None, {
                        "success": False,
                        "error_type": "INVALID_FORMAT",
                        "error_message": f"Unknown shift type: {shift_type}",
                        "fixing_tips": "Allowed shift types: Night, Evening, Day."
                    }
                record["shift_rate"] = shift_rate
                processed.append(record)
            except Exception as e:
                self.logger.error(f"Business rule application error: {str(e)}")
                return None, {
                    "success": False,
                    "error_type": "INVALID_FORMAT",
                    "error_message": "Failed to apply business rules.",
                    "fixing_tips": "Check shift data and types."
                }
        return processed, None

class AllowanceCalculator(BaseService):
    """Applies business rules and formulas to calculate shift allowances."""
    def calculate_allowance(self, shift_data: List[Dict[str, Any]]) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
        results = []
        try:
            for record in shift_data:
                try:
                    hours = float(record["hours_worked"])
                    rate = float(record["shift_rate"])
                    allowance = round(hours * rate, 2)
                    record["allowance_amount"] = allowance
                    results.append(record)
                except Exception as e:
                    self.logger.warning(f"Allowance calculation error for record: {str(e)}")
            if not results:
                return None, {
                    "success": False,
                    "error_type": "INVALID_FORMAT",
                    "error_message": "No valid allowance calculations could be performed.",
                    "fixing_tips": "Check shift data for valid hours and rates."
                }
            self.logger.info(f"Calculated allowances for {len(results)} records.")
            return results, None
        except Exception as e:
            self.logger.error(f"Allowance calculation exception: {str(e)}")
            return None, {
                "success": False,
                "error_type": "INVALID_FORMAT",
                "error_message": "Failed to calculate allowances.",
                "fixing_tips": "Check input data and business rules."
            }

class AuditLogger(BaseService):
    """Logs all access and calculation events for compliance and audit."""
    def log_event(self, event_type: str, details: Dict[str, Any]) -> None:
        try:
            log_entry = {
                "event_type": event_type,
                "details": details
            }
            self.logger.info(f"Audit log: {log_entry}")
        except Exception as e:
            self.logger.error(f"Audit logging failed: {str(e)}")

class OutputFormatter(BaseService):
    """Formats responses, masks/redacts PII, and applies response templates."""
    @with_content_safety(config=GUARDRAILS_CONFIG)
    def mask_pii(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Mask employee_id except last 2 chars
        for record in data:
            emp_id = record.get("employee_id", "")
            if len(emp_id) > 2:
                record["employee_id"] = "*" * (len(emp_id) - 2) + emp_id[-2:]
        return data

    def format_output(self, results: List[Dict[str, Any]], template_type: str = "default") -> str:
        try:
            masked = self.mask_pii(results)
            lines = []
            for rec in masked:
                line = (
                    f"Employee: {rec['employee_id']}, "
                    f"Date: {rec['shift_date']}, "
                    f"Shift: {rec['shift_type']}, "
                    f"Hours: {rec['hours_worked']}, "
                    f"Allowance: ${rec['allowance_amount']}"
                )
                lines.append(line)
            return "\n".join(lines)
        except Exception as e:
            self.logger.error(f"Output formatting error: {str(e)}")
            return "An error occurred while formatting the output."

class LLMOrchestrator(BaseService):
    """Manages LLM prompt construction, calls, and fallback logic."""
    def __init__(self):
        super().__init__()
        self.client = openai.AsyncOpenAI(api_key=Config.OPENAI_API_KEY)
        self.primary_model = "gpt-4o"
        self.fallback_model = "gpt-3.5-turbo"
        self.system_prompt = (
            "You are a professional finance assistant specializing in shift allowance calculations. "
            "Your role is to securely access Excel files from SharePoint, extract shift details, and calculate allowances according to company policy. "
            "Always validate data, explain your calculations, and ensure compliance with security and privacy standards."
        )

    @with_content_safety(config=GUARDRAILS_CONFIG)
    async def call_llm(self, prompt: str, model: Optional[str] = None) -> str:
        chosen_model = model or self.primary_model
        try:
            response = await self.client.chat.completions.create(
                model=chosen_model,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=2000
            )
            return response.choices[0].message.content
        except Exception as e:
            self.logger.error(f"LLM call failed on {chosen_model}: {str(e)}")
            if chosen_model != self.fallback_model:
                # Fallback to alternate model
                try:
                    response = await self.client.chat.completions.create(
                        model=self.fallback_model,
                        messages=[
                            {"role": "system", "content": self.system_prompt},
                            {"role": "user", "content": prompt}
                        ],
                        temperature=0.7,
                        max_tokens=2000
                    )
                    return response.choices[0].message.content
                except Exception as e2:
                    self.logger.error(f"LLM fallback call failed: {str(e2)}")
                    return "Unable to process your request at this time due to technical issues."
            else:
                return "Unable to process your request at this time due to technical issues."

# -------------------- Main Agent Class --------------------

class ShiftAllowanceAgent:
    """Main agent orchestrating the shift allowance calculation workflow."""

    def __init__(self):
        self.input_handler = InputHandler()
        self.auth_service = AuthenticationService()
        self.sp_connector = SharePointConnector(self.auth_service)
        self.excel_parser = ExcelParser()
        self.business_rules = BusinessRulesEngine()
        self.allowance_calculator = AllowanceCalculator()
        self.audit_logger = AuditLogger()
        self.output_formatter = OutputFormatter()
        self.llm_orchestrator = LLMOrchestrator()

    @with_content_safety(config=GUARDRAILS_CONFIG)
    async def process_request(self, user_input: Dict[str, Any]) -> Dict[str, Any]:
        self.audit_logger.log_event("REQUEST_RECEIVED", {"input": user_input})
        # Input validation
        req, error = self.input_handler.validate(user_input)
        if error:
            self.audit_logger.log_event("INPUT_VALIDATION_FAILED", error)
            return error

        # Retrieve Excel file from SharePoint
        file_content, error = self.sp_connector.retrieve_excel_file(
            req.sharepoint_url, req.excel_file_path
        )
        if error:
            self.audit_logger.log_event("FILE_RETRIEVAL_FAILED", error)
            return error

        # Parse Excel file
        shift_records, error = self.excel_parser.parse_excel(file_content)
        if error:
            self.audit_logger.log_event("EXCEL_PARSING_FAILED", error)
            return error

        # Apply business rules
        processed_records, error = self.business_rules.apply_business_rules(shift_records)
        if error:
            self.audit_logger.log_event("BUSINESS_RULES_FAILED", error)
            return error

        # Calculate allowance
        calculated, error = self.allowance_calculator.calculate_allowance(processed_records)
        if error:
            self.audit_logger.log_event("ALLOWANCE_CALCULATION_FAILED", error)
            return error

        # Format output
        output_text = self.output_formatter.format_output(calculated)
        self.audit_logger.log_event("ALLOWANCE_CALCULATION_SUCCESS", {"output": output_text})

        # LLM explanation (optional, for user-friendly summary)
        llm_prompt = (
            "Explain the following shift allowance calculation results in simple terms for an HR manager:\n"
            f"{output_text}"
        )
        llm_response = await self.llm_orchestrator.call_llm(llm_prompt)

        return {
            "success": True,
            "result": {
                "calculation": output_text,
                "explanation": llm_response
            }
        }

# -------------------- FastAPI App & Endpoints --------------------

app = FastAPI(
    title="Shift Allowance Calculation Agent",
    description="API for calculating shift allowances from SharePoint Excel files.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = ShiftAllowanceAgent()

@app.exception_handler(ValidationError)
@with_content_safety(config=GUARDRAILS_CONFIG)
async def validation_exception_handler(request: Request, exc: ValidationError):
    logger.error(f"JSON validation error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error_type": "JSON_VALIDATION_ERROR",
            "error_message": "Malformed JSON or invalid fields.",
            "fixing_tips": "Check your JSON formatting, ensure all required fields are present and properly quoted."
        }
    )

@app.exception_handler(Exception)
@with_content_safety(config=GUARDRAILS_CONFIG)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error_type": "INTERNAL_SERVER_ERROR",
            "error_message": str(exc),
            "fixing_tips": "Contact IT support if the issue persists."
        }
    )

@app.post("/calculate_shift_allowance", response_model=ShiftAllowanceResponse)
@with_content_safety(config=GUARDRAILS_CONFIG)
async def calculate_shift_allowance(request: Request):
    try:
        data = await request.json()
    except Exception as e:
        logger.error(f"Malformed JSON: {str(e)}")
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "error_type": "MALFORMED_JSON",
                "error_message": "Malformed JSON in request body.",
                "fixing_tips": "Ensure your JSON is properly formatted. Common issues: missing quotes, trailing commas, or unescaped characters."
            }
        )
    # Input size validation
    if any(isinstance(v, str) and len(v) > Config.MAX_TEXT_LENGTH for v in data.values()):
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={
                "success": False,
                "error_type": "INPUT_TOO_LARGE",
                "error_message": f"Input text exceeds {Config.MAX_TEXT_LENGTH} characters.",
                "fixing_tips": "Reduce the size of your input fields."
            }
        )
    # Orchestrate the workflow
    result = await agent.process_request(data)
    return JSONResponse(status_code=200 if result.get("success") else 400, content=result)

@app.get("/")
async def root():
    return {
        "success": True,
        "message": "Shift Allowance Calculation Agent is running. Use /calculate_shift_allowance endpoint."
    }

# -------------------- Main Execution Block --------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("agent:app", host="0.0.0.0", port=8000, reload=False)