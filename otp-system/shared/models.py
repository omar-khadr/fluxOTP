# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# Shared Data Models & API Contracts for OTP System
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# This module defines all data transfer objects (DTOs) and domain models used across
# microservices, ensuring consistency, type safety, and clear API contracts.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import json
import hashlib


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# ENUMS & CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────────────────────────

class MessageSourceType(str, Enum):
    """Types of OTP message sources."""
    EMAIL = "email"
    SMS = "sms"
    PUSH_NOTIFICATION = "push"
    WEBHOOK = "webhook"
    UNKNOWN = "unknown"


class ProcessingStatus(str, Enum):
    """Message processing status throughout the pipeline."""
    RECEIVED = "received"
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    INTELLIGENCE = "intelligence"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


class ExtractionConfidence(str, Enum):
    """Confidence levels for OTP extraction."""
    VERY_HIGH = "very_high"    # > 0.95
    HIGH = "high"               # 0.80 - 0.95
    MEDIUM = "medium"           # 0.60 - 0.80
    LOW = "low"                 # 0.40 - 0.60
    VERY_LOW = "very_low"       # < 0.40


class CircuitBreakerState(str, Enum):
    """Circuit breaker states for resilience."""
    CLOSED = "closed"           # Normal operation
    OPEN = "open"               # Failing, reject requests
    HALF_OPEN = "half_open"     # Testing recovery


class ProviderHealthStatus(str, Enum):
    """Provider health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNREACHABLE = "unreachable"


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# DOMAIN MODELS
# ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class RawMessage:
    """
    Represents a raw OTP message received from a provider (email, SMS, webhook, etc).
    This is the entry point into the system.
    """
    message_id: str              # Unique identifier for this message (UUID)
    source_type: MessageSourceType  # Where did this come from?
    source_provider: str         # Specific provider (e.g., "gmail-1", "twilio")
    subject: Optional[str] = None  # For email
    body: str = ""               # Full message text
    received_at: datetime = field(default_factory=datetime.utcnow)
    extracted_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def content_hash(self) -> str:
        """Generate SHA256 hash of message content for deduplication."""
        content = f"{self.subject or ''}{self.body}".encode('utf-8')
        return hashlib.sha256(content).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary, handling special types."""
        data = asdict(self)
        data['received_at'] = self.received_at.isoformat()
        data['source_type'] = self.source_type.value
        return data


@dataclass
class PreprocessedMessage:
    """
    Represents a message after preprocessing (normalization, cleaning, tokenization).
    """
    original_message_id: str
    cleaned_text: str           # Lowercased, no extra spaces, normalized
    tokens: List[str]           # Tokenized words/phrases
    extracted_fields: Dict[str, str]  # Numbers, emails, URLs extracted
    text_length: int
    processing_time_ms: float
    preprocessing_score: float  # 0-1, quality of text for extraction


@dataclass
class OTPExtraction:
    """
    Represents a single OTP code extracted from a message.
    Contains the code itself, confidence, and supporting evidence.
    """
    code: str                    # The OTP (e.g., "123456")
    code_type: str               # "6digit", "8alpha", "token", etc.
    confidence: float            # 0.0 - 1.0 confidence score
    confidence_level: ExtractionConfidence
    matched_pattern: str         # Regex pattern that matched
    context_before: str          # Text before the code
    context_after: str           # Text after the code
    extraction_method: str       # "regex", "nlp", "hybrid"
    validation_flags: Dict[str, bool] = field(default_factory=dict)
    
    def is_high_confidence(self) -> bool:
        """Check if extraction meets high confidence threshold."""
        return self.confidence >= 0.80


@dataclass
class ProcessedMessage:
    """
    Represents a fully processed message with extracted OTPs and metadata.
    This is produced by the Intelligence Engine and passed to the Processor.
    """
    message_id: str
    original_message_id: str
    source_type: MessageSourceType
    source_provider: str
    received_at: datetime
    
    # Extraction results
    extracted_otps: List[OTPExtraction] = field(default_factory=list)
    primary_otp: Optional[OTPExtraction] = None
    extraction_confidence: float = 0.0  # Average confidence
    
    # Processing metadata
    status: ProcessingStatus = ProcessingStatus.COMPLETED
    processing_start_time: datetime = field(default_factory=datetime.utcnow)
    processing_end_time: Optional[datetime] = None
    total_processing_time_ms: float = 0.0
    
    # Quality metrics
    is_duplicate: bool = False
    duplicate_of_message_id: Optional[str] = None
    
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data = {
            'message_id': self.message_id,
            'original_message_id': self.original_message_id,
            'source_type': self.source_type.value,
            'source_provider': self.source_provider,
            'received_at': self.received_at.isoformat(),
            'extracted_otps': [
                {
                    'code': otp.code,
                    'confidence': otp.confidence,
                    'confidence_level': otp.confidence_level.value,
                    'extraction_method': otp.extraction_method,
                } for otp in self.extracted_otps
            ],
            'primary_otp': self.primary_otp.code if self.primary_otp else None,
            'extraction_confidence': self.extraction_confidence,
            'status': self.status.value,
            'is_duplicate': self.is_duplicate,
            'total_processing_time_ms': self.total_processing_time_ms,
        }
        return data


@dataclass
class IntelligenceResult:
    """
    Result from the Intelligence Engine after NLP/Regex processing.
    """
    message_id: str
    raw_message: RawMessage
    preprocessed_message: PreprocessedMessage
    
    extractions: List[OTPExtraction] = field(default_factory=list)
    top_extraction: Optional[OTPExtraction] = None
    
    extraction_time_ms: float = 0.0
    errors: List[str] = field(default_factory=list)
    quality_score: float = 0.0  # 0-1 overall quality
    
    # Metadata for debugging and analysis
    nlp_entities_found: List[Dict[str, Any]] = field(default_factory=list)
    pattern_matches: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ProcessingResult:
    """
    Final result after full processing (dedup, validation, delivery).
    """
    message_id: str
    intelligence_result: IntelligenceResult
    
    # Final decision
    accepted: bool
    primary_otp: Optional[str] = None
    
    # Deduplication
    is_duplicate: bool = False
    duplicate_message_id: Optional[str] = None
    
    # Delivery
    delivered_to: List[str] = field(default_factory=list)  # ["kafka", "webhook"]
    delivery_status: Dict[str, str] = field(default_factory=dict)
    
    # Timing
    total_latency_ms: float = 0.0
    
    errors: List[str] = field(default_factory=list)
    
    def to_json(self) -> str:
        """Serialize to JSON for logging/delivery."""
        return json.dumps({
            'message_id': self.message_id,
            'accepted': self.accepted,
            'primary_otp': self.primary_otp,
            'is_duplicate': self.is_duplicate,
            'total_latency_ms': self.total_latency_ms,
            'errors': self.errors,
        })


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# PROVIDER HEALTH & MONITORING MODELS
# ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderMetrics:
    """
    Real-time metrics for a provider (collected by Resilience Manager).
    """
    provider_id: str
    source_type: MessageSourceType
    
    # Success/Failure tracking
    total_messages: int = 0
    successful_extractions: int = 0
    failed_messages: int = 0
    duplicate_messages: int = 0
    
    # Performance
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    
    # Health indicators
    error_rate: float = 0.0  # (failed / total)
    availability: float = 1.0  # 0-1
    circuit_breaker_state: CircuitBreakerState = CircuitBreakerState.CLOSED
    
    # Timestamps
    last_successful_message: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    last_check: datetime = field(default_factory=datetime.utcnow)
    
    consecutive_failures: int = 0
    consecutive_successes: int = 0


@dataclass
class HealthCheckReport:
    """
    Health check report for a provider/service.
    """
    provider_id: str
    status: ProviderHealthStatus
    is_alive: bool
    response_time_ms: float
    error_message: Optional[str] = None
    check_timestamp: datetime = field(default_factory=datetime.utcnow)
    metrics: Optional[ProviderMetrics] = None


@dataclass
class CircuitBreakerStatus:
    """
    Current state of a circuit breaker for a provider.
    """
    provider_id: str
    state: CircuitBreakerState
    failure_count: int = 0
    success_count: int = 0
    last_state_change: datetime = field(default_factory=datetime.utcnow)
    last_failure_time: Optional[datetime] = None
    next_retry_time: Optional[datetime] = None
    
    def should_attempt_request(self) -> bool:
        """Determine if we should attempt a request to this provider."""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        if self.state == CircuitBreakerState.HALF_OPEN:
            return True  # Try to recover
        return False  # OPEN state


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# API REQUEST/RESPONSE CONTRACTS
# ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class OTPIngestRequest:
    """
    HTTP API request to ingest an OTP message.
    """
    source_type: str  # "email", "sms", "webhook"
    source_provider: str
    subject: Optional[str] = None
    body: str = ""


@dataclass
class OTPIngestResponse:
    """
    HTTP response to OTP ingestion.
    """
    message_id: str
    status: str  # "accepted", "rejected", "error"
    processing_time_ms: float
    errors: List[str] = field(default_factory=list)


@dataclass
class HealthCheckResponse:
    """
    Health check endpoint response.
    """
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    services: Dict[str, str] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# RESILIENCE & FAILOVER MODELS
# ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class FailoverDecision:
    """
    Result of failover logic determining which provider to use next.
    """
    current_provider: str
    should_failover: bool
    recommended_provider: Optional[str] = None
    reason: str = ""
    confidence: float = 0.0
    alternative_providers: List[str] = field(default_factory=list)


@dataclass
class RetryPolicy:
    """
    Retry policy for transient failures.
    """
    max_attempts: int = 3
    initial_backoff_ms: int = 1000
    max_backoff_ms: int = 30000
    backoff_multiplier: float = 2.0
    jitter_enabled: bool = True
    jitter_factor: float = 0.1


@dataclass
class RetryContext:
    """
    Context for a retry operation.
    """
    attempt_number: int = 0
    last_error: Optional[str] = None
    last_error_timestamp: Optional[datetime] = None
    total_attempts: int = 0
    elapsed_time_ms: float = 0.0
